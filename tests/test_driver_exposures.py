"""Tests for the measured driver exposures (src/macro_engine/driver_exposures.py, WO_MD1), on synthetic stores.

The synthetic store: SPY, DGS10 and oil are random walks. Each fund's log price is built from the three driver LEVELS,
so that its return relative to SPY over ANY n sessions is exactly `beta_market * SPY return + beta_rates * yield change
+ beta_oil * oil change` (plus noise when asked). A fund's betas are therefore known in advance.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
import yaml

from macro_engine import driver_exposures as de

END = "2026-11-11"
NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
BETAS = {  # fund -> (market, rates per basis point, oil)
    "XLK": (0.6, -0.01, 0.3),
    "XLE": (0.1, 0.0, 0.4),
    "XLU": (-0.7, -0.02, 0.0),
    "XLP": (-0.9, 0.0, 0.0),
    "XLRE": (0.2, -0.03, 0.05),
}


def _reject(const):
    raise ValueError(f"non-finite constant in the file: {const}")


def make_store(path, n=1500, noise=0.0, flat_tail=0, drop_oil=False, drop_oil_tail=0, drop_spy=False, spy_end=None):
    """Write a synthetic store; returns the arrays. `flat_tail` freezes SPY, DGS10 and oil over the last rows."""
    rng = np.random.default_rng(11)
    days = pd.bdate_range(end=spy_end or END, periods=n)
    spy = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)) )
    dgs = 4.0 + np.cumsum(rng.normal(0.0, 0.05, n))
    oil = 70.0 * np.exp(np.cumsum(rng.normal(0.0, 0.015, n)))
    if flat_tail:
        for a in (spy, dgs, oil):
            a[-flat_tail:] = a[-flat_tail - 1]
    ls, lo = 100.0 * np.log(spy), 100.0 * np.log(oil)
    funds = {}
    for tk in de.FUNDS:
        bm, br, bo = BETAS.get(tk, (0.0, 0.0, 0.0))
        lf = ls + bm * ls + br * 100.0 * dgs + bo * lo + 100.0 * np.cumsum(rng.normal(0.0, noise, n)) * (noise > 0)
        funds[tk] = 50.0 * np.exp(lf / 100.0)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sector_proxy_prices (ticker VARCHAR, date DATE, close DOUBLE, source VARCHAR)")
    con.execute("CREATE TABLE raw_observations (series_id VARCHAR, date DATE, value DOUBLE, realtime_start DATE)")
    con.execute("CREATE TABLE other_table (a INTEGER)")
    con.execute("INSERT INTO other_table VALUES (1), (2)")
    rows = []
    if not drop_spy:
        rows += [("SPY", d.date(), float(c), "x") for d, c in zip(days, spy)]
    for tk, px in funds.items():
        rows += [(tk, d.date(), float(c), "x") for d, c in zip(days, px)]
    frame = pd.DataFrame(rows)  # noqa: F841 (read by name from DuckDB)
    con.execute("INSERT INTO sector_proxy_prices SELECT * FROM frame")
    obs = []
    obs += [("DGS10", d.date(), float(v), d.date()) for d, v in zip(days, dgs)]
    if not drop_oil:
        obs += [("DCOILWTICO", d.date(), float(v), d.date()) for d, v in zip(days[: n - drop_oil_tail], oil[: n - drop_oil_tail])]
    frame = pd.DataFrame(obs)  # noqa: F841
    con.execute("INSERT INTO raw_observations SELECT * FROM frame")
    con.close()
    return {"days": days, "spy": spy, "dgs": dgs, "oil": oil, "funds": funds}


def build(tmp_path, **kw):
    arrays = make_store(tmp_path / "s.duckdb", **kw)
    return de.build_driver_exposures(tmp_path / "s.duckdb", NOW), arrays


# --------------------------------------------------------------------------- the fit


def test_exact_linear_fund_recovers_its_betas(tmp_path):
    d, a = build(tmp_path)
    for tk, (bm, br, bo) in BETAS.items():
        e = d["funds"][tk]["exposures"]
        assert e["market"]["beta"] == pytest.approx(bm, abs=1e-6), tk
        assert e["rates"]["beta"] == pytest.approx(br, abs=1e-6), tk
        assert e["oil"]["beta"] == pytest.approx(bo, abs=1e-6), tk
        assert d["funds"][tk]["r2"] == pytest.approx(1.0, abs=1e-9)
    assert d["funds"]["XLK"]["n_weeks"] == de.WEEKS
    assert d["funds"]["XLK"]["sector_id"] == "information_technology"
    assert d["degraded"] is False and d["as_of"] == END


def test_noisy_fund_recovers_betas_within_tolerance_and_t_signs(tmp_path):
    d, _ = build(tmp_path, noise=0.003)
    e = d["funds"]["XLU"]["exposures"]
    assert e["market"]["beta"] == pytest.approx(-0.7, abs=0.15) and e["market"]["t"] < -2.5
    assert e["rates"]["beta"] == pytest.approx(-0.02, abs=0.01) and e["rates"]["t"] < 0
    assert d["funds"]["XLE"]["exposures"]["oil"]["t"] > 2.5
    assert 0.0 < d["funds"]["XLK"]["r2"] < 1.0


def test_weekly_sample_is_anchored_on_the_newest_session_and_does_not_overlap():
    idx = de.weekly_indices(23)
    assert list(idx) == [2, 7, 12, 17, 22]
    assert (np.diff(idx) == de.SAMPLE).all()
    assert de.weekly_indices(5)[-1] == 4 and len(de.weekly_indices(0)) == 0


def test_the_window_runs_to_the_newest_session_in_weekly_steps(tmp_path):
    d, a = build(tmp_path)
    f = d["funds"]["XLK"]
    assert f["last_week"] == a["days"][-1].date().isoformat()
    first = a["days"][-1 - 5 * (de.WEEKS - 1)].date().isoformat()
    assert f["first_week"] == first


def test_fewer_than_104_weeks_gives_null_exposures_with_a_reason(tmp_path):
    d, _ = build(tmp_path, n=5 * 100 + 7)  # 100 weekly rows with a 5-session change each
    assert d["degraded"] is True
    f = d["funds"]["XLK"]
    assert f["exposures"]["market"] == {"beta": None, "t": None} and f["r2"] is None
    assert f["reason"].startswith("fund_short") and f["n_weeks"] < de.MIN_WEEKS
    assert any(r.startswith("no_exposures") for r in d["reasons"])
    assert f["readings"]["21"]["implied_total"] is None and f["readings"]["21"]["realised"] is not None


def test_104_weeks_is_enough(tmp_path):
    d, _ = build(tmp_path, n=5 * 105 + 1)
    assert d["funds"]["XLK"]["n_weeks"] == 105 and d["funds"]["XLK"]["r2"] is not None


def test_a_missing_driver_series_is_dropped_and_the_others_still_fit(tmp_path):
    d, _ = build(tmp_path, drop_oil=True)
    assert d["degraded"] is False
    assert any(r.startswith("driver_missing: oil") for r in d["reasons"])
    assert any(r.startswith("driver_dropped: oil") and "market, rates" in r for r in d["reasons"])
    for f in d["funds"].values():
        assert f["exposures"]["oil"] == {"beta": None, "t": None}
    assert d["funds"]["XLU"]["exposures"]["market"]["beta"] == pytest.approx(-0.7, abs=1e-6)  # its oil beta is 0
    assert d["drivers"]["oil"]["windows"]["21"] == {"change": None, "z": None, "moved": False}
    assert d["funds"]["XLU"]["readings"]["21"]["implied"]["oil"] is None


# --------------------------------------------------------------------------- what moved and the reading


def test_moved_at_exactly_one():
    assert de.is_moved(1.0) and de.is_moved(-1.0) and de.is_moved(2.5)
    assert not de.is_moved(0.999) and not de.is_moved(0.0) and not de.is_moved(None)


def test_drivers_block_is_the_change_and_z_of_each_driver(tmp_path):
    from macro_engine.market_state import z_of_change

    d, a = build(tmp_path)
    ch21 = 100.0 * np.log(a["spy"][-1] / a["spy"][-22])
    w = d["drivers"]["market"]["windows"]["21"]
    assert w["change"] == pytest.approx(ch21)
    series = 100.0 * np.log(a["spy"][21:] / a["spy"][:-21])
    assert w["z"] == pytest.approx(z_of_change(np.concatenate([np.full(21, np.nan), series])))
    assert d["drivers"]["rates"]["windows"]["63"]["change"] == pytest.approx((a["dgs"][-1] - a["dgs"][-64]) * 100.0)
    assert d["drivers"]["oil"]["windows"]["21"]["change"] == pytest.approx(100.0 * np.log(a["oil"][-1] / a["oil"][-22]))
    assert w["moved"] == (abs(w["z"]) >= 1.0)


def test_exposed_needs_both_a_significant_t_and_a_move(tmp_path):
    d, _ = build(tmp_path, noise=0.003, flat_tail=70)  # rates flat: they cannot have moved
    assert d["drivers"]["rates"]["windows"]["21"]["moved"] is False
    xlu = d["funds"]["XLU"]
    assert abs(xlu["exposures"]["rates"]["t"]) >= de.T_THRESHOLD  # significant, but did not move
    for w in ("21", "63"):
        for tk, f in d["funds"].items():
            want = {
                name
                for name in de.DRIVERS
                if f["exposures"][name]["t"] is not None
                and abs(f["exposures"][name]["t"]) >= de.T_THRESHOLD
                and d["drivers"][name]["windows"][w]["moved"]
            }
            assert {r["driver"] for r in f["readings"][w]["exposed"]} == want, (tk, w)


def test_exposed_lists_both_conditions_and_sorts_by_implied(tmp_path):
    d, _ = build(tmp_path, noise=0.003)
    found = False
    for f in d["funds"].values():
        ex = f["readings"]["21"]["exposed"]
        assert [abs(r["implied"]) for r in ex] == sorted((abs(r["implied"]) for r in ex), reverse=True)
        for r in ex:
            found = True
            assert abs(r["t"]) >= 2.5 and abs(r["z"]) >= 1.0
            assert r["implied"] == pytest.approx(r["beta"] * r["change"])
    assert found


def test_implied_realised_and_unexplained_arithmetic(tmp_path):
    d, a = build(tmp_path, noise=0.003)
    w = 21
    for tk, f in d["funds"].items():
        r = f["readings"][str(w)]
        for name in de.DRIVERS:
            change = d["drivers"][name]["windows"][str(w)]["change"]
            assert r["implied"][name] == pytest.approx(f["exposures"][name]["beta"] * change)
        assert r["implied_total"] == pytest.approx(sum(r["implied"].values()))
        fund = a["funds"][tk]
        assert r["realised"] == pytest.approx(100.0 * np.log(fund[-1] / fund[-22]) - 100.0 * np.log(a["spy"][-1] / a["spy"][-22]))
        assert r["unexplained"] == pytest.approx(r["realised"] - r["implied_total"])


def test_exact_fund_is_fully_explained_and_zero_is_a_value(tmp_path):
    d, _ = build(tmp_path)
    r = d["funds"]["XLP"]["readings"]["21"]  # betas (-0.9, 0, 0): rates and oil implied are 0.0, not None
    assert r["implied"]["rates"] == pytest.approx(0.0, abs=1e-9) and r["implied"]["oil"] == pytest.approx(0.0, abs=1e-9)
    assert r["unexplained"] == pytest.approx(0.0, abs=1e-6)


def test_most_helped_and_hurt_rank_the_sector_funds_by_implied_total(tmp_path):
    d, _ = build(tmp_path, noise=0.003)
    assert d["drivers"]["market"]["windows"]["21"]["moved"] or d["drivers"]["rates"]["windows"]["21"]["moved"] or d["drivers"]["oil"]["windows"]["21"]["moved"]
    totals = {tk: d["funds"][tk]["readings"]["21"]["implied_total"] for tk in de.SECTOR_FUNDS}
    order = sorted(totals, key=totals.get)
    assert [r["fund"] for r in d["most_helped"]] == order[::-1][:3]
    assert [r["fund"] for r in d["most_hurt"]] == order[:3]
    assert d["most_helped"][0]["sector_id"] == d["funds"][order[-1]]["sector_id"]
    assert all(r["fund"] not in de.SUB_FUNDS for r in d["most_helped"] + d["most_hurt"])
    assert d["summary"].startswith("Over 21 sessions ") and " ahead and " in d["summary"]


def test_most_hurt_is_empty_when_nothing_moved(tmp_path):
    d, _ = build(tmp_path, noise=0.003, flat_tail=70)
    assert not any(d["drivers"][n]["windows"]["21"]["moved"] for n in de.DRIVERS)
    assert d["most_helped"] == [] and d["most_hurt"] == []
    assert any(r.startswith("most_helped_empty") for r in d["reasons"])
    assert d["summary"] == "Over 21 sessions no driver moved by a full standard deviation."


# --------------------------------------------------------------------------- history, degraded


def test_history_block_length_and_alignment(tmp_path):
    d, a = build(tmp_path, drop_oil_tail=8)
    h = d["history"]
    assert len(h["dates"]) == len(h["spy_close"]) == len(h["dgs10"]) == len(h["oil"]) == de.HISTORY
    assert h["dates"][-1] == END and h["dates"][0] == a["days"][-de.HISTORY].date().isoformat()
    assert h["spy_close"][-1] == pytest.approx(a["spy"][-1]) and h["dgs10"][-1] == pytest.approx(a["dgs"][-1])
    assert h["oil"][-4] == pytest.approx(a["oil"][-9]) and h["oil"][-3:] == [None, None, None]  # carried 5 sessions, then null


def test_degraded_spy_missing(tmp_path):
    d, _ = build(tmp_path, drop_spy=True)
    assert d["degraded"] is True and d["funds"] is None and d["drivers"] is None
    assert d["most_helped"] == [] and any(r.startswith("spy_missing") for r in d["reasons"])


def test_degraded_spy_stale_and_the_5_day_edge(tmp_path):
    make_store(tmp_path / "s.duckdb")
    d = de.build_driver_exposures(tmp_path / "s.duckdb", datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc))  # 6 days after
    assert d["degraded"] is True and d["funds"] is None and any(r.startswith("spy_stale") for r in d["reasons"])
    d5 = de.build_driver_exposures(tmp_path / "s.duckdb", datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc))
    assert d5["degraded"] is False


def test_singular_design_is_a_null_with_a_reason():
    assert de.fit_ols(np.arange(10.0), np.ones((10, 1))) is None  # a constant column
    assert de.fit_ols(np.arange(3.0), np.arange(6.0).reshape(3, 2)) is None  # no degrees of freedom


def test_sub_industry_sector_ids_match_the_config():
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "sectors.yaml").read_text(encoding="utf-8"))
    by_proxy = {s["proxy_ticker"]: s["sector_id"] for s in cfg["sectors"]}
    for tk in de.SUB_FUNDS:
        assert de.SUB_IDS[tk] == by_proxy[tk]
    for tk, (sid, _) in de.SECTOR_FUNDS.items():
        assert sid == by_proxy[tk]


# --------------------------------------------------------------------------- files, CLI, additivity


def test_write_archive_and_strict_json(tmp_path):
    make_store(tmp_path / "s.duckdb", noise=0.003)
    main, archive = de.write_driver_exposures(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    assert main == tmp_path / "out" / "driver_exposures.json"
    assert archive == tmp_path / "out" / "archive" / "2026-11-12" / "driver_exposures" / "driver_exposures.json"
    text = main.read_text(encoding="utf-8")
    parsed = json.loads(text, parse_constant=_reject)
    assert parsed["schema"] == "mri.driver_exposures.v1" and parsed["degraded"] is False
    assert archive.read_text(encoding="utf-8") == text


def test_no_nan_in_a_degraded_or_short_file(tmp_path):
    make_store(tmp_path / "s.duckdb", n=300, drop_oil=True)
    main, _ = de.write_driver_exposures(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    assert json.loads(main.read_text(encoding="utf-8"), parse_constant=_reject)["degraded"] is True
    (tmp_path / "s.duckdb").unlink()
    make_store(tmp_path / "s.duckdb", drop_spy=True)
    main, _ = de.write_driver_exposures(tmp_path / "s.duckdb", tmp_path / "out2", NOW)
    assert json.loads(main.read_text(encoding="utf-8"), parse_constant=_reject)["degraded"] is True


def test_cli_command_registered(tmp_path):
    from typer.testing import CliRunner

    from macro_engine.cli import app

    make_store(tmp_path / "s.duckdb")
    res = CliRunner().invoke(
        app, ["write-driver-exposures", "--db-path", str(tmp_path / "s.duckdb"), "--outputs-dir", str(tmp_path / "o")]
    )
    assert res.exit_code == 0, res.output
    assert (tmp_path / "o" / "driver_exposures.json").exists()


def test_building_changes_no_other_table_or_file(tmp_path):
    make_store(tmp_path / "s.duckdb")
    before_bytes = (tmp_path / "s.duckdb").read_bytes()
    con = duckdb.connect(str(tmp_path / "s.duckdb"), read_only=True)
    tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
    before = {t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in tables}
    con.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    de.build_driver_exposures(tmp_path / "s.duckdb", NOW)
    con = duckdb.connect(str(tmp_path / "s.duckdb"), read_only=True)
    after_tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
    after = {t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in after_tables}
    con.close()
    assert after_tables == tables and after == before
    assert (tmp_path / "s.duckdb").read_bytes() == before_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == files
