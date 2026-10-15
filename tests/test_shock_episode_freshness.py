"""2026-10-15: shock episode memory, per-source freshness, and the oil shock's daily tail.

The operator ruled that a week of FRED lag is not acceptable for oil, so the oil shock chains
Yahoo CL=F daily returns onto the last FRED spot level until FRED publishes. These tests pin:
the episode fields that keep an active shock's direction and peak after today's reading drops
under the trigger, the cadence-aware `stale_input`, the tail arithmetic and its provenance,
and the `risk_flags` contract the rs2 live runner reads (depth_pipeline.risk_flag_active).

Synthetic series only -- never reads the live store, never touches the network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine.ingest.yahoo_oil import parse_closed_bars
from macro_engine.shocks.config import load_shocks_config
from macro_engine.shocks.register import (
    OIL_TAIL_SOURCE,
    _oil_levels_with_tail,
    _run_single_leg_shock,
    build_shock_register_artifact,
    build_shock_register_history,
)
from macro_engine.shocks.service import build_and_write_shock_register
from macro_engine.shocks.state_machine import ACTIVE, DECAYING, INACTIVE, R_DAYS
from macro_engine.storage.duckdb_store import DuckDBStore

CONFIG_PATH = "config/shocks.yaml"
OIL_THRESHOLDS = {
    "severity_1_up": 30.0, "severity_2_up": 40.0,
    "severity_1_down": -30.0, "severity_2_down": -40.0,
}


def _run_oil(values, *, gap=None, max_lag=3, tail_after=None, value_dates=None):
    calendar = pd.bdate_range("2020-01-01", periods=len(values))
    vals = pd.Series(values, index=calendar, dtype=float)
    vdates = value_dates if value_dates is not None else pd.Series(calendar, index=calendar)
    return _run_single_leg_shock(
        shock_id="oil_shock",
        calendar=calendar,
        values=vals,
        value_dates=vdates,
        thresholds=OIL_THRESHOLDS,
        signed=True,
        retire_level=10.0,
        sunset_days=180,
        series_id="DCOILWTICO",
        measure_label="63-trading-day log change, %, signed",
        stale_gap=gap,
        max_lag_trading_days=max_lag,
        tail_after=tail_after,
    )


# -----------------------------------------------------------------------------
# Episode memory
# -----------------------------------------------------------------------------


def test_active_shock_keeps_its_direction_and_peak_after_dropping_under_the_trigger():
    # quiet, a +35 crossing, a +45 peak, then 20 (under 30, above the 10 retire line).
    values = [0.0, 0.0, 35.0, 45.0] + [20.0] * (R_DAYS + 3)
    frame = _run_oil(values)
    after = frame.iloc[5]
    assert after["state"] == ACTIVE
    assert after["severity"] == 0 and after["direction"] == "none"  # today's reading, unchanged
    assert after["episode_direction"] == "up"
    assert after["peak_severity"] == 2
    assert after["peak_value"] == 45.0
    decaying = frame[frame["state"] == DECAYING].iloc[0]
    assert decaying["episode_direction"] == "up" and decaying["peak_severity"] == 2
    quiet = frame.iloc[0]
    assert quiet["state"] == INACTIVE
    assert pd.isna(quiet["episode_direction"]) and pd.isna(quiet["peak_severity"]) and pd.isna(quiet["peak_value"])


def test_down_episode_carries_down_and_the_signed_peak():
    frame = _run_oil([0.0, -35.0, -42.0, -20.0, -20.0])
    last = frame.iloc[-1]
    assert last["severity"] == 0
    assert last["episode_direction"] == "down"
    assert last["peak_severity"] == 2
    assert last["peak_value"] == -42.0


def test_episode_fields_clear_when_the_episode_closes():
    values = [35.0] + [5.0] * (2 * R_DAYS + 2)  # fires once, then sits inside the retire band
    frame = _run_oil(values)
    closed = frame[frame["state"] == INACTIVE]
    assert not closed.empty
    first_closed = closed.iloc[0]
    assert pd.isna(first_closed["episode_direction"])
    assert pd.isna(first_closed["peak_severity"]) and pd.isna(first_closed["peak_value"])


# -----------------------------------------------------------------------------
# Freshness
# -----------------------------------------------------------------------------


def test_stale_input_uses_the_per_source_lag_limit_and_records_the_lag():
    values = [5.0] * 6
    gap = pd.Series(range(6), index=pd.bdate_range("2020-01-01", periods=6))
    frame = _run_oil(values, gap=gap, max_lag=3)
    assert list(frame["lag_trading_days"]) == [0, 1, 2, 3, 4, 5]
    assert not bool(frame.iloc[3]["stale_input"])  # lag 3, not > 3
    assert bool(frame.iloc[4]["stale_input"])


def test_config_carries_a_lag_limit_for_every_shock():
    config = load_shocks_config(CONFIG_PATH)
    for shock_id, cfg in config["shocks"].items():
        assert isinstance(cfg.get("max_lag_trading_days"), int), shock_id
    assert config["shocks"]["oil_shock"]["max_lag_trading_days"] == 3
    assert config["shocks"]["oil_shock"]["daily_tail"]["symbol"] == "CL=F"


# -----------------------------------------------------------------------------
# Oil tail arithmetic
# -----------------------------------------------------------------------------


def test_tail_chains_returns_onto_the_last_fred_level_never_levels():
    d = pd.bdate_range("2026-10-01", periods=4)
    fred = pd.Series([95.0, 96.0], index=d[:2])
    # futures sit ~7% under spot; only their move after the anchor date may carry over
    market = pd.Series([89.0, 90.0, 99.0, 81.0], index=d)
    levels, d0, reason = _oil_levels_with_tail(fred, market, max_anchor_gap_days=4)
    assert reason is None and d0 == d[1]
    assert levels.loc[d[1]] == 96.0  # FRED kept as is
    assert levels.loc[d[2]] == pytest.approx(96.0 * 99.0 / 90.0)
    assert levels.loc[d[3]] == pytest.approx(96.0 * 81.0 / 90.0)


def test_tail_refuses_without_a_recent_anchor_close():
    fred = pd.Series([96.0], index=[pd.Timestamp("2026-10-06")])
    market = pd.Series([80.0, 90.0], index=pd.to_datetime(["2026-09-25", "2026-10-08"]))
    levels, d0, reason = _oil_levels_with_tail(fred, market, max_anchor_gap_days=4)
    assert d0 is None and reason == "oil_tail_unavailable:no_anchor_close"
    assert levels.equals(fred)
    _, d0, reason = _oil_levels_with_tail(fred, pd.Series(dtype=float), max_anchor_gap_days=4)
    assert d0 is None and reason == "oil_tail_unavailable:no_market_closes"


# -----------------------------------------------------------------------------
# Yahoo payload parsing
# -----------------------------------------------------------------------------


def _chart_payload(dates_closes):
    ts = [int(pd.Timestamp(f"{d} 00:00").tz_localize("America/New_York").timestamp()) for d, _ in dates_closes]
    return json.dumps({"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "timestamp": ts,
        "indicators": {"quote": [{"close": [c for _, c in dates_closes]}]},
    }], "error": None}}).encode()


def test_parser_keeps_only_closed_sessions_and_drops_null_closes():
    payload = _chart_payload([("2026-10-12", 93.74), ("2026-10-13", None), ("2026-10-14", 94.28)])
    # 16:30 New York on 10-14: the 10-14 session (closes 17:00) is still open
    before_close = datetime(2026, 10, 14, 20, 30, tzinfo=timezone.utc)
    frame = parse_closed_bars(payload, "CL=F", now=before_close)
    assert [str(d) for d in frame["date"]] == ["2026-10-12"]
    after_close = datetime(2026, 10, 14, 21, 31, tzinfo=timezone.utc)
    frame = parse_closed_bars(payload, "CL=F", now=after_close)
    assert [str(d) for d in frame["date"]] == ["2026-10-12", "2026-10-14"]
    assert frame["payload_sha256"].nunique() == 1 and len(frame["payload_sha256"].iloc[0]) == 64
    assert set(frame["source"]) == {"yahoo_chart_v8"}


# -----------------------------------------------------------------------------
# End to end on a synthetic store
# -----------------------------------------------------------------------------

VIX_END = pd.Timestamp("2016-06-30")
FRED_OIL_END = pd.Timestamp("2016-06-22")  # FRED oil a week behind, as on 2026-10-15


def _rows(series_id, dates, values, frequency="daily"):
    dates = pd.DatetimeIndex(dates)
    return pd.DataFrame({
        "series_id": series_id, "date": dates, "value": np.asarray(values, dtype=float),
        "realtime_start": dates, "realtime_end": dates, "source": "FRED",
        "fetched_at": pd.Timestamp.now(tz="UTC"), "frequency": frequency, "units": "x",
    })


def _store(tmp_path, *, with_market=True, vix_spike=True):
    cal = pd.bdate_range("2015-01-01", VIX_END)
    n = len(cal)
    vix = np.full(n, 15.0)
    if vix_spike:
        vix[-3:] = 35.0  # volatility shock fires at the end of the window
    oil_cal = cal[cal <= FRED_OIL_END]
    frames = [
        _rows("VIXCLS", cal, vix),
        _rows("BAA10Y", cal, np.linspace(2.0, 2.3, n)),
        _rows("DFII10", cal, np.linspace(0.3, 0.5, n)),
        _rows("DGS2", cal, np.linspace(0.6, 0.8, n)),
        _rows("DCOILWTICO", oil_cal, np.linspace(50.0, 55.0, len(oil_cal))),
        _rows("DTWEXBGS", cal, np.linspace(90.0, 92.0, n)),
        _rows("DTWEXB", cal, np.linspace(90.0, 92.0, n)),
        _rows("T10YIE", cal, np.linspace(1.5, 1.7, n)),
    ]
    icsa = pd.date_range("2013-01-05", "2016-06-30", freq="W-SAT")
    frames.append(_rows("ICSA", icsa, np.full(len(icsa), 300000.0), frequency="weekly"))
    db_path = tmp_path / "shock_tail.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    with duckdb.connect(str(db_path)) as con:
        con.register("obs", pd.concat(frames, ignore_index=True))
        con.execute("INSERT INTO raw_observations SELECT * FROM obs")
    if with_market:
        mdates = pd.bdate_range(FRED_OIL_END - pd.Timedelta(days=10), VIX_END + pd.Timedelta(days=2))
        store.upsert_market_observations(pd.DataFrame({
            "series_id": "CL=F", "date": mdates.date, "value": np.linspace(45.0, 60.0, len(mdates)),
            "source": "yahoo_chart_v8", "fetched_at": pd.Timestamp("2016-07-02"), "payload_sha256": "0" * 64,
        }))
    return str(db_path), store


def _history(db_path):
    config = load_shocks_config(CONFIG_PATH)
    con = duckdb.connect(db_path, read_only=True)
    try:
        return config, build_shock_register_history(con, config, start_date="2015-01-01")
    finally:
        con.close()


def test_tail_extends_the_calendar_and_marks_its_rows_provisional(tmp_path):
    db_path, _ = _store(tmp_path)
    _, history = _history(db_path)
    last_date = history["date"].max()
    assert pd.Timestamp(last_date) == pd.Timestamp("2016-07-01")  # the market day past VIX's last date
    oil = history[history["shock_id"] == "oil_shock"].set_index("date")
    tail_rows = oil[[pd.Timestamp(d) > FRED_OIL_END for d in oil.index]]
    assert not tail_rows.empty
    assert set(tail_rows["value_source"]) == {OIL_TAIL_SOURCE}
    assert tail_rows["provisional"].all()
    assert (tail_rows["lag_trading_days"] == 0).all() and not tail_rows["stale_input"].any()
    fred_row = oil.loc[FRED_OIL_END.date()]
    assert fred_row["value_source"] == "fred" and not fred_row["provisional"]
    vix_last = history[(history["shock_id"] == "volatility_shock") & (history["date"] == last_date)].iloc[0]
    assert vix_last["lag_trading_days"] == 1 and not vix_last["stale_input"]  # forward-filled, within limit


def test_without_market_closes_oil_falls_back_to_fred_flagged_stale(tmp_path):
    db_path, _ = _store(tmp_path, with_market=False)
    _, history = _history(db_path)
    assert pd.Timestamp(history["date"].max()) == VIX_END  # no extension
    oil_last = history[(history["shock_id"] == "oil_shock") & (history["date"] == VIX_END.date())].iloc[0]
    assert oil_last["value_source"] == "fred"
    assert oil_last["lag_trading_days"] == 6 and bool(oil_last["stale_input"])
    assert oil_last["reason"] == "oil_tail_unavailable:no_market_closes"


def test_fred_publication_replaces_the_provisional_rows(tmp_path):
    db_path, _ = _store(tmp_path)
    late = pd.bdate_range(FRED_OIL_END + pd.Timedelta(days=1), VIX_END)
    with duckdb.connect(db_path) as con:
        con.register("late", _rows("DCOILWTICO", late, np.full(len(late), 56.0)))
        con.execute("INSERT INTO raw_observations SELECT * FROM late")
    _, history = _history(db_path)
    oil = history[history["shock_id"] == "oil_shock"]
    on_vix_end = oil[oil["date"] == VIX_END.date()].iloc[0]
    assert on_vix_end["value_source"] == "fred" and not on_vix_end["provisional"]


def test_artifact_carries_the_new_fields_and_the_risk_flag_contract(tmp_path):
    db_path, _ = _store(tmp_path)
    config, history = _history(db_path)
    as_of = history["date"].max()
    payload = json.loads(json.dumps(build_shock_register_artifact(history, config, as_of), default=str))
    oil = next(s for s in payload["shocks"] if s["shock_id"] == "oil_shock")
    for key in ("episode_direction", "peak_severity", "peak_value", "lag_trading_days",
                "max_lag_trading_days", "value_source", "provisional", "value_date"):
        assert key in oil
    assert oil["value_source"] == OIL_TAIL_SOURCE and oil["provisional"] is True
    # The contract rs2's depth_pipeline.risk_flag_active / risk_bucket reads -- keep byte-compatible:
    # risk_flags is a list; the entry with id "elevated_volatility_ahead" has a boolean `active`
    # that is True exactly when volatility_shock severity >= 1 on the artifact's date.
    flags = payload["risk_flags"]
    assert isinstance(flags, list)
    flag = next(f for f in flags if isinstance(f, dict) and f.get("id") == "elevated_volatility_ahead")
    assert type(flag["active"]) is bool
    vix = next(s for s in payload["shocks"] if s["shock_id"] == "volatility_shock")
    assert flag["active"] is (vix["severity"] >= 1)


def test_risk_flag_inactive_on_a_quiet_date(tmp_path):
    db_path, _ = _store(tmp_path, vix_spike=False)
    config, history = _history(db_path)
    payload = build_shock_register_artifact(history, config, history["date"].max())
    flag = next(f for f in payload["risk_flags"] if f["id"] == "elevated_volatility_ahead")
    assert flag["active"] is False


def test_service_survives_a_failed_fetch_and_names_it(tmp_path):
    db_path, _ = _store(tmp_path)

    def broken_fetcher(symbol):
        raise RuntimeError("blocked")

    json_path, md_path = build_and_write_shock_register(
        db_path=db_path, output_dir=str(tmp_path / "out"), start_date="2015-01-01",
        attach_news=False, oil_fetcher=broken_fetcher,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "oil_tail_fetch_failed:RuntimeError" in payload["reasons"]
    oil = next(s for s in payload["shocks"] if s["shock_id"] == "oil_shock")
    assert oil["value_source"] == OIL_TAIL_SOURCE  # previously stored closes still serve the tail
    assert "episode direction" in md_path.read_text(encoding="utf-8")
