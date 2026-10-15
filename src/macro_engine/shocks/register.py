"""MRI Layer 2 (MRI-12) — the daily shock register builder (S4.4).

Reuses the S4.3 transform code (`shocks/transforms.py`) and the D2/D3 config
(`config/shocks.yaml`, loaded through `shocks/config.py`). Never touches a number in
Layer 1: this module only classifies FRED series moves into the register described in
`P0_0_MRI_TARGET_ARCHITECTURE.md` §3 and `outputs/shock_register.json` §1.3.4.

`build_shock_register_history` walks every trading day once per shock (a state machine
has memory, so this cannot be vectorised across days) and returns one row per
`(shock_id, date)` in the shape `storage.duckdb_store.DuckDBStore.replace_shock_register`
expects. `build_shock_register_artifact` turns one date's rows plus the config's rich
threshold metadata into the `outputs/shock_register.json` payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from macro_engine.shocks.config import to_measurement_thresholds
from macro_engine.shocks.severity import compute_severity, is_retired
from macro_engine.shocks.state_machine import (
    ShockEpisodeState,
    age_days,
    is_active,
    step,
)
from macro_engine.shocks.transforms import (
    claims_spike_pct,
    diff_63d_bp,
    log_change_63d_pct,
    stitch_dollar,
)

REGISTER_DISCLAIMER = (
    "A shock is provenance and context, not a regime, a forecast or a score. It is never "
    "blended into a Layer-1 number. This is a diagnostic research artifact, not investment "
    "advice."
)

STALE_INPUT_TRADING_DAYS = 5

_ROW_COLUMNS = [
    "shock_id", "date", "series_id", "measure", "value", "value_date", "direction",
    "intensity", "severity", "active", "state", "onset_date", "age_days",
    "peak_intensity", "peak_date", "proxy", "stale_input", "values_revised", "reason",
    "episode_direction", "peak_severity", "peak_value", "lag_trading_days", "value_source",
    "provisional",
]

OIL_TAIL_SOURCE = "yahoo_cl_chained"


def _load_series(con: duckdb.DuckDBPyConnection, series_id: str) -> pd.Series:
    """The series' own native observation dates, deduplicated and sorted -- never a
    calendar-reindexed frame. The 63-trading-day transforms are defined by row position,
    so they must run on the dates a series actually reports, exactly as S4.3 measured
    the thresholds they are compared against."""
    df = con.execute(
        "SELECT date, value FROM raw_observations WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
        [series_id],
    ).df()
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    series = df.drop_duplicates(subset="date", keep="last").set_index("date")["value"].sort_index()
    return series


def _load_market_series(con: duckdb.DuckDBPyConnection, series_id: str) -> pd.Series:
    """Daily closes from `market_observations` (the oil tail's Yahoo input). Empty when the
    table does not exist yet or holds nothing for `series_id`."""
    try:
        df = con.execute(
            "SELECT date, value FROM market_observations WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
            [series_id],
        ).df()
    except duckdb.CatalogException:
        return pd.Series(dtype=float)
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset="date", keep="last").set_index("date")["value"].sort_index()


def _oil_levels_with_tail(
    fred_levels: pd.Series, market_closes: pd.Series, *, max_anchor_gap_days: int
) -> tuple[pd.Series, pd.Timestamp | None, str | None]:
    """FRED spot levels, extended past FRED's last observation by chaining market daily
    RETURNS onto the last FRED level: `est[t] = fred[d0] * close[t] / close[anchor]`.
    Levels are never mixed -- front-month futures and spot can sit ~7% apart in a supply
    shock, while their day-to-day moves agree closely (measured 2026-10-15, see
    config/shocks.yaml oil_shock.daily_tail).

    Returns `(levels, last_fred_date, reason)`. `last_fred_date` is None and `reason` says
    why when no tail can be chained; the FRED levels are then returned unchanged."""
    if fred_levels.empty:
        return fred_levels, None, "oil_tail_unavailable:no_fred_levels"
    if market_closes.empty:
        return fred_levels, None, "oil_tail_unavailable:no_market_closes"
    d0 = fred_levels.index[-1]
    anchors = market_closes[market_closes.index <= d0]
    if anchors.empty or (d0 - anchors.index[-1]).days > max_anchor_gap_days:
        return fred_levels, None, "oil_tail_unavailable:no_anchor_close"
    tail = market_closes[market_closes.index > d0]
    if tail.empty:
        return fred_levels, d0, None
    estimated = float(fred_levels.iloc[-1]) * tail / float(anchors.iloc[-1])
    return pd.concat([fred_levels, estimated]), d0, None


class _EpisodeTracker:
    """Episode memory kept beside the state machine (2026-10-15), so a shock that is still
    `active`/`decaying` after its reading drops under the trigger keeps saying what it was.
    `severity`/`direction` stay TODAY's threshold reading; these fields never replace them.

    - episode_direction: direction of the most recent trigger crossing in this episode.
    - peak_severity: the highest severity reached in this episode.
    - peak_value: the reading on `peak_date` (the state machine's peak-intensity day).
    All three are None whenever the shock is not active (inactive or retired_sunset)."""

    def __init__(self) -> None:
        self.onset: date | None = None
        self.direction: str | None = None
        self.peak_severity: int | None = None
        self.peak_value: float | None = None

    def update(self, state: ShockEpisodeState, *, today: date, severity: int, direction: str, value: float | None) -> None:
        if not is_active(state):
            self.onset, self.direction, self.peak_severity, self.peak_value = None, None, None, None
            return
        if state.onset_date != self.onset:
            self.onset, self.direction, self.peak_severity, self.peak_value = state.onset_date, None, 0, None
        if severity >= 1:
            self.direction = direction
            self.peak_severity = max(self.peak_severity or 0, severity)
        if state.peak_date == today:
            self.peak_value = value


def _trading_calendar(con: duckdb.DuckDBPyConnection, start_date: str, end_date: str | None) -> pd.DatetimeIndex:
    """VIXCLS is required (not `required: false`... it *is* `layer2_shocks`, but S4.2's
    resolution gate held it to >= 95% of trading days like every series except the
    accepted DCOILWTICO exception) -- its own date index is the canonical trading
    calendar every other shock is forward-filled onto."""
    vix = _load_series(con, "VIXCLS")
    if vix.empty:
        # Annotation-only artifact (§3.1: "never blended into a Layer-1 number") -- an
        # empty calendar degrades the register to "no data on any shock" rather than
        # raising, the same way `anchors` degrades loudly *inside its own payload*
        # instead of taking the daily diagnostic down with it.
        print(
            "shocks: VIXCLS has no observations in this store; register will be empty",
            flush=True,
        )
        return pd.DatetimeIndex([])
    cal = vix.index[vix.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        cal = cal[cal <= pd.Timestamp(end_date)]
    return pd.DatetimeIndex(sorted(cal.unique()))


def _ffill_onto_calendar(
    transform: pd.Series, calendar: pd.DatetimeIndex
) -> tuple[pd.Series, pd.Series]:
    """Forward-fill an already-transformed series (indexed by its own native dates) onto
    the canonical calendar. Returns `(value, value_date)`, both indexed by `calendar`:
    `value` is the last transform reading available on or before that day, `value_date`
    is the native date it actually came from (never a guessed value -- a repeated real
    reading, with its own date carried alongside so staleness is visible)."""
    if transform.empty:
        return (
            pd.Series(float("nan"), index=calendar),
            pd.Series(pd.NaT, index=calendar),
        )
    native = transform.dropna().sort_index()
    if native.empty:
        return (
            pd.Series(float("nan"), index=calendar),
            pd.Series(pd.NaT, index=calendar),
        )
    union_index = native.index.union(calendar)
    value_ff = native.reindex(union_index).ffill().reindex(calendar)
    date_ff = pd.Series(native.index, index=native.index).reindex(union_index).ffill().reindex(calendar)
    return value_ff, date_ff


def _lag_limit(shock_cfg: dict[str, Any]) -> int:
    """`max_lag_trading_days` from config/shocks.yaml (each source's publication cadence)."""
    return int(shock_cfg.get("max_lag_trading_days", STALE_INPUT_TRADING_DAYS))


def _trading_day_gap(calendar: pd.DatetimeIndex, value_dates: pd.Series) -> pd.Series:
    """How many canonical trading days separate `calendar[i]` from the last calendar day
    at or before `value_dates[i]`. Used only for `stale_input` (DCOILWTICO)."""
    cal_arr = calendar.values
    today_pos = pd.Series(range(len(calendar)), index=calendar)
    vd = pd.to_datetime(value_dates)
    value_pos = np.searchsorted(cal_arr, vd.values.astype("datetime64[ns]"), side="right") - 1
    gap = today_pos.values - value_pos
    return pd.Series(gap, index=calendar)


def _intensity(value: float, thresholds: dict[str, Any], *, signed: bool) -> float | None:
    if signed:
        side_threshold = thresholds["severity_1_up"] if value >= 0 else thresholds["severity_1_down"]
    else:
        side_threshold = thresholds["severity_1"]
    if not side_threshold:
        return None
    return value / side_threshold


def _run_single_leg_shock(
    *,
    shock_id: str,
    calendar: pd.DatetimeIndex,
    values: pd.Series,
    value_dates: pd.Series,
    thresholds: dict[str, Any],
    signed: bool,
    retire_level: float,
    sunset_days: int,
    series_id: str,
    measure_label: str,
    forced_reason: dict[date, str] | None = None,
    stale_gap: pd.Series | None = None,
    values_revised: bool = False,
    max_lag_trading_days: int = STALE_INPUT_TRADING_DAYS,
    tail_after: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """`stale_gap` (trading days between each calendar day and its reading's date) drives
    `lag_trading_days` and `stale_input` (gap > `max_lag_trading_days`). Rows whose reading
    is dated after `tail_after` came from the chained oil tail: `value_source` says so and
    they are `provisional` until FRED publishes."""
    rows: list[dict[str, Any]] = []
    state = ShockEpisodeState()
    episode = _EpisodeTracker()
    forced_reason = forced_reason or {}
    for i, ts in enumerate(calendar):
        today = ts.date()
        raw_value = values.iloc[i]
        value = None if pd.isna(raw_value) else float(raw_value)
        reason = forced_reason.get(today)
        if value is None:
            severity, direction, intensity = 0, "none", None
            if reason is None:
                reason = "insufficient_history"
        else:
            result = compute_severity(value, thresholds, signed=signed)
            severity, direction = result.severity, result.direction
            intensity = _intensity(value, thresholds, signed=signed)
        retired_today = is_retired(value, retire_level, signed=signed)
        state = step(
            state,
            today=today,
            severity=severity,
            retired_today=retired_today,
            intensity=intensity,
            sunset_days=sunset_days,
        )
        episode.update(state, today=today, severity=severity, direction=direction, value=value)
        vd = value_dates.iloc[i]
        lag = int(stale_gap.iloc[i]) if stale_gap is not None and value is not None else None
        stale = bool(lag > max_lag_trading_days) if lag is not None else False
        from_tail = bool(tail_after is not None and not pd.isna(vd) and pd.Timestamp(vd) > tail_after)
        rows.append(
            {
                "shock_id": shock_id,
                "date": today,
                "series_id": series_id,
                "measure": measure_label,
                "value": value,
                "value_date": None if pd.isna(vd) else pd.Timestamp(vd).date(),
                "direction": direction,
                "intensity": intensity,
                "severity": severity,
                "active": is_active(state),
                "state": state.state,
                "onset_date": state.onset_date,
                "age_days": age_days(state, today),
                "peak_intensity": state.peak_intensity,
                "peak_date": state.peak_date,
                "proxy": False,
                "stale_input": stale,
                "values_revised": values_revised,
                "reason": reason,
                "episode_direction": episode.direction,
                "peak_severity": episode.peak_severity,
                "peak_value": episode.peak_value,
                "lag_trading_days": lag,
                "value_source": None if value is None else (OIL_TAIL_SOURCE if from_tail else "fred"),
                "provisional": from_tail,
            }
        )
    return pd.DataFrame(rows, columns=_ROW_COLUMNS)


def _run_rates_shock(
    *,
    calendar: pd.DatetimeIndex,
    dfii_values: pd.Series,
    dfii_dates: pd.Series,
    dgs2_values: pd.Series,
    dgs2_dates: pd.Series,
    thresholds: dict[str, Any],
    retire_level: float,
    sunset_days: int,
    dfii10_series_id: str,
    dgs2_series_id: str,
    measure_label: str,
    dfii_gap: pd.Series | None = None,
    dgs2_gap: pd.Series | None = None,
    max_lag_trading_days: int = STALE_INPUT_TRADING_DAYS,
) -> pd.DataFrame:
    """§3.2: DFII10 primary once it exists (2003-01 on), DGS2 always computed beside it
    as the policy leg -- whichever leg's severity is higher wins the day, DFII10 breaking
    ties (`sev_dfii >= sev_dgs2`), matching `scripts/measure_shocks.py:build_monthly_panel`
    exactly so the daily register and the monthly impact-study panel never disagree about
    which leg fired on a given month-end."""
    dfii_th = thresholds["dfii10"]
    dgs2_th = thresholds["dgs2"]
    rows: list[dict[str, Any]] = []
    state = ShockEpisodeState()
    episode = _EpisodeTracker()
    for i, ts in enumerate(calendar):
        today = ts.date()
        v_dfii = dfii_values.iloc[i]
        v_dfii = None if pd.isna(v_dfii) else float(v_dfii)
        v_dgs2 = dgs2_values.iloc[i]
        v_dgs2 = None if pd.isna(v_dgs2) else float(v_dgs2)

        sev_dfii, dir_dfii, int_dfii = 0, "none", None
        if v_dfii is not None:
            r = compute_severity(v_dfii, dfii_th, signed=True)
            sev_dfii, dir_dfii = r.severity, r.direction
            int_dfii = _intensity(v_dfii, dfii_th, signed=True)
        sev_dgs2, dir_dgs2, int_dgs2 = 0, "none", None
        if v_dgs2 is not None:
            r = compute_severity(v_dgs2, dgs2_th, signed=True)
            sev_dgs2, dir_dgs2 = r.severity, r.direction
            int_dgs2 = _intensity(v_dgs2, dgs2_th, signed=True)

        if v_dfii is not None and sev_dfii >= sev_dgs2 and sev_dfii > 0:
            value, direction, intensity, severity = v_dfii, dir_dfii, int_dfii, sev_dfii
            proxy, series_id, value_date, retire_signed_value = (
                False, dfii10_series_id, dfii_dates.iloc[i], v_dfii,
            )
        elif sev_dgs2 > 0:
            value, direction, intensity, severity = v_dgs2, dir_dgs2, int_dgs2, sev_dgs2
            proxy, series_id, value_date, retire_signed_value = (
                True, dgs2_series_id, dgs2_dates.iloc[i], v_dgs2,
            )
        elif v_dfii is not None:
            # Neither leg fired: report the primary leg (DFII10) at severity 0.
            value, direction, intensity, severity = v_dfii, "none", int_dfii, 0
            proxy, series_id, value_date, retire_signed_value = (
                False, dfii10_series_id, dfii_dates.iloc[i], v_dfii,
            )
        elif v_dgs2 is not None:
            # Pre-2003: DFII10 does not exist yet, DGS2 is the only leg.
            value, direction, intensity, severity = v_dgs2, "none", int_dgs2, 0
            proxy, series_id, value_date, retire_signed_value = (
                True, dgs2_series_id, dgs2_dates.iloc[i], v_dgs2,
            )
        else:
            value, direction, intensity, severity = None, "none", None, 0
            proxy, series_id, value_date, retire_signed_value = False, dfii10_series_id, None, None

        reason = None if value is not None else "insufficient_history"
        retired_today = is_retired(retire_signed_value, retire_level, signed=True)
        state = step(
            state,
            today=today,
            severity=severity,
            retired_today=retired_today,
            intensity=intensity,
            sunset_days=sunset_days,
        )
        episode.update(state, today=today, severity=severity, direction=direction, value=value)
        leg_gap = dgs2_gap if proxy else dfii_gap
        lag = int(leg_gap.iloc[i]) if leg_gap is not None and value is not None else None
        rows.append(
            {
                "shock_id": "rates_shock",
                "date": today,
                "series_id": series_id,
                "measure": measure_label,
                "value": value,
                "value_date": None if value_date is None or pd.isna(value_date) else pd.Timestamp(value_date).date(),
                "direction": direction,
                "intensity": intensity,
                "severity": severity,
                "active": is_active(state),
                "state": state.state,
                "onset_date": state.onset_date,
                "age_days": age_days(state, today),
                "peak_intensity": state.peak_intensity,
                "peak_date": state.peak_date,
                "proxy": proxy,
                "stale_input": bool(lag > max_lag_trading_days) if lag is not None else False,
                "values_revised": False,
                "reason": reason,
                "episode_direction": episode.direction,
                "peak_severity": episode.peak_severity,
                "peak_value": episode.peak_value,
                "lag_trading_days": lag,
                "value_source": None if value is None else "fred",
                "provisional": False,
            }
        )
    return pd.DataFrame(rows, columns=_ROW_COLUMNS)


def build_shock_register_history(
    con: duckdb.DuckDBPyConnection,
    config: dict[str, Any],
    *,
    start_date: str = "1990-01-01",
    end_date: str | None = None,
) -> pd.DataFrame:
    """One row per `(shock_id, date)`, `start_date..end_date` (inclusive) on the VIXCLS
    trading calendar. `config` is `shocks.config.load_shocks_config`'s return value
    (raw config plus the computed `taxonomy_version`)."""
    calendar = _trading_calendar(con, start_date, end_date)
    thresholds_by_shock = to_measurement_thresholds(config)
    shocks_cfg = config["shocks"]
    frames: list[pd.DataFrame] = []

    # Oil's daily tail (operator ruling 2026-10-15) is resolved first: its dates extend the
    # canonical calendar past VIXCLS's last FRED date, so the newest oil reading gets a row.
    # Every other shock is forward-filled onto those extra days and its lag says so.
    oil_cfg = shocks_cfg["oil_shock"]
    oil_levels = _load_series(con, oil_cfg["series_id"])
    oil_tail_after, oil_tail_reason = None, None
    tail_cfg = oil_cfg.get("daily_tail")
    if tail_cfg:
        oil_levels, oil_tail_after, oil_tail_reason = _oil_levels_with_tail(
            oil_levels,
            _load_market_series(con, tail_cfg["symbol"]),
            max_anchor_gap_days=int(tail_cfg["max_anchor_gap_days"]),
        )
        if oil_tail_after is not None and len(calendar):
            extra = oil_levels.index[(oil_levels.index > calendar[-1]) & (oil_levels.index > oil_tail_after)]
            if end_date is not None:
                extra = extra[extra <= pd.Timestamp(end_date)]
            calendar = pd.DatetimeIndex(sorted(calendar.union(extra)))

    # volatility_shock: transform is the level itself (no differencing).
    cfg = shocks_cfg["volatility_shock"]
    vix_native = _load_series(con, cfg["series_id"])
    values, value_dates = _ffill_onto_calendar(vix_native, calendar)
    frames.append(
        _run_single_leg_shock(
            shock_id="volatility_shock",
            calendar=calendar,
            values=values,
            value_dates=value_dates,
            thresholds=thresholds_by_shock["volatility_shock"],
            signed=False,
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            series_id=cfg["series_id"],
            measure_label=cfg["measure"],
            stale_gap=_trading_day_gap(calendar, value_dates),
            max_lag_trading_days=_lag_limit(cfg),
        )
    )

    # credit_shock: 63-trading-day change in BAA10Y, bp.
    cfg = shocks_cfg["credit_shock"]
    baa_native = diff_63d_bp(_load_series(con, cfg["series_id"]))
    values, value_dates = _ffill_onto_calendar(baa_native, calendar)
    frames.append(
        _run_single_leg_shock(
            shock_id="credit_shock",
            calendar=calendar,
            values=values,
            value_dates=value_dates,
            thresholds=thresholds_by_shock["credit_shock"],
            signed=False,
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            series_id=cfg["series_id"],
            measure_label=cfg["measure"],
            stale_gap=_trading_day_gap(calendar, value_dates),
            max_lag_trading_days=_lag_limit(cfg),
        )
    )

    # rates_shock: dual leg, DFII10 primary / DGS2 policy leg, both signed.
    cfg = shocks_cfg["rates_shock"]
    dfii_native = diff_63d_bp(_load_series(con, cfg["series_id"]))
    dgs2_native = diff_63d_bp(_load_series(con, cfg["proxy_series_id"]))
    dfii_values, dfii_dates = _ffill_onto_calendar(dfii_native, calendar)
    dgs2_values, dgs2_dates = _ffill_onto_calendar(dgs2_native, calendar)
    frames.append(
        _run_rates_shock(
            calendar=calendar,
            dfii_values=dfii_values,
            dfii_dates=dfii_dates,
            dgs2_values=dgs2_values,
            dgs2_dates=dgs2_dates,
            thresholds=thresholds_by_shock["rates_shock"],
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            dfii10_series_id=cfg["series_id"],
            dgs2_series_id=cfg["proxy_series_id"],
            measure_label=cfg["measure"],
            dfii_gap=_trading_day_gap(calendar, dfii_dates),
            dgs2_gap=_trading_day_gap(calendar, dgs2_dates),
            max_lag_trading_days=_lag_limit(cfg),
        )
    )

    # oil_shock: 63-trading-day log change, %, signed. DCOILWTICO resolves on only
    # 93.3% of trading days (accepted STOP, S4 plan "Resolution STOP") -- forward-filled
    # and flagged `stale_input` beyond 5 trading days, never guessed.
    cfg = shocks_cfg["oil_shock"]
    oil_native = log_change_63d_pct(oil_levels)
    values, value_dates = _ffill_onto_calendar(oil_native, calendar)
    oil_forced = {calendar[-1].date(): oil_tail_reason} if oil_tail_reason and len(calendar) else None
    gap = _trading_day_gap(calendar, value_dates)
    frames.append(
        _run_single_leg_shock(
            shock_id="oil_shock",
            calendar=calendar,
            values=values,
            value_dates=value_dates,
            thresholds=thresholds_by_shock["oil_shock"],
            signed=True,
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            series_id=cfg["series_id"],
            measure_label=cfg["measure"],
            stale_gap=gap,
            max_lag_trading_days=_lag_limit(cfg),
            tail_after=oil_tail_after,
            forced_reason=oil_forced,
        )
    )

    # dollar_shock: stitched broad dollar index (§3.3), null before 1995 by design --
    # no narrower index is substituted.
    cfg = shocks_cfg["dollar_shock"]
    df_gs = con.execute(
        "SELECT date, value FROM raw_observations WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
        [cfg["component_series"][0]],
    ).df()
    df_b = con.execute(
        "SELECT date, value FROM raw_observations WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
        [cfg["component_series"][1]],
    ).df()
    if not df_gs.empty and not df_b.empty:
        stitched, _mean_shift, _residual_sd = stitch_dollar(df_gs, df_b)
        stitched_native = stitched.set_index("date")["value"].sort_index()
        dollar_native = log_change_63d_pct(stitched_native)
    else:
        dollar_native = pd.Series(dtype=float)
    values, value_dates = _ffill_onto_calendar(dollar_native, calendar)
    forced = {ts.date(): "no_daily_broad_index_before_1995" for ts in calendar if ts < pd.Timestamp("1995-01-01")}
    frames.append(
        _run_single_leg_shock(
            shock_id="dollar_shock",
            calendar=calendar,
            values=values,
            value_dates=value_dates,
            thresholds=thresholds_by_shock["dollar_shock"],
            signed=True,
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            series_id=cfg["series_id"],
            measure_label=cfg["measure"],
            forced_reason=forced,
            stale_gap=_trading_day_gap(calendar, value_dates),
            max_lag_trading_days=_lag_limit(cfg),
        )
    )

    # labour_shock: ICSA, 4-week mean / trailing 52-week min. The transform must run on
    # ICSA's own weekly index (rolling 4/52 *weekly* prints) before forward-filling onto
    # the daily calendar -- filling first would turn the rolling windows into 4/52
    # *trading days*, a different and wrong number. Store values are revised (S4 plan
    # "ICSA: flag values_revised"); flagged on every row, not date-conditioned.
    cfg = shocks_cfg["labour_shock"]
    icsa_native = claims_spike_pct(_load_series(con, cfg["series_id"]))
    values, value_dates = _ffill_onto_calendar(icsa_native, calendar)
    frames.append(
        _run_single_leg_shock(
            shock_id="labour_shock",
            calendar=calendar,
            values=values,
            value_dates=value_dates,
            thresholds=thresholds_by_shock["labour_shock"],
            signed=False,
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            series_id=cfg["series_id"],
            measure_label=cfg["measure"],
            values_revised=True,
            stale_gap=_trading_day_gap(calendar, value_dates),
            max_lag_trading_days=_lag_limit(cfg),
        )
    )

    # inflation_shock: 63-trading-day change in T10YIE, bp, signed. Null before 2003 --
    # no daily breakeven series exists before it.
    cfg = shocks_cfg["inflation_shock"]
    infl_native = diff_63d_bp(_load_series(con, cfg["series_id"]))
    values, value_dates = _ffill_onto_calendar(infl_native, calendar)
    forced = {ts.date(): "no_daily_breakeven_before_2003" for ts in calendar if ts < pd.Timestamp("2003-01-01")}
    frames.append(
        _run_single_leg_shock(
            shock_id="inflation_shock",
            calendar=calendar,
            values=values,
            value_dates=value_dates,
            thresholds=thresholds_by_shock["inflation_shock"],
            signed=True,
            retire_level=cfg["retire_level"],
            sunset_days=cfg["sunset_days"],
            series_id=cfg["series_id"],
            measure_label=cfg["measure"],
            forced_reason=forced,
            stale_gap=_trading_day_gap(calendar, value_dates),
            max_lag_trading_days=_lag_limit(cfg),
        )
    )

    history = pd.concat(frames, ignore_index=True)
    history["taxonomy_version"] = config["taxonomy_version"]
    history["source_run_id"] = None
    history["built_at"] = datetime.now(timezone.utc)
    return history


@dataclass(frozen=True)
class RiskFlag:
    id: str
    source_shock: str
    active: bool
    measured_effect: dict[str, Any]
    meaning: str


def compute_risk_flags(history_on_date: pd.DataFrame, config: dict[str, Any]) -> list[dict[str, Any]]:
    """MRI_S4_PLAN.md "Impact study results and decision": `volatility_shock` is the one
    shock whose T3 forward-risk leg passed. Its effect size is read from
    `config["risk_flags"]` (written into `config/shocks.yaml` from `$SCR\\s4_impact\\t3.json`
    at build time) -- never hard-coded here."""
    flags_cfg = config.get("risk_flags") or []
    row_by_shock = {
        row["shock_id"]: row for row in history_on_date.to_dict(orient="records")
    }
    out: list[dict[str, Any]] = []
    for flag_cfg in flags_cfg:
        source_shock = flag_cfg["source_shock"]
        source_row = row_by_shock.get(source_shock)
        active = bool(source_row is not None and (source_row.get("severity") or 0) >= 1)
        out.append(
            {
                "id": flag_cfg["id"],
                "source_shock": source_shock,
                "active": active,
                "measured_effect": flag_cfg["measured_effect"],
                "meaning": flag_cfg["meaning"],
            }
        )
    return out


def build_shock_register_artifact(
    history: pd.DataFrame,
    config: dict[str, Any],
    as_of: date,
    *,
    built_at: datetime | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    """The `outputs/shock_register.json` payload (§1.3.4) for one trading day, built from
    a slice of `build_shock_register_history`'s output. Numeric fields only -- narrative
    is attached afterwards by `shocks/narrative.py`, never here."""
    built_at = built_at or datetime.now(timezone.utc)
    on_date = history[history["date"] == as_of].copy()
    shocks_cfg = config["shocks"]
    reasons: list[str] = [
        "composition_registry_not_applicable_layer2",
        "no_fitted_parameters_layer2",
    ]

    shock_rows: list[dict[str, Any]] = []
    for shock_id, cfg in shocks_cfg.items():
        row = on_date[on_date["shock_id"] == shock_id]
        if row.empty:
            reasons.append(f"{shock_id}:no_data_on_date")
            shock_rows.append(
                {
                    "shock_id": shock_id,
                    "series_id": cfg["series_id"],
                    "measure": cfg["measure"],
                    "value": None,
                    "value_date": None,
                    "direction": "none",
                    "intensity": None,
                    "severity": 0,
                    "active": False,
                    "state": "inactive",
                    "onset_date": None,
                    "age_days": None,
                    "peak_intensity": None,
                    "peak_date": None,
                    "thresholds": _thresholds_block(cfg),
                    "proxy": False,
                    "stale_input": False,
                    "values_revised": False,
                    "reason": "no_data_on_date",
                    "episode_direction": None,
                    "peak_severity": None,
                    "peak_value": None,
                    "lag_trading_days": None,
                    "max_lag_trading_days": _lag_limit(cfg),
                    "value_source": None,
                    "provisional": False,
                }
            )
            continue
        r = row.iloc[0]
        reason = None if pd.isna(r["reason"]) else r["reason"]
        if reason:
            reasons.append(f"{shock_id}:{reason}")
        age_days_value = r["age_days"]
        shock_rows.append(
            {
                "shock_id": shock_id,
                "series_id": r["series_id"],
                "measure": r["measure"],
                "value": _none_or_float(r["value"]),
                "value_date": _none_or_iso(r["value_date"]),
                "direction": r["direction"],
                "intensity": _none_or_float(r["intensity"]),
                "severity": int(r["severity"]),
                "active": bool(r["active"]),
                "state": r["state"],
                "onset_date": _none_or_iso(r["onset_date"]),
                "age_days": None if pd.isna(age_days_value) else int(age_days_value),
                "peak_intensity": _none_or_float(r["peak_intensity"]),
                "peak_date": _none_or_iso(r["peak_date"]),
                "thresholds": _thresholds_block(cfg),
                "proxy": bool(r["proxy"]),
                "stale_input": bool(r["stale_input"]),
                "values_revised": bool(r["values_revised"]),
                "reason": reason,
                "episode_direction": _none_or_str(r["episode_direction"]),
                "peak_severity": _none_or_int(r["peak_severity"]),
                "peak_value": _none_or_float(r["peak_value"]),
                "lag_trading_days": _none_or_int(r["lag_trading_days"]),
                "max_lag_trading_days": _lag_limit(cfg),
                "value_source": _none_or_str(r["value_source"]),
                "provisional": bool(r["provisional"]) if not pd.isna(r["provisional"]) else False,
            }
        )

    return {
        "schema_version": 1,
        "process_id": "MRI-12",
        "asof": as_of.isoformat(),
        "cadence": "daily",
        "built_at": built_at.isoformat(),
        "scoring_mode": "calendar_asof",
        "composition_id": None,
        "source_run_id": source_run_id,
        "parameter_vintage": None,
        "taxonomy_version": config["taxonomy_version"],
        "shocks": shock_rows,
        "risk_flags": compute_risk_flags(on_date, config),
        "unmeasured_narratives": [],
        "reasons": reasons,
        "deprecations": [],
        "disclaimer": REGISTER_DISCLAIMER,
    }


def _thresholds_block(shock_cfg: dict[str, Any]) -> dict[str, Any]:
    """Verbatim from `config/shocks.yaml` -- "the evidence travels with the trigger"
    (§1.3.4). `rates_shock` carries both legs; every other shock carries `up`/`down` (or
    just `up`)."""
    return shock_cfg.get("thresholds", {})


def _none_or_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def _none_or_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _none_or_str(value: Any) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return str(value)


def _none_or_iso(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        return value
    return pd.Timestamp(value).date().isoformat()
