"""Tests for the daily market state (src/macro_engine/market_state.py, WO_MK1), on synthetic stores.

The synthetic store: SPY is flat at 100 (its last close is set by each test); every fund is SPY times a noise factor
and every FRED series is a base level plus noise. The last 105 rows of every noise series repeat a 21-row pattern, so
a change over 21 or 63 sessions is exactly zero on the last day unless a test adds a shock, and the standard
deviation of the changes (from the noisy earlier rows) is not zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine import market_state as ms

END = "2026-11-11"
NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)

FRED_BASE = {  # series -> (base, noise scale, "add" | "log")
    "VIXCLS": (15.0, 0.5, "add"),
    "DGS2": (3.0, 0.05, "add"),
    "DGS10": (4.0, 0.05, "add"),
    "DFII10": (1.8, 0.05, "add"),
    "T10YIE": (2.2, 0.03, "add"),
    "BAA10Y": (2.0, 0.03, "add"),
    "BAMLH0A0HYM2": (3.5, 0.1, "add"),
    "DCOILWTICO": (70.0, 0.01, "log"),
    "DTWEXBGS": (120.0, 0.002, "log"),
}
FUNDS = [*ms.SECTOR_FUNDS, *ms.SUB_FUNDS]


def _reject(const):
    raise ValueError(f"non-finite constant in the file: {const}")


def _noise(rng, n, scale):
    x = rng.normal(0.0, scale, n)
    x[-105:] = np.tile(rng.normal(0.0, scale, 21), 5)
    return x


def _insert(con, table, rows):
    frame = pd.DataFrame(rows)  # noqa: F841 (read by name from DuckDB)
    if len(frame):
        con.execute(f"INSERT INTO {table} SELECT * FROM frame")


def make_store(
    path,
    n=800,
    spy_last=110.0,
    vix_last=15.0,
    spy_tail=None,
    vix_tail=None,
    shocks=None,
    shock_prior=(),
    drop_series=(),
    drop_tail=None,
    drop_spy=False,
):
    """Write a synthetic store; returns the trading days. `shocks` adds to the last value of a noise series (a FRED
    series name or a fund ticker); names in `shock_prior` get the same shock 21 rows earlier, so only their 63-session
    change moves. `drop_tail` removes the last k rows of a FRED series."""
    rng = np.random.default_rng(7)
    days = pd.bdate_range(end=END, periods=n)
    shocks = shocks or {}
    drop_tail = drop_tail or {}
    spy = np.full(n, 100.0)
    spy[-1] = spy_last
    if spy_tail is not None:
        spy[-len(spy_tail) :] = spy_tail

    def shocked(name, x):
        if name in shocks:
            x[-1] += shocks[name]
            if name in shock_prior:
                x[-22] += shocks[name]
        return x

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sector_proxy_prices (ticker VARCHAR, date DATE, close DOUBLE, source VARCHAR)")
    con.execute(
        "CREATE TABLE raw_observations (series_id VARCHAR, date DATE, value DOUBLE, realtime_start DATE)"
    )
    con.execute("CREATE TABLE other_table (a INTEGER)")
    con.execute("INSERT INTO other_table VALUES (1), (2)")
    price_rows = []
    if not drop_spy:
        price_rows += [("SPY", d.date(), float(c), "x") for d, c in zip(days, spy)]
    for tk in FUNDS:
        x = shocked(tk, _noise(rng, n, 0.01))
        px = spy * np.exp(x)
        price_rows += [(tk, d.date(), float(c), "x") for d, c in zip(days, px)]
    _insert(con, "sector_proxy_prices", price_rows)
    fred_rows = []
    for sid, (base, scale, kind) in FRED_BASE.items():
        x = shocked(sid, _noise(rng, n, scale))
        v = base + x if kind == "add" else base * np.exp(x)
        if sid == "VIXCLS":
            v[-1] = vix_last
            if vix_tail is not None:
                v[-len(vix_tail) :] = vix_tail
        if sid in drop_series:
            continue
        k = drop_tail.get(sid, 0)
        rows = [(sid, d.date(), float(c), d.date()) for d, c in zip(days, v)]
        fred_rows += rows[: n - k]
    _insert(con, "raw_observations", fred_rows)
    con.close()
    return days


def build(tmp_path, **kw):
    make_store(tmp_path / "s.duckdb", **kw)
    return ms.build_market_state(tmp_path / "s.duckdb", NOW)


STRESS = dict(spy_last=90.0, vix_last=25.0)


# --------------------------------------------------------------------------- the step


@pytest.mark.parametrize(
    "below,vix,expected",
    [
        (False, 19.99, "calm"),
        (False, 20.0, "watch"),  # VIX exactly 20 is not below 20
        (True, 19.99, "watch"),
        (True, 20.0, "stress"),
        (True, 39.99, "stress"),
        (True, 40.0, "crisis"),  # exactly 40 is crisis
        (False, 40.0, "crisis"),
        (False, 55.0, "crisis"),
        (None, 25.0, None),
        (True, None, None),
    ],
)
def test_step_rule_and_boundaries(below, vix, expected):
    assert ms.stress_step(below, vix) == expected


def test_step_in_the_store_each_state(tmp_path):
    for spy_last, vix_last, expected in [(110.0, 15.0, "calm"), (110.0, 25.0, "watch"), (90.0, 15.0, "watch"), (90.0, 25.0, "stress"), (90.0, 45.0, "crisis")]:
        d = build(tmp_path, spy_last=spy_last, vix_last=vix_last)
        assert d["step"] == expected, (spy_last, vix_last)
        (tmp_path / "s.duckdb").unlink()


def test_close_equal_to_its_mean_is_not_below(tmp_path):
    d = build(tmp_path, spy_last=100.0, vix_last=20.0)  # flat SPY: close equals the 200-close mean
    assert d["facts"]["spy"]["below_200d"] is False and d["step"] == "watch"
    (tmp_path / "s.duckdb").unlink()
    d = build(tmp_path, spy_last=100.0, vix_last=19.99)
    assert d["step"] == "calm"


def test_since_is_the_first_day_of_the_current_run(tmp_path):
    days = make_store(tmp_path / "s.duckdb", spy_tail=[100.0, 90.0, 90.0, 90.0], vix_tail=[15.0, 25.0, 25.0, 25.0])
    d = ms.build_market_state(tmp_path / "s.duckdb", NOW)
    assert d["step"] == "stress" and d["since"] == days[-3].date().isoformat()


def test_since_is_broken_by_a_different_step(tmp_path):
    days = make_store(tmp_path / "s.duckdb", spy_tail=[90.0, 90.0, 90.0, 90.0], vix_tail=[25.0, 15.0, 25.0, 25.0])
    d = ms.build_market_state(tmp_path / "s.duckdb", NOW)
    assert d["step"] == "stress" and d["since"] == days[-2].date().isoformat()  # the day before was `watch`


# --------------------------------------------------------------------------- the driver, one winner each


def test_rates_wins(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"DGS2": 0.6, "DGS10": 0.6})
    assert d["driver"] == "rates" and d["driver_window"] == 21
    assert d["scores"]["21"]["rates"] >= 1.0
    assert d["scores"]["21"]["growth_credit"] < d["scores"]["21"]["rates"]


def test_growth_credit_wins(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"BAA10Y": 1.0})
    assert d["driver"] == "growth_credit"
    assert d["scores"]["21"]["growth_credit"] >= 1.0


def test_inflation_wins(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"DCOILWTICO": 0.3, "T10YIE": 0.3})
    assert d["driver"] == "inflation"


def test_sector_led_names_the_worst_fund(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"XLK": -0.1})
    assert d["driver"] == "sector_led:information_technology"
    assert d["scores"]["21"]["sector_fund"] == "XLK" and d["scores"]["21"]["sector"] >= 1.0
    assert "technology" in d["summary"]


def test_sector_led_takes_the_worst_of_two(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"XLE": -0.05, "XLU": -0.2})
    assert d["driver"] == "sector_led:utilities"
    (tmp_path / "s.duckdb").unlink()
    d = build(tmp_path, **STRESS, shocks={"XLE": -0.2, "XLU": -0.05})
    assert d["driver"] == "sector_led:energy"


def test_unclear_under_the_threshold(tmp_path):
    d = build(tmp_path, **STRESS)  # every change is zero
    assert d["step"] == "stress" and d["driver"] == "unclear" and d["driver_window"] is None
    assert d["scores"]["21"]["rates"] == pytest.approx(0.0, abs=1e-9)  # 0.0 is a value, not an absence
    assert d["backdrop"] is None
    assert "No single driver" in d["summary"]


def test_pick_driver_threshold_boundary():
    assert ms.pick_driver({"rates": 1.0, "inflation": 0.2}) == "rates"
    assert ms.pick_driver({"rates": 0.999, "inflation": 0.2}) == "unclear"
    assert ms.pick_driver({"rates": None, "growth_credit": None}) == "unclear"
    assert ms.pick_driver({"rates": 1.5, "sector_led:energy": 2.5}) == "sector_led:energy"
    assert ms.pick_driver({"rates": -3.0, "inflation": 0.0}) == "unclear"


def test_calm_gives_none(tmp_path):
    d = build(tmp_path, spy_last=110.0, vix_last=15.0, shocks={"DGS2": 0.6, "DGS10": 0.6})
    assert d["step"] == "calm" and d["driver"] == "none" and d["driver_window"] is None
    assert d["summary"].startswith("Calm since ")


def test_63_session_fallback(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"DGS2": 0.6, "DGS10": 0.6}, shock_prior=("DGS2", "DGS10"))
    assert d["scores"]["21"]["driver"] == "unclear" and d["scores"]["63"]["driver"] == "rates"
    assert d["driver"] == "rates" and d["driver_window"] == 63 and d["backdrop"] is None
    assert "in 63 sessions" in d["summary"]


def test_backdrop_is_the_63_session_driver_when_it_differs(tmp_path):
    d = build(
        tmp_path,
        **STRESS,
        shocks={"DGS2": 2.0, "DGS10": 2.0, "DCOILWTICO": 0.1, "T10YIE": 0.1},
        shock_prior=("DGS2", "DGS10"),
    )
    assert d["scores"]["21"]["driver"] == "inflation" and d["scores"]["63"]["driver"] == "rates"
    assert d["driver"] == "inflation" and d["driver_window"] == 21 and d["backdrop"] == "rates"
    assert "Led by inflation" in d["summary"] and "Backdrop: rates (2-year yield" in d["summary"]


def test_publish_driver_pure():
    assert ms.publish_driver("calm", "rates", "inflation") == ("none", None, "inflation")
    assert ms.publish_driver("stress", "rates", "rates") == ("rates", 21, None)
    assert ms.publish_driver("stress", "unclear", "rates") == ("rates", 63, None)
    assert ms.publish_driver("stress", "unclear", "unclear") == ("unclear", None, None)
    assert ms.publish_driver("stress", "rates", "unclear") == ("rates", 21, None)
    assert ms.publish_driver(None, "rates", "rates") == (None, None, None)


def test_z_and_mean_of_legs():
    changes = np.array([1.0, -1.0] * 300 + [2.0])
    assert ms.z_of_change(changes) == pytest.approx(2.0 / float(np.std(changes)))
    assert ms.z_of_change(np.array([1.0, -1.0] * 100 + [2.0])) is None  # fewer than 500
    assert ms.z_of_change(np.array([np.nan] * 600 + [np.nan])) is None
    assert ms.mean_of_legs([2.0, None, 0.0]) == 1.0
    assert ms.mean_of_legs([None, None]) is None
    assert ms.mean_of_legs([0.0]) == 0.0


# --------------------------------------------------------------------------- missing legs, degraded


def test_a_missing_leg_is_a_reason_and_does_not_degrade(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"DGS10": 0.6}, drop_series=("DGS2",))
    assert d["degraded"] is False and d["step"] == "stress"
    assert any(r.startswith("leg_missing") and "DGS2" in r for r in d["reasons"])
    assert d["scores"]["21"]["rates"] is not None  # the mean of the legs that exist
    assert d["scores"]["21"]["legs"]["DGS2"]["z"] is None


def test_too_short_a_history_gives_null_scores_not_a_degraded_step(tmp_path):
    d = build(tmp_path, n=400, **STRESS)
    assert d["step"] == "stress" and d["degraded"] is False
    assert d["scores"]["21"]["rates"] is None and d["driver"] == "unclear"
    assert any(r.startswith("leg_missing") for r in d["reasons"])


def test_degraded_spy_missing(tmp_path):
    d = build(tmp_path, drop_spy=True)
    assert d["degraded"] is True and d["step"] is None and d["driver"] is None
    assert any(r.startswith("spy_missing") for r in d["reasons"])


def test_degraded_spy_stale_and_the_5_day_edge(tmp_path):
    make_store(tmp_path / "s.duckdb", **STRESS)
    d = ms.build_market_state(tmp_path / "s.duckdb", datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc))  # 6 days after
    assert d["degraded"] is True and d["step"] is None and any(r.startswith("spy_stale") for r in d["reasons"])
    d5 = ms.build_market_state(tmp_path / "s.duckdb", datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc))
    assert d5["step"] == "stress"


def test_degraded_spy_short(tmp_path):
    d = build(tmp_path, n=199, **STRESS)
    assert d["degraded"] is True and d["step"] is None and any(r.startswith("spy_short") for r in d["reasons"])


def test_degraded_vix_missing(tmp_path):
    d = build(tmp_path, **STRESS, drop_series=("VIXCLS",))
    assert d["degraded"] is True and d["step"] is None and d["driver"] is None
    assert any(r.startswith("vix_missing") for r in d["reasons"])


def test_vix_carried_forward_at_most_5_sessions(tmp_path):
    d = build(tmp_path, **STRESS, drop_tail={"VIXCLS": 5})  # last value is 5 sessions old: still used
    assert d["step"] is not None and d["facts"]["vix"]["observed"] is not None
    (tmp_path / "s.duckdb").unlink()
    d = build(tmp_path, **STRESS, drop_tail={"VIXCLS": 6})  # 6 sessions old: stale
    assert d["degraded"] is True and d["step"] is None and any(r.startswith("vix_stale") for r in d["reasons"])


def test_align_forward_fill_limit():
    cal = pd.bdate_range("2026-01-05", periods=12)
    s = pd.Series([1.0, 2.0], index=[cal[0], cal[3]])
    values, obs = ms._align(s, cal)
    got = values.to_numpy()
    assert list(got[:3]) == [1.0, 1.0, 1.0] and got[3] == 2.0
    assert list(got[3:9]) == [2.0] * 6  # the value of row 3 reaches rows 4..8 (5 sessions)
    assert np.isnan(got[9:]).all()
    assert obs.iloc[8] == cal[3] and pd.isna(obs.iloc[9])


def test_latest_vintage_wins_and_null_values_are_ignored(tmp_path):
    days = make_store(tmp_path / "s.duckdb", **STRESS)
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    last = days[-1].date()
    con.execute("INSERT INTO raw_observations VALUES ('VIXCLS', ?, 99.0, '2000-01-01')", [last])  # older vintage
    con.execute("INSERT INTO raw_observations VALUES ('VIXCLS', ?, NULL, '2030-01-01')", [last])  # newer, null
    con.close()
    d = ms.build_market_state(tmp_path / "s.duckdb", NOW)
    assert d["facts"]["vix"]["level"] == 25.0


# --------------------------------------------------------------------------- facts and the summary


def test_facts(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"XLK": -0.1})
    spy = d["facts"]["spy"]
    assert spy["close"] == 90.0 and spy["below_200d"] is True
    assert spy["drawdown_pct"] == pytest.approx(-10.0) and spy["return_5"] == pytest.approx(-10.0)
    assert spy["mean_200"] == pytest.approx((199 * 100.0 + 90.0) / 200)
    assert d["facts"]["breadth_below_200d_pct"] == 100.0  # every fund falls with SPY
    assert d["facts"]["vix"]["level"] == 25.0 and d["facts"]["vix"]["change_5"] is not None
    assert d["facts"]["series"]["DGS2"]["change_unit"] == "basis points"
    assert set(d["facts"]["sectors"]) == set(FUNDS)
    assert d["facts"]["sectors"]["XLK"]["relative_21"] == pytest.approx(-10.0, abs=1.0)
    assert d["laggards"][0]["fund"] == "XLK" and len(d["leaders"]) == 3 and len(d["laggards"]) == 3
    assert d["leaders"][0]["relative_21"] >= d["leaders"][1]["relative_21"] >= d["leaders"][2]["relative_21"]
    assert d["evidence"]["study"] == "WO_MK1" and d["evidence"]["steps"]["stress"]["share_of_days_pct"] == 21.0


def test_summary_sentence(tmp_path):
    d = build(tmp_path, **STRESS, shocks={"XLK": -0.1})
    assert d["summary"].startswith("Stress since ")
    assert "Led by technology (−" in d["summary"] and "points against the index in 21 sessions)." in d["summary"]
    assert d["summary"].endswith(" lead.")
    assert d["asof"] == END and d["built_at"] == "2026-11-12T06:00:00Z"


# --------------------------------------------------------------------------- files, CLI, additivity


def test_write_archive_and_strict_json(tmp_path):
    make_store(tmp_path / "s.duckdb", **STRESS, shocks={"XLK": -0.1})
    main, archive = ms.write_market_state(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    assert main == tmp_path / "out" / "market_state.json"
    assert archive == tmp_path / "out" / "archive" / "2026-11-12" / "market_state" / "market_state.json"
    text = main.read_text(encoding="utf-8")
    parsed = json.loads(text, parse_constant=_reject)
    assert parsed["schema"] == "mri.market_state.v1" and parsed["step"] == "stress"
    assert archive.read_text(encoding="utf-8") == text


def test_no_nan_in_a_degraded_or_short_file(tmp_path):
    make_store(tmp_path / "s.duckdb", n=400, **STRESS, drop_series=("DGS2", "DTWEXBGS"))
    main, _ = ms.write_market_state(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    json.loads(main.read_text(encoding="utf-8"), parse_constant=_reject)
    (tmp_path / "s.duckdb").unlink()
    make_store(tmp_path / "s.duckdb", drop_spy=True)
    main, _ = ms.write_market_state(tmp_path / "s.duckdb", tmp_path / "out2", NOW)
    assert json.loads(main.read_text(encoding="utf-8"), parse_constant=_reject)["degraded"] is True


def test_cli_command_registered(tmp_path):
    from typer.testing import CliRunner

    from macro_engine.cli import app

    make_store(tmp_path / "s.duckdb", **STRESS)
    res = CliRunner().invoke(
        app, ["write-market-state", "--db-path", str(tmp_path / "s.duckdb"), "--outputs-dir", str(tmp_path / "o")]
    )
    assert res.exit_code == 0, res.output
    assert (tmp_path / "o" / "market_state.json").exists()


def test_building_changes_no_other_table_or_file(tmp_path):
    make_store(tmp_path / "s.duckdb", **STRESS)
    before_bytes = (tmp_path / "s.duckdb").read_bytes()
    con = duckdb.connect(str(tmp_path / "s.duckdb"), read_only=True)
    tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
    before = {t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in tables}
    con.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    ms.build_market_state(tmp_path / "s.duckdb", NOW)
    con = duckdb.connect(str(tmp_path / "s.duckdb"), read_only=True)
    after_tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
    after = {t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in after_tables}
    con.close()
    assert after_tables == tables and after == before
    assert (tmp_path / "s.duckdb").read_bytes() == before_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == files
