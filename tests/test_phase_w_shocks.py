"""Tests for the S4.4 shock register (MRI-12): severity classification, the §3.4 state
machine, the register builder, the §3.5 narrative attachment, and the T3 risk flag.

Synthetic series only in the DuckDB-backed tests -- never reads the live store.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine.shocks.config import compute_taxonomy_version, load_shocks_config
from macro_engine.shocks.narrative import attach_narrative
from macro_engine.shocks.register import (
    _run_single_leg_shock,
    build_shock_register_artifact,
    build_shock_register_history,
)
from macro_engine.shocks.severity import compute_severity, is_retired
from macro_engine.shocks.state_machine import (
    ACTIVE,
    DECAYING,
    INACTIVE,
    RETIRED_SUNSET,
    R_DAYS,
    ShockEpisodeState,
    is_active,
    step,
)
from macro_engine.storage.duckdb_store import DuckDBStore

CONFIG_PATH = "config/shocks.yaml"


# -----------------------------------------------------------------------------
# severity.py
# -----------------------------------------------------------------------------


def test_compute_severity_unsigned():
    thresholds = {"severity_1": 30.0, "severity_2": 40.0}
    assert compute_severity(29.9, thresholds, signed=False).severity == 0
    result = compute_severity(30.0, thresholds, signed=False)
    assert (result.severity, result.direction) == (1, "up")
    assert compute_severity(40.0, thresholds, signed=False).severity == 2
    assert compute_severity(None, thresholds, signed=False).severity == 0


def test_compute_severity_signed_both_sides():
    thresholds = {
        "severity_1_up": 30.0,
        "severity_2_up": 40.0,
        "severity_1_down": -30.0,
        "severity_2_down": -40.0,
    }
    up = compute_severity(35.0, thresholds, signed=True)
    assert (up.severity, up.direction) == (1, "up")
    down = compute_severity(-45.0, thresholds, signed=True)
    assert (down.severity, down.direction) == (2, "down")
    neutral = compute_severity(0.0, thresholds, signed=True)
    assert (neutral.severity, neutral.direction) == (0, "none")


def test_is_retired_unsigned_and_signed():
    assert is_retired(19.9, 20.0, signed=False)
    assert not is_retired(20.0, 20.0, signed=False)
    assert is_retired(2.4, 2.5, signed=True)
    assert is_retired(-2.5, 2.5, signed=True)
    assert not is_retired(2.6, 2.5, signed=True)
    assert not is_retired(None, 2.5, signed=True)


# -----------------------------------------------------------------------------
# state_machine.py
# -----------------------------------------------------------------------------


def _run_days(specs, sunset_days: int = 180):
    """`specs` is a list of `(severity, retired_today, intensity)`. Returns the list of
    `ShockEpisodeState` after each day, one trading day apart starting 2020-01-01."""
    state = ShockEpisodeState()
    states = []
    today = date(2020, 1, 1)
    for severity, retired_today, intensity in specs:
        state = step(
            state,
            today=today,
            severity=severity,
            retired_today=retired_today,
            intensity=intensity,
            sunset_days=sunset_days,
        )
        states.append(state)
        today = today + timedelta(days=1)
    return states


def test_inactive_to_active_on_first_severity():
    states = _run_days([(0, False, None), (1, False, 1.1)])
    assert states[0].state == INACTIVE
    assert states[1].state == ACTIVE
    assert states[1].onset_date == date(2020, 1, 2)


def test_active_to_decaying_after_r_consecutive_zero_days():
    # Onset + (R_DAYS - 1) zero-severity days: still inside the grace window.
    specs = [(1, False, 1.5)] + [(0, False, 0.5)] * (R_DAYS - 1)
    states = _run_days(specs)
    assert all(s.state == ACTIVE for s in states)
    assert all(is_active(s) for s in states)  # the whole point of R_DAYS: active through the grace window

    # One more zero-severity day reaches R_DAYS consecutive zeros -> DECAYING.
    states2 = _run_days(specs + [(0, False, 0.4)])
    assert states2[-1].state == DECAYING
    assert states2[-1].onset_date == date(2020, 1, 1)  # unchanged -- same episode


def test_decaying_to_active_is_same_episode_reactivation():
    specs = [(1, False, 1.5)] + [(0, False, 0.5)] * R_DAYS  # -> DECAYING
    specs.append((1, False, 1.2))  # re-fires while decaying
    states = _run_days(specs)
    assert states[-2].state == DECAYING
    assert states[-1].state == ACTIVE
    assert states[-1].onset_date == date(2020, 1, 1)  # same episode, not a new onset


def test_decaying_to_inactive_closes_episode_after_r_retired_days():
    specs = [(1, False, 1.5)] + [(0, False, 0.5)] * R_DAYS  # -> DECAYING
    specs += [(0, True, 0.1)] * R_DAYS  # R consecutive retired days
    states = _run_days(specs)
    assert states[-1].state == INACTIVE
    assert states[-1].onset_date is None


def test_sunset_after_age_exceeds_sunset_days():
    sunset_days = 5
    specs = [(1, False, 2.0)] * 8  # keeps firing past the sunset horizon
    states = _run_days(specs, sunset_days=sunset_days)
    for i in range(6):  # ages 0..5: within the horizon
        assert states[i].state == ACTIVE
        assert states[i].onset_date == date(2020, 1, 1)
    # age 6 > sunset_days(5): "any active/decaying -- age > sunset_days --> retired_sunset"
    assert states[6].state == RETIRED_SUNSET
    assert states[6].sunset_flagged_on == date(2020, 1, 1) + timedelta(days=6)
    # still firing severity >= 1: a fresh episode reopens the same day (state_machine.py's
    # module docstring -- retired_sunset is a disposition on the closed episode, not a
    # life sentence on the shock).
    assert states[7].state == ACTIVE
    assert states[7].onset_date == date(2020, 1, 1) + timedelta(days=7)


def test_retired_sunset_closes_when_the_shock_quiets_down():
    sunset_days = 3
    specs = [(1, False, 2.0)] * 5  # ages 0..4; sunset at age 4
    specs += [(0, True, 0.1)]  # quiet AND inside the retire band the same day
    specs += [(0, False, 0.2)]  # stays quiet
    states = _run_days(specs, sunset_days=sunset_days)
    assert states[3].state == ACTIVE  # age 3, not yet > 3
    assert states[4].state == RETIRED_SUNSET  # age 4 > 3
    assert states[5].state == INACTIVE  # retired_today and severity 0 -> episode closes
    assert states[5].onset_date is None
    assert states[6].state == INACTIVE  # nothing re-fires it


def test_is_active_true_in_active_and_decaying_only():
    assert is_active(ShockEpisodeState(state=ACTIVE))
    assert is_active(ShockEpisodeState(state=DECAYING))
    assert not is_active(ShockEpisodeState(state=INACTIVE))
    assert not is_active(ShockEpisodeState(state=RETIRED_SUNSET))


# -----------------------------------------------------------------------------
# register.py -- forced-null-reason and stale_input, exercised directly against
# `_run_single_leg_shock` (isolates the mechanism from the dollar/oil ingest specifics,
# which are covered by the end-to-end test below and by tests/test_measure_shocks.py).
# -----------------------------------------------------------------------------


def test_null_before_start_date_carries_the_forced_reason():
    calendar = pd.date_range("2002-12-30", periods=5, freq="B")
    values = pd.Series([np.nan, np.nan, 10.0, 12.0, 11.0], index=calendar)
    value_dates = pd.Series([pd.NaT, pd.NaT, calendar[2], calendar[3], calendar[4]], index=calendar)
    forced = {
        calendar[0].date(): "no_daily_breakeven_before_2003",
        calendar[1].date(): "no_daily_breakeven_before_2003",
    }
    thresholds = {
        "severity_1_up": 45.0, "severity_2_up": 55.0,
        "severity_1_down": -45.0, "severity_2_down": -60.0,
    }
    frame = _run_single_leg_shock(
        shock_id="inflation_shock",
        calendar=calendar,
        values=values,
        value_dates=value_dates,
        thresholds=thresholds,
        signed=True,
        retire_level=20.0,
        sunset_days=120,
        series_id="T10YIE",
        measure_label="63-trading-day change, bp, signed",
        forced_reason=forced,
    )
    assert pd.isna(frame.iloc[0]["value"])
    assert frame.iloc[0]["reason"] == "no_daily_breakeven_before_2003"
    assert frame.iloc[0]["severity"] == 0
    assert frame.iloc[0]["direction"] == "none"
    assert frame.iloc[1]["reason"] == "no_daily_breakeven_before_2003"
    assert frame.iloc[2]["value"] == 10.0
    assert pd.isna(frame.iloc[2]["reason"])


def test_stale_input_flag_beyond_five_trading_days():
    calendar = pd.date_range("2020-01-01", periods=10, freq="B")
    # DCOILWTICO observed only on day 0, forward-filled for the rest -- never a guessed
    # value, just a repeated real one, which is what `stale_input` exists to flag.
    values = pd.Series([50.0] * 10, index=calendar)
    value_dates = pd.Series([calendar[0]] * 10, index=calendar)
    gap = pd.Series(range(10), index=calendar)  # trading-day gap == position index here
    thresholds = {
        "severity_1_up": 30.0, "severity_2_up": 40.0,
        "severity_1_down": -30.0, "severity_2_down": -40.0,
    }
    frame = _run_single_leg_shock(
        shock_id="oil_shock",
        calendar=calendar,
        values=values,
        value_dates=value_dates,
        thresholds=thresholds,
        signed=True,
        retire_level=10.0,
        sunset_days=180,
        series_id="DCOILWTICO",
        measure_label="63-trading-day log change, %, signed",
        stale_gap=gap,
    )
    assert not bool(frame.iloc[5]["stale_input"])  # gap 5, not > 5
    assert bool(frame.iloc[6]["stale_input"])  # gap 6 > 5


# -----------------------------------------------------------------------------
# End-to-end: a synthetic DuckDB store, the full register history, the artifact, the
# risk flag, and narrative byte-identity.
# -----------------------------------------------------------------------------


def _build_synthetic_store(tmp_path) -> str:
    calendar = pd.bdate_range("2015-01-01", "2016-06-30")
    n = len(calendar)

    vix = np.full(n, 15.0)
    spike_start, spike_end = 200, 230  # a clean VIX >= 30 episode, well clear of both ends
    vix[spike_start:spike_end] = 35.0
    baa10y = np.linspace(2.0, 2.3, n)  # slow drift -- no 63-day shock on any of these
    dfii10 = np.linspace(0.3, 0.5, n)
    dgs2 = np.linspace(0.6, 0.8, n)
    dcoil = np.linspace(50.0, 55.0, n)
    dtwexbgs = np.linspace(90.0, 92.0, n)
    dtwexb = dtwexbgs.copy()  # identical series -> stitch mean_shift ~ 0, no discontinuity
    t10yie = np.linspace(1.5, 1.7, n)

    def rows(series_id: str, dates, values, **extra) -> pd.DataFrame:
        payload = {
            "series_id": series_id,
            "date": pd.DatetimeIndex(dates),
            "value": np.asarray(values, dtype=float),
            "realtime_start": pd.DatetimeIndex(dates),
            "realtime_end": pd.DatetimeIndex(dates),
            "source": "FRED",
            "fetched_at": pd.Timestamp.now(tz="UTC"),
            "frequency": "daily",
            "units": "pct",
        }
        payload.update(extra)
        return pd.DataFrame(payload)

    frames = [
        rows("VIXCLS", calendar, vix),
        rows("BAA10Y", calendar, baa10y),
        rows("DFII10", calendar, dfii10),
        rows("DGS2", calendar, dgs2),
        rows("DCOILWTICO", calendar, dcoil),
        rows("DTWEXBGS", calendar, dtwexbgs),
        rows("DTWEXB", calendar, dtwexb),
        rows("T10YIE", calendar, t10yie),
    ]
    # Two years of weekly history before the window so claims_spike_pct's 52-week
    # rolling minimum is already warmed up by 2015-01-01.
    icsa_dates = pd.date_range("2013-01-05", "2016-06-30", freq="W-SAT")
    frames.append(rows("ICSA", icsa_dates, np.full(len(icsa_dates), 300000.0), frequency="weekly"))

    all_obs = pd.concat(frames, ignore_index=True)

    db_path = tmp_path / "shocks_fixture.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    with duckdb.connect(str(db_path)) as con:
        con.register("obs_frame", all_obs)
        con.execute("INSERT INTO raw_observations SELECT * FROM obs_frame")
    return str(db_path)


@pytest.fixture(scope="module")
def register_fixture(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("shocks_fixture")
    db_path = _build_synthetic_store(tmp_path)
    config = load_shocks_config(CONFIG_PATH)
    con = duckdb.connect(db_path, read_only=True)
    try:
        history = build_shock_register_history(
            con, config, start_date="2015-01-01", end_date="2016-06-30"
        )
    finally:
        con.close()
    return {"db_path": db_path, "config": config, "history": history}


def test_volatility_shock_fires_during_the_planted_spike(register_fixture):
    history = register_fixture["history"]
    vol = history[history["shock_id"] == "volatility_shock"].reset_index(drop=True)
    fired = vol.iloc[200:230]
    assert (fired["severity"] >= 1).all()
    assert (fired["value"] == 35.0).all()
    quiet = vol.iloc[:190]
    assert (quiet["severity"] == 0).all()


def test_taxonomy_version_is_stable_and_matches_the_config_hash(register_fixture):
    config = register_fixture["config"]
    assert config["taxonomy_version"] == compute_taxonomy_version(CONFIG_PATH)
    assert compute_taxonomy_version(CONFIG_PATH) == compute_taxonomy_version(CONFIG_PATH)


def test_risk_flag_active_on_a_fired_date_inactive_on_a_quiet_date(register_fixture):
    history = register_fixture["history"]
    config = register_fixture["config"]
    vol = history[history["shock_id"] == "volatility_shock"].reset_index(drop=True)
    fired_date = vol.iloc[205]["date"]
    quiet_date = vol.iloc[10]["date"]

    payload_fired = build_shock_register_artifact(history, config, fired_date)
    payload_quiet = build_shock_register_artifact(history, config, quiet_date)

    def _flag(payload):
        return next(f for f in payload["risk_flags"] if f["id"] == "elevated_volatility_ahead")

    assert _flag(payload_fired)["active"] is True
    assert _flag(payload_quiet)["active"] is False
    # The effect numbers are read from config, not hard-coded in the risk-flag function.
    effect = _flag(payload_fired)["measured_effect"]
    assert effect["delta_points"] == 10.6
    assert effect["nw_t"] == 4.57
    assert effect["n_months"] == 417
    assert effect["half_sample_deltas"] == [4.9, 12.6]
    assert effect["study"] == "MRI_S4 impact T3"


def test_active_shocks_on_date_matches_the_register(register_fixture):
    from macro_engine.reports.writer import _active_shocks_for_date

    history = register_fixture["history"]
    vol = history[history["shock_id"] == "volatility_shock"].reset_index(drop=True)
    fired_date = vol.iloc[205]["date"]
    ids, reason = _active_shocks_for_date(history, fired_date)
    assert reason is None
    assert "volatility_shock" in ids
    assert ids == sorted(ids)


def test_narrative_attachment_is_byte_identical_on_every_other_field(register_fixture):
    history = register_fixture["history"]
    config = register_fixture["config"]
    as_of = pd.Timestamp(history["date"].max()).date()
    payload = build_shock_register_artifact(history, config, as_of)
    payload_before = copy.deepcopy(payload)

    news_items = pd.DataFrame(
        {
            "news_id": ["n1", "n2"],
            "published_at": [
                pd.Timestamp(as_of) - pd.Timedelta(days=2),
                pd.Timestamp(as_of) - pd.Timedelta(days=5),
            ],
        }
    )
    theme_scores = pd.DataFrame(
        {
            "news_id": ["n1", "n2"],
            "theme_id": ["financial_stability_risk", "geopolitical_risk"],
            "direction": ["positive", "negative"],
            "severity": [0.6, 0.4],
            "confidence": [0.8, 0.7],
            "time_horizon": ["near_term", "near_term"],
        }
    )
    classifications = pd.DataFrame(
        {"news_id": ["n1", "n2"], "summary": ["Vol summary one.", "Vol summary two."]}
    )

    with_narrative = attach_narrative(
        payload,
        news_items=news_items,
        news_theme_scores=theme_scores,
        news_classifications=classifications,
        shock_theme_map=config.get("narrative_themes", {}),
        known_theme_ids={"financial_stability_risk", "geopolitical_risk"},
        as_of=as_of,
    )

    # attach_narrative actually attached something -- otherwise this test would pass
    # vacuously.
    vol_row = next(r for r in with_narrative["shocks"] if r["shock_id"] == "volatility_shock")
    assert vol_row.get("narrative") is not None
    assert vol_row["narrative"]["item_count"] == 2

    numeric_fields = [
        "shock_id", "series_id", "measure", "value", "value_date", "direction",
        "intensity", "severity", "active", "state", "onset_date", "age_days",
        "peak_intensity", "peak_date", "thresholds", "proxy", "stale_input",
        "values_revised", "reason",
    ]
    before_by_id = {row["shock_id"]: row for row in payload_before["shocks"]}
    after_by_id = {row["shock_id"]: row for row in with_narrative["shocks"]}
    assert set(before_by_id) == set(after_by_id)
    for shock_id, before_row in before_by_id.items():
        after_row = after_by_id[shock_id]
        for field in numeric_fields:
            assert before_row[field] == after_row[field], (shock_id, field)
    assert payload_before["risk_flags"] == with_narrative["risk_flags"]
    assert payload_before["taxonomy_version"] == with_narrative["taxonomy_version"]
    assert payload_before["asof"] == with_narrative["asof"]

    # attach_narrative must not mutate its input.
    assert "narrative" not in payload["shocks"][0] or payload["shocks"][0] == payload_before["shocks"][0]
