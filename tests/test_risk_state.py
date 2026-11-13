"""Tests for the shadow risk dial (src/macro_engine/risk_state.py), on synthetic stores."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine import risk_state as rs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import risk_dial_test as rd  # noqa: E402

END = "2026-11-11"
NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


def _days(n: int, end: str = END):
    return pd.bdate_range(end=end, periods=n)


def _store(path, closes, days, readings=None, reading_end="2026-11", vix=None, drop_regime=False):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sector_proxy_prices (ticker VARCHAR, date DATE, close DOUBLE)")
    con.execute("CREATE TABLE regime_scores (regime_id VARCHAR, date DATE, probability DOUBLE, valid BOOLEAN)")
    con.execute("CREATE TABLE shock_register (shock_id VARCHAR, date DATE, active BOOLEAN)")
    con.executemany(
        "INSERT INTO sector_proxy_prices VALUES ('SPY', ?, ?)", [(d.date(), float(c)) for d, c in zip(days, closes)]
    )
    if not drop_regime:
        if readings is None:
            readings = 0.3 + 0.01 * (np.arange(40) * 7 % 11)  # 40 monthly readings
        months = pd.period_range(end=reading_end, periods=len(readings), freq="M")
        rows = []
        for mth, val in zip(months, readings):
            d = mth.to_timestamp().date()
            rows += [("recession", d, float(val), True), ("stagflation", d, 0.0, True)]
        con.executemany("INSERT INTO regime_scores VALUES (?, ?, ?, ?)", rows)
    if vix is None:
        vix = [(days[-1].date(), True)]
    if vix:
        con.executemany("INSERT INTO shock_register VALUES ('volatility_shock', ?, ?)", vix)
    con.close()


def _flat(n=260, last=None):
    c = np.full(n, 100.0)
    if last is not None:
        c[-1] = last
    return c


def test_dial_full_when_close_above_mean(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=101.0), _days(260))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["dial"]["state"] == "full" and d["price_rule"]["below"] is False
    assert d["price_rule"]["closes_used"] == 200


def test_dial_half_when_close_below_mean(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["dial"]["state"] == "half" and d["price_rule"]["below"] is True
    assert d["price_rule"]["spy_close"] == 99.0
    assert d["price_rule"]["mean_200"] == pytest.approx((199 * 100.0 + 99.0) / 200)


def test_dial_boundary_equal_is_full(tmp_path):
    # last close exactly equal to the mean of the last 200: a flat series
    _store(tmp_path / "s.duckdb", _flat(), _days(260))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["price_rule"]["below"] is False and d["dial"]["state"] == "full"


def test_since_is_first_close_of_the_current_state(tmp_path):
    days = _days(260)
    closes = _flat()
    closes[-3:] = 90.0  # the last three closes are below the 200-day mean; the one before is not
    _store(tmp_path / "s.duckdb", closes, days)
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["dial"]["state"] == "half"
    assert d["dial"]["since"] == days[-3].date().isoformat()


def test_since_when_state_changed_long_ago(tmp_path):
    days = _days(300)
    # 100 flat, then a fall to 50 (below its mean), then a recovery back above it
    closes = np.concatenate([np.full(100, 100.0), np.full(100, 50.0), np.linspace(50.0, 400.0, 100)])
    _store(tmp_path / "s.duckdb", closes, days)
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    p = [rs.price_signal(closes, i) for i in range(199, 300)]
    changes = [i for i in range(1, len(p)) if p[i] != p[i - 1]]
    assert changes, "the synthetic path must change state"
    assert d["dial"]["state"] == ("half" if p[-1] == 1.0 else "full")
    assert d["dial"]["since"] == days[199 + changes[-1]].date().isoformat()
    assert d["dial"]["since"] != days[199].date().isoformat()


def test_asof_is_last_spy_close_and_not_degraded(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=101.0), _days(260))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["asof"] == END and d["inputs"] == {"spy_last_date": END, "regime_month": "2026-11"}
    assert d["degraded"] is False and d["reasons"] == []
    assert d["status"] == "shadow" and d["schema"] == "mri.risk_state.v1" and d["built_at"] == "2026-11-12T06:00:00Z"
    assert d["evidence"] == {"prereg": "PREREG_risk_dial_v1", "verdict": "MIXED", "n_months": 320}
    assert d["vix_alarm"]["active"] is True


def test_degraded_spy_stale(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260))
    d = rs.build_risk_state(tmp_path / "s.duckdb", datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc))  # 6 days after
    assert d["degraded"] is True and d["dial"]["state"] is None and d["dial"]["since"] is None
    assert d["price_rule"] is None and d["asof"] == END
    assert any(r.startswith("spy_stale") for r in d["reasons"])
    # 5 days is still fresh
    d5 = rs.build_risk_state(tmp_path / "s.duckdb", datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc))
    assert d5["dial"]["state"] == "half"


def test_degraded_fewer_than_200_closes(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(199), _days(199))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["degraded"] is True and d["dial"]["state"] is None and d["price_rule"] is None
    assert any(r.startswith("spy_short") for r in d["reasons"])


def test_degraded_no_regime_reading(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260), drop_regime=True)
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["degraded"] is True and d["macro_reading"] is None and d["inputs"]["regime_month"] is None
    assert d["dial"]["state"] == "half"  # the price part is unaffected
    assert any(r.startswith("regime_missing") for r in d["reasons"])


def test_degraded_fewer_than_36_readings_keeps_value_nulls_threshold(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260), readings=np.full(20, 0.5))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    m = d["macro_reading"]
    assert m["recession_plus_stagflation"] == 0.5 and m["top_third_threshold"] is None and m["in_top_third"] is None
    assert d["degraded"] is True and any(r.startswith("regime_short") for r in d["reasons"])


def test_degraded_no_vix_row(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260), vix=[(pd.Timestamp("2026-11-12").date(), True)])
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)  # the only row is after the last SPY close
    assert d["vix_alarm"]["active"] is None and d["degraded"] is True
    assert any(r.startswith("vix_alarm_missing") for r in d["reasons"])


def test_macro_reading_is_latest_valid_month_with_registered_threshold(tmp_path):
    readings = np.concatenate([np.full(39, 0.2), [0.9]])
    _store(tmp_path / "s.duckdb", _flat(last=101.0), _days(260), readings=readings)
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    m = d["macro_reading"]
    assert m["month"] == "2026-11" and m["recession_plus_stagflation"] == pytest.approx(0.9)
    assert m["top_third_threshold"] == pytest.approx(float(np.quantile(readings, 2.0 / 3.0)))
    assert m["in_top_third"] is True
    low = np.concatenate([np.full(39, 0.2), [0.1]])
    _store(tmp_path / "t.duckdb", _flat(last=101.0), _days(260), readings=low)
    assert rs.build_risk_state(tmp_path / "t.duckdb", NOW)["macro_reading"]["in_top_third"] is False


def test_latest_valid_reading_is_reported_when_later_months_are_absent(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=101.0), _days(260), reading_end="2026-10")
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["macro_reading"]["month"] == "2026-10"


def test_registered_definitions_have_one_owner():
    assert rd.price_signal is rs.price_signal
    assert rd.macro_signal is rs.macro_signal
    assert rd.reading_series is rs.reading_series


def test_dial_matches_registered_price_signal_on_random_walk(tmp_path):
    rng = np.random.default_rng(5)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
    _store(tmp_path / "s.duckdb", closes, _days(500))
    d = rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert d["dial"]["state"] == ("half" if rd.price_signal(closes, 499) == 1.0 else "full")


def _reject(const):
    raise ValueError(f"non-finite constant {const} in the file")


def test_written_file_is_strict_json_and_archived(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260))
    main, archive = rs.write_risk_state(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    assert main == tmp_path / "out" / "risk_state.json"
    assert archive == tmp_path / "out" / "archive" / "2026-11-12" / "risk_state" / "risk_state.json"
    text = main.read_text(encoding="utf-8")
    parsed = json.loads(text, parse_constant=_reject)
    assert parsed["dial"]["state"] == "half"
    assert archive.read_text(encoding="utf-8") == text


def test_degraded_file_is_still_strict_json(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(199), _days(199), drop_regime=True, vix=[])
    main, _ = rs.write_risk_state(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    parsed = json.loads(main.read_text(encoding="utf-8"), parse_constant=_reject)
    assert parsed["degraded"] is True and len(parsed["reasons"]) >= 3


def test_store_is_opened_read_only(tmp_path):
    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260))
    before = (tmp_path / "s.duckdb").read_bytes()
    rs.build_risk_state(tmp_path / "s.duckdb", NOW)
    assert (tmp_path / "s.duckdb").read_bytes() == before


def test_cli_command_registered(tmp_path):
    from typer.testing import CliRunner

    from macro_engine.cli import app

    _store(tmp_path / "s.duckdb", _flat(last=99.0), _days(260))
    res = CliRunner().invoke(
        app, ["write-risk-state", "--db-path", str(tmp_path / "s.duckdb"), "--outputs-dir", str(tmp_path / "o")]
    )
    assert res.exit_code == 0, res.output
    assert (tmp_path / "o" / "risk_state.json").exists()


# --------------------------------------------------------------------------- the month decision (WO_DB1)


def _md_store(tmp_path, closes, days):
    _store(tmp_path / "s.duckdb", closes, days)
    return tmp_path / "s.duckdb"


def _md_path():
    """Flat 100 for 300 sessions, then a fall below the 200-day mean on 2026-10-30 (the last close of October),
    a recovery on 2026-11-02 (the first session of November) and a fall again on 2026-11-09."""
    days = pd.bdate_range("2025-09-01", "2026-11-11")
    closes = np.full(len(days), 100.0)
    idx = {d.date().isoformat(): i for i, d in enumerate(days)}
    closes[idx["2026-10-30"]] = 90.0  # below
    closes[idx["2026-11-02"]] = 105.0  # above
    closes[idx["2026-11-09"]] = 95.0  # below
    return days, closes, idx


def test_month_decision_uses_last_close_before_the_month(tmp_path):
    days, closes, _ = _md_path()
    d = rs.build_risk_state(_md_store(tmp_path, closes, days), NOW)
    md = d["month_decision"]
    assert md["month"] == "2026-11" and md["decided_on"] == "2026-10-30"
    assert md["dial"] == "half" and md["spy_close"] == 90.0
    assert md["spy_mean_200"] == pytest.approx((199 * 100.0 + 90.0) / 200)
    # the daily dial on 2026-11-11 is a different matter: close 100, flat mean ~ above
    assert d["dial"]["state"] == "full"


def test_month_decision_same_on_first_session_and_mid_month(tmp_path):
    days, closes, idx = _md_path()
    first = rs.build_risk_state(
        _md_store(tmp_path, closes[: idx["2026-11-02"] + 1], days[: idx["2026-11-02"] + 1]),
        datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc),
    )
    (tmp_path / "s.duckdb").unlink()
    mid = rs.build_risk_state(_md_store(tmp_path, closes, days), NOW)
    assert first["asof"] == "2026-11-02" and mid["asof"] == "2026-11-11"
    assert first["month_decision"] == mid["month_decision"]
    assert first["month_decision"]["decided_on"] == "2026-10-30"


def test_month_decision_unchanged_by_a_daily_dial_change_inside_the_month(tmp_path):
    days, closes, idx = _md_path()
    states = {}
    for last in ("2026-11-02", "2026-11-09"):  # the daily dial is full, then half
        n = idx[last] + 1
        path = tmp_path / f"{last}.duckdb"
        _store(path, closes[:n], days[:n])
        states[last] = rs.build_risk_state(path, datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc) if last > "2026-11-05" else datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc))
    assert states["2026-11-02"]["dial"]["state"] == "full" and states["2026-11-09"]["dial"]["state"] == "half"
    assert states["2026-11-02"]["month_decision"] == states["2026-11-09"]["month_decision"]


def test_month_decision_rolls_at_month_boundary(tmp_path):
    days, closes, idx = _md_path()
    n = idx["2026-10-30"] + 1  # as-of the last October close: October's decision, taken on 2026-09-30
    d = rs.build_risk_state(_md_store(tmp_path, closes[:n], days[:n]), datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc))
    md = d["month_decision"]
    assert md["month"] == "2026-10" and md["decided_on"] == "2026-09-30"
    assert md["dial"] == "full" and md["spy_close"] == 100.0


def test_month_decision_null_with_reason_when_too_few_closes(tmp_path):
    days = pd.bdate_range("2026-03-02", "2026-11-11")  # fewer than 200 closes before November
    d = rs.build_risk_state(_md_store(tmp_path, np.full(len(days), 100.0), days), NOW)
    md = d["month_decision"]
    assert md["month"] == "2026-11" and md["dial"] is None
    assert md["reason"].startswith("month_decision_short")


def test_month_decision_null_when_no_close_before_the_month(tmp_path):
    days = pd.bdate_range("2026-11-02", "2026-11-11")
    d = rs.build_risk_state(_md_store(tmp_path, np.full(len(days), 100.0), days), NOW)
    assert d["month_decision"]["dial"] is None and d["month_decision"]["reason"]


def test_month_decision_null_when_store_has_no_spy(tmp_path):
    days = pd.bdate_range("2026-03-02", "2026-11-11")
    _store(tmp_path / "s.duckdb", np.full(len(days), 100.0), days)
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("DELETE FROM sector_proxy_prices")
    con.close()
    md = rs.build_risk_state(tmp_path / "s.duckdb", NOW)["month_decision"]
    assert md["dial"] is None and md["reason"]


def test_month_decision_uses_the_daily_rule_function(tmp_path, monkeypatch):
    days, closes, idx = _md_path()
    calls = []
    real = rs.price_signal
    monkeypatch.setattr(rs, "price_signal", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    d = rs.build_risk_state(_md_store(tmp_path, closes, days), NOW)
    assert idx["2026-10-30"] in calls and d["month_decision"]["dial"] == "half"


def test_status_stays_shadow_and_names_the_consumer_and_the_truth(tmp_path):
    days, closes, _ = _md_path()
    d = rs.build_risk_state(_md_store(tmp_path, closes, days), NOW)
    assert d["status"] == "shadow"
    assert d["consumers"] == ["screener paper book equal_dial"]
    assert "paper" in d["changes"] and "no real-money" in d["changes"]
    assert "nothing" not in d["changes"].lower()
