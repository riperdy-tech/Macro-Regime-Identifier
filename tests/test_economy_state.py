"""Tests for the daily economy state (src/macro_engine/economy_state.py, WO_ME1), on synthetic stores.

The default synthetic store: every price index grows at a constant annualised rate, so each rate is the same at every
horizon; unemployment and the funds rate are flat; weekly claims are flat. Tests override single series.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from macro_engine import economy_state as es

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
END = 2026 * 12 + 9  # month key of 2026-10
N = 40


def _reject(const):
    raise ValueError(f"non-finite constant in the file: {const}")


def month_date(k: int) -> date:
    return date(k // 12, k % 12 + 1, 1)


def grow(rate_at, n=N, end=END):
    """Monthly index ending at month `end`; rate_at(i) is the annualised percent growth into step i (0 = first)."""
    v, out = 100.0, []
    for i in range(n):
        if i:
            v *= (1.0 + rate_at(i) / 100.0) ** (1.0 / 12.0)
        out.append((month_date(end - n + 1 + i), v))
    return out


def flat(value, n=N, end=END):
    return [(month_date(end - n + 1 + i), value) for i in range(n)]


def default_series():
    weekly = [(date(2026, 11, 7) - timedelta(days=7 * i), 200000.0) for i in range(70)][::-1]
    return {
        "PCEPI": grow(lambda i: 3.0),
        "PCEPILFE": grow(lambda i: 3.0),
        "CPIAUCSL": grow(lambda i: 3.0),
        "CPILFESL": grow(lambda i: 3.0),
        "UNRATE": flat(4.0),
        "PAYEMS": [(d, 150000.0 * v / 100.0) for d, v in grow(lambda i: 1.0)],
        "INDPRO": grow(lambda i: 2.0),
        "FEDFUNDS": flat(4.0),
        "ICSA": weekly,
        "DGS2": [(date(2026, 11, 10), 4.4), (date(2026, 11, 11), 4.5)],
    }


def make_store(path, **override):
    """Write a synthetic store; an override is a series -> rows list, or None to leave the series out."""
    series = default_series()
    for sid, rows in override.items():
        if rows is None:
            series.pop(sid)
        else:
            series[sid] = rows
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE raw_observations (series_id VARCHAR, date DATE, value DOUBLE, realtime_start DATE)")
    con.execute("CREATE TABLE other_table (a INTEGER)")
    con.execute("INSERT INTO other_table VALUES (1)")
    rows = [(sid, d, v, d) for sid, part in series.items() for d, v in part]
    if rows:
        con.executemany("INSERT INTO raw_observations VALUES (?, ?, ?, ?)", rows)
    con.close()


def build(tmp_path, now=NOW, **override):
    make_store(tmp_path / "s.duckdb", **override)
    return es.build_economy_state(tmp_path / "s.duckdb", now)


# --------------------------------------------------------------------------- rate_m and gaps


def test_rate_m_hand_arithmetic():
    x = {0: 100.0, 3: 100.25, 6: 101.0, 12: 103.6}
    assert es.rate_m(x, 12, 12) == pytest.approx(3.6)
    assert es.rate_m(x, 6, 6) == pytest.approx((1.01**2 - 1) * 100)
    assert es.rate_m(x, 3, 3) == pytest.approx((1.0025**4 - 1) * 100)
    assert es.rate_m(x, 12, 6) == pytest.approx(((103.6 / 101.0) ** 2 - 1) * 100)


def test_rate_m_missing_end_is_none():
    x = {0: 100.0, 12: 103.6}
    assert es.rate_m(x, 12, 6) is None  # month 6 absent
    assert es.rate_m(x, 13, 12) is None  # month 13 absent
    assert es.rate_m({0: 0.0, 12: 1.0}, 12, 12) is None


def test_missing_month_nulls_only_what_needs_it(tmp_path):
    rows = [(d, v) for d, v in grow(lambda i: 3.0) if d != date(2026, 4, 1)]
    doc = build(tmp_path, CPIAUCSL=rows)
    cpi = doc["inflation"]["series"]["CPIAUCSL"]
    assert cpi["rate_12"]["month"] == "2026-10" and cpi["rate_12"]["value"] == pytest.approx(3.0)
    assert cpi["rate_3"]["month"] == "2026-10"
    assert cpi["rate_6"]["month"] == "2026-09"  # October's six-month base (April) is missing; September's is not
    assert cpi["rate_6"]["value"] == pytest.approx(3.0)
    assert cpi["gap_6_minus_12"]["month"] == "2026-09"


# --------------------------------------------------------------------------- the words


@pytest.mark.parametrize(
    "rate,word",
    [
        (None, None),
        (-1.0, "below target"),
        (1.4999, "below target"),
        (1.5, "near target"),
        (2.5, "near target"),
        (2.5001, "above target"),
        (3.5, "above target"),
        (3.5001, "well above target"),
    ],
)
def test_inflation_level_boundaries(rate, word):
    assert es.inflation_level(rate) == word


@pytest.mark.parametrize(
    "gap,word", [(None, None), (0.5, "rising"), (0.4999, "steady"), (0.0, "steady"), (-0.4999, "steady"), (-0.5, "falling")]
)
def test_direction_word_boundaries(gap, word):
    assert es.direction_word(gap) == word


@pytest.mark.parametrize(
    "change,word",
    [(None, None), (0.125, "tightening"), (0.12, "on hold"), (0.0, "on hold"), (-0.12, "on hold"), (-0.125, "easing")],
)
def test_policy_direction_boundaries(change, word):
    assert es.policy_direction(change) == word


@pytest.mark.parametrize(
    "gap,word",
    [(None, None), (-0.25, "prices cuts"), (-0.2499, None), (0.0, None), (0.9, None), (1.4999, None), (1.5, "prices hikes")],
)
def test_market_word_boundaries(gap, word):
    assert es.market_word(gap) == word


@pytest.mark.parametrize(
    "rise,word",
    [
        (None, None),
        (-0.2, "steady"),
        (0.0, "steady"),
        (0.2999, "steady"),
        (0.3, "softening"),
        (0.1 + 0.2, "softening"),  # 0.30000000000000004
        (4.3 - 4.0, "softening"),  # 0.2999999999999998: arithmetic noise must not decide the word
        (0.4999, "softening"),
        (0.5, "deteriorating"),
        (4.6 - 4.1, "deteriorating"),  # 0.4999999999999991
    ],
)
def test_jobs_state_boundaries(rise, word):
    assert es.jobs_state(rise) == word


@pytest.mark.parametrize("rate,word", [(None, None), (0.0, "expanding"), (-0.0001, "contracting"), (1.0, "expanding")])
def test_growth_state_boundaries(rate, word):
    assert es.growth_state(rate) == word


@pytest.mark.parametrize(
    "pay,ip,word",
    [
        (0.5, 0.0, "accelerating"),
        (0.4999, 0.0, "steady"),
        (0.0, 1.0, "accelerating"),
        (0.0, 0.9999, "steady"),
        (-0.5, 0.0, "slowing"),
        (0.0, -1.0, "slowing"),
        (0.5, -1.0, "steady"),  # one up and one down
        (-0.5, 1.0, "steady"),
        (0.5, 1.0, "accelerating"),
        (-0.5, -1.0, "slowing"),
        (0.5, None, "accelerating"),
        (None, -1.0, "slowing"),
        (None, None, None),
    ],
)
def test_growth_direction_legs(pay, ip, word):
    assert es.growth_direction(pay, ip) == word


# --------------------------------------------------------------------------- the rise from the low


def test_rise_from_low_arithmetic():
    last = 2026 * 12 + 9
    u = {k: 4.0 for k in range(last - 20, last + 1)}
    for k in (last - 2, last - 1, last):
        u[k] = 4.6
    rise, month = es.rise_from_low(u)
    assert month == last
    assert rise == pytest.approx(0.6)  # current mean 4.6 against the lowest earlier 3-month mean, 4.0
    assert es.jobs_state(rise) == "deteriorating"


def test_rise_from_low_with_missing_months():
    last = 2026 * 12 + 9
    u = {k: 4.0 for k in range(last - 20, last + 1)}
    for k in (last - 2, last - 1, last):
        u[k] = 4.6
    del u[last - 1]  # two of the last three months remain: still formed
    assert es.rise_from_low(u)[0] == pytest.approx(0.6)
    del u[last - 2]  # one left: the current mean cannot be formed
    assert es.rise_from_low(u)[0] is None
    # too few earlier means: only 8 months of history before the current three
    short = {k: 4.0 for k in range(last - 9, last + 1)}
    assert es.rise_from_low(short)[0] is None
    # exactly enough: 9 earlier means exist
    enough = {k: 4.0 for k in range(last - 10, last + 1)}
    assert es.rise_from_low(enough)[0] == pytest.approx(0.0)
    assert es.rise_from_low({})[0] is None


def test_claims_facts():
    values = [100.0] * 60 + [110.0] * 4
    cur, pct = es.claims_facts(values)
    assert cur == 110.0 and pct == pytest.approx(10.0)
    cur, pct = es.claims_facts([100.0] * 54)  # 51 four-week means: no 52-week low
    assert cur == 100.0 and pct is None
    assert es.claims_facts([1.0, 2.0, 3.0]) == (None, None)


# --------------------------------------------------------------------------- the file


def test_default_store_reading(tmp_path):
    doc = build(tmp_path)
    assert doc["schema"] == "mri.economy_state.v1" and doc["degraded"] is False and doc["reasons"] == []
    assert doc["asof"] == "2026-11-11"
    inf = doc["inflation"]
    assert inf["level"] == "above target" and inf["direction"] == "steady" and inf["core_direction"] == "steady"
    assert inf["gap_to_target"]["value"] == pytest.approx(1.0)
    pol = doc["policy"]
    assert pol["direction"] == "on hold" and pol["market"] is None
    assert pol["funds_rate"] == {"value": 4.0, "month": "2026-10"}
    assert pol["change_6m"]["value"] == 0.0
    assert pol["real_rate"]["value"] == pytest.approx(1.0)
    assert pol["two_year_minus_funds"]["value"] == pytest.approx(0.5)
    assert pol["two_year_minus_funds"]["dgs2_date"] == "2026-11-11"
    jobs = doc["jobs"]
    assert jobs["state"] == "steady" and jobs["unemployment"]["value"] == 4.0
    assert jobs["rise_from_low"]["value"] == pytest.approx(0.0)
    pay = default_series()["PAYEMS"]
    assert jobs["payrolls_3m_average_change"]["value"] == pytest.approx((pay[-1][1] - pay[-4][1]) / 3.0)
    assert jobs["claims_4w"]["value"] == 200000.0 and jobs["claims_above_52w_low_pct"]["value"] == pytest.approx(0.0)
    gro = doc["growth"]
    assert gro["state"] == "expanding" and gro["direction"] == "steady"
    assert gro["payrolls"]["rate_12"]["value"] == pytest.approx(1.0)
    assert gro["industrial_production"]["rate_12"]["value"] == pytest.approx(2.0)
    assert "quadrant" not in json.dumps(doc["summary"]).lower()
    assert not any(k in doc for k in ("quadrant", "regime", "label"))


def test_payrolls_average_change_is_in_thousands_per_month(tmp_path):
    rows = [(month_date(END - N + 1 + i), 150000.0 + 100.0 * i) for i in range(N)]
    doc = build(tmp_path, PAYEMS=rows)
    assert doc["jobs"]["payrolls_3m_average_change"] == {"value": 100.0, "month": "2026-10"}


def test_absent_core_series_is_not_an_error(tmp_path):
    doc = build(tmp_path, PCEPILFE=None, CPILFESL=None)
    inf = doc["inflation"]
    assert inf["core_direction"] is None and inf["level"] == "above target"
    assert inf["series"]["PCEPILFE"]["rate_12"] == {"value": None, "month": None}
    assert inf["series"]["CPILFESL"]["direction"] is None
    assert doc["degraded"] is False
    assert "PCEPILFE_missing: no observations in the store" in doc["reasons"]
    assert "core inflation" not in doc["summary"]


def test_absent_primary_series_nulls_its_axis_and_degrades(tmp_path):
    doc = build(tmp_path, FEDFUNDS=None)
    assert doc["policy"]["direction"] is None and doc["policy"]["funds_rate"]["value"] is None
    assert doc["policy"]["real_rate"]["value"] is None and doc["policy"]["two_year_minus_funds"]["value"] is None
    assert doc["degraded"] is True and "FEDFUNDS_missing: no observations in the store" in doc["reasons"]
    assert doc["inflation"]["level"] == "above target"  # the other axes stand
    assert "Policy: no reading." in doc["summary"]


def test_empty_store_is_degraded_without_error(tmp_path):
    doc = build(tmp_path, **{sid: None for sid in default_series()})
    assert doc["degraded"] is True and doc["asof"] is None
    assert doc["summary"] == "Inflation: no reading. Policy: no reading. Jobs: no reading. Growth: no reading."


def test_each_primary_word_missing_degrades(tmp_path):
    assert build(tmp_path, PCEPI=None)["degraded"] is True
    (tmp_path / "s.duckdb").unlink()
    assert build(tmp_path, UNRATE=None)["degraded"] is True
    (tmp_path / "s.duckdb").unlink()
    assert build(tmp_path, PAYEMS=None)["degraded"] is True
    (tmp_path / "s.duckdb").unlink()
    assert build(tmp_path, INDPRO=None)["degraded"] is False  # growth direction can use payrolls alone


@pytest.mark.parametrize(
    "sid,axis,words",
    [
        ("PCEPI", "inflation", [("inflation", "level"), ("inflation", "direction"), ("inflation", "core_direction")]),
        ("FEDFUNDS", "policy", [("policy", "direction"), ("policy", "market")]),
        ("UNRATE", "jobs", [("jobs", "state")]),
        ("PAYEMS", "growth", [("growth", "state"), ("growth", "direction")]),
    ],
)
def test_staleness_per_axis(tmp_path, sid, axis, words):
    old = [(d, v) for d, v in default_series()[sid] if d <= date(2026, 5, 1)]  # 165 days before the build day
    doc = build(tmp_path, **{sid: old})
    for a, w in words:
        assert doc[a][w] is None
    assert any(r.startswith(f"{axis}_stale:") for r in doc["reasons"])
    assert sum(r.split(":")[0].endswith("_stale") for r in doc["reasons"]) == 1
    assert doc["degraded"] is True
    assert doc["asof"] == "2026-11-11"  # the other series are current


def test_staleness_boundary_is_120_days(tmp_path):
    july = {sid: [(d, v) for d, v in rows if d <= date(2026, 7, 1)] for sid, rows in default_series().items() if sid not in ("ICSA", "DGS2")}
    now_ok = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)  # 2026-07-01 + 120 days
    now_stale = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
    doc = build(tmp_path, now=now_ok, **july)
    assert doc["degraded"] is False and not any("_stale" in r for r in doc["reasons"])
    doc = es.build_economy_state(tmp_path / "s.duckdb", now_stale)
    assert doc["degraded"] is True
    assert sorted(r.split(":")[0] for r in doc["reasons"] if "_stale" in r) == [
        "growth_stale", "inflation_stale", "jobs_stale", "policy_stale"
    ]


def test_stale_axis_keeps_numbers_and_summary_says_no_reading(tmp_path):
    old = [(d, v) for d, v in default_series()["UNRATE"] if d <= date(2026, 5, 1)]
    doc = build(tmp_path, UNRATE=old)
    assert doc["jobs"]["unemployment"] == {"value": 4.0, "month": "2026-05"}
    assert "Jobs: no reading." in doc["summary"]


def test_core_direction_and_market_word_in_the_summary(tmp_path):
    core = grow(lambda i: 2.0 if i < N - 6 else 6.0)  # last six months fast: rate_6 well above rate_12
    doc = build(tmp_path, PCEPILFE=core, DGS2=[(date(2026, 11, 11), 2.0)])
    assert doc["inflation"]["core_direction"] == "rising" and doc["inflation"]["direction"] == "steady"
    assert doc["policy"]["market"] == "prices cuts"
    assert ", with core inflation rising." in doc["summary"]
    assert "; the 2-year yield prices cuts." in doc["summary"]


def test_summary_sentence_for_a_constructed_case(tmp_path):
    funds = [(d, 3.5 if d < date(2026, 5, 1) else 3.75) for d, _ in flat(0.0)]  # 0.25 up over the six months to 2026-10
    pce = grow(lambda i: 4.0 if i < N - 6 else 4.8)
    doc = build(tmp_path, FEDFUNDS=funds, PCEPI=pce)
    p = doc["inflation"]["series"]["PCEPI"]
    r12, r6 = p["rate_12"]["value"], p["rate_6"]["value"]
    assert r6 == pytest.approx(4.8) and 4.3 < r12 < 4.5
    assert doc["summary"] == (
        f"Inflation well above target and steady (PCE prices {es._fmt(r12)}% on the year, +4.8% annualised over six months, "
        "to October). "
        f"Policy tightening: funds rate 3.75%, up 0.25 in six months, {abs(3.75 - r12):.1f} below inflation. "
        "Jobs steady: unemployment 4.0%, no rise from its 12-month low. "
        "Growth expanding and steady: payrolls +1.0% on the year."
    )


def test_summary_on_hold_and_above_inflation(tmp_path):
    doc = build(tmp_path)
    assert doc["summary"] == (
        "Inflation above target and steady (PCE prices +3.0% on the year, +3.0% annualised over six months, to October). "
        "Policy on hold: funds rate 4.00%, unchanged in six months, 1.0 above inflation. "
        "Jobs steady: unemployment 4.0%, no rise from its 12-month low. "
        "Growth expanding and steady: payrolls +1.0% on the year."
    )


# --------------------------------------------------------------------------- files, CLI, additivity, config


def test_write_archive_and_strict_json(tmp_path):
    make_store(tmp_path / "s.duckdb")
    main, archive = es.write_economy_state(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    assert main == tmp_path / "out" / "economy_state.json"
    assert archive == tmp_path / "out" / "archive" / "2026-11-12" / "economy_state" / "economy_state.json"
    text = main.read_text(encoding="utf-8")
    parsed = json.loads(text, parse_constant=_reject)
    assert parsed["schema"] == "mri.economy_state.v1"
    assert archive.read_text(encoding="utf-8") == text


def test_no_nan_in_a_degraded_file(tmp_path):
    make_store(tmp_path / "s.duckdb", PCEPILFE=None, FEDFUNDS=None, UNRATE=flat(4.0, 3))
    main, _ = es.write_economy_state(tmp_path / "s.duckdb", tmp_path / "out", NOW)
    parsed = json.loads(main.read_text(encoding="utf-8"), parse_constant=_reject)
    assert parsed["degraded"] is True


def test_cli_command_registered(tmp_path):
    from typer.testing import CliRunner

    from macro_engine.cli import app

    make_store(tmp_path / "s.duckdb")
    res = CliRunner().invoke(
        app, ["write-economy-state", "--db-path", str(tmp_path / "s.duckdb"), "--outputs-dir", str(tmp_path / "o")]
    )
    assert res.exit_code == 0, res.output
    assert (tmp_path / "o" / "economy_state.json").exists()


def test_building_changes_no_table_or_file(tmp_path):
    make_store(tmp_path / "s.duckdb")
    before_bytes = (tmp_path / "s.duckdb").read_bytes()
    files = sorted(p.name for p in tmp_path.iterdir())
    es.build_economy_state(tmp_path / "s.duckdb", NOW)
    assert (tmp_path / "s.duckdb").read_bytes() == before_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == files


def test_config_loads_with_the_two_new_sources():
    from macro_engine.dimensions.config import load_dimension_config
    from macro_engine.ingest.registry import load_ingestion_sources
    from macro_engine.regimes.config import load_regime_config

    sources = {s.series_id: s for s in load_ingestion_sources("config/phase_b_sources.yaml")}
    for sid, (stale, unusable, lag) in {"PCEPILFE": (103, 120, 58), "CPILFESL": (45, 120, 44)}.items():
        s = sources[sid]
        assert (s.provider, s.dimension, s.frequency) == ("FRED", "economy_state", "monthly")
        assert (s.required, s.enabled) == (False, True)
        assert (s.stale_after_days, s.unusable_after_days, s.publication_lag_days) == (stale, unusable, lag)
    # the same lags as the series they mirror
    for core, headline in (("PCEPILFE", "PCEPI"), ("CPILFESL", "CPIAUCSL")):
        assert sources[core].unusable_after_days == sources[headline].unusable_after_days
        assert sources[core].publication_lag_days == sources[headline].publication_lag_days
    dims = load_dimension_config("config/phase_b_sources.yaml")
    assert not {f.series_id for f in dims.features} & {"PCEPILFE", "CPILFESL"}
    assert not {s for d in dims.dimensions for s in map(str, d.model_dump().values())} & {"PCEPILFE", "CPILFESL"}
    load_regime_config("config/phase_b_sources.yaml")
