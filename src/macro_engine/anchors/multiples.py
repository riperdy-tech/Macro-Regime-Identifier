"""Regime-conditional justified-multiple bands.

Two independent legs, and the payload always says which one produced a number:

**Market-observed leg.** The conditional distribution (p25 / median / p75) of a
forward multiple for a sector, conditioned on the macro state that prevailed at each
observation. This needs a VALUATION PANEL — prices AND forward earnings. MRI holds no
such panel: the local sector ETF file it already loads (`data/sector_proxy_prices.csv`,
via `config/sector_validation.yaml`) carries `ticker,date,close` and nothing else. A
price-only series cannot produce a P/E, so with no panel configured this leg publishes
`panel_source: "unavailable"` and null quantiles. It never dresses a price statistic up
as a multiple.

**Arithmetic leg.** A Gordon justified multiple from the cost-of-capital and growth
anchors. Both of those are FRED-provable, so this leg is available even when the
market-observed leg is not — and its job is to stop a statistical artefact running
away unnoticed, not to replace observation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from macro_engine.anchors.config import AnchorConfig
from macro_engine.anchors.models import (
    AnchorProvenance,
    SectorMultipleBand,
    SectorMultipleBandsPayload,
)
from macro_engine.evaluation.asof import normalize_asof

PANEL_REQUIRED_NOTE = (
    "The market-observed band requires a valuation panel (date, sector, forward "
    "multiple). MRI's local sector ETF file is price-only by construction, so no "
    "multiple can be derived from it; see docs/ANCHOR_METHODOLOGY.md §5."
)


# ── Regime state ──────────────────────────────────────────────────────────────


def _bucket(value: float | None, low_name: str, low: float | None, high_name: str, high: float | None, mid_name: str) -> str:
    if value is None or pd.isna(value):
        return "unknown"
    if low is not None and value < low:
        return low_name
    if high is not None and value > high:
        return high_name
    return mid_name


def build_regime_state_frame(
    *,
    dimension_scores: pd.DataFrame,
    observations: pd.DataFrame,
    config: AnchorConfig,
) -> pd.DataFrame:
    """Monthly macro state labels, dated by the month they describe.

    Derived from the STORED dimension history plus the observed policy rate — not from
    `current_regime.json`, whose snapshot date can be synthetic.
    """
    state_config = config.regime_state
    if dimension_scores.empty:
        return pd.DataFrame(columns=["date", "growth", "inflation", "credit", "rate_band"])

    frame = dimension_scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    pivot = frame.pivot_table(index="date", columns="dimension_id", values="score", aggfunc="last")

    growth_cfg = state_config.growth
    inflation_cfg = state_config.inflation
    credit_cfg = state_config.credit

    def column(dimension_id: str) -> pd.Series:
        if dimension_id in pivot.columns:
            return pd.to_numeric(pivot[dimension_id], errors="coerce")
        return pd.Series(index=pivot.index, dtype="float64")

    growth_scores = column(growth_cfg.dimension_id if hasattr(growth_cfg, "dimension_id") else "growth_momentum")
    inflation_scores = column(
        inflation_cfg.dimension_id if hasattr(inflation_cfg, "dimension_id") else "inflation_pressure"
    )
    credit_scores = column(credit_cfg.dimension_id if hasattr(credit_cfg, "dimension_id") else "credit_liquidity")

    rate_cfg = state_config.rate_band
    rate_series = getattr(rate_cfg, "series", "DGS10")
    rates = observations.copy()
    if not rates.empty and "series_id" in rates.columns:
        rates = rates[rates["series_id"] == rate_series].copy()
        rates["date"] = pd.to_datetime(rates["date"], errors="coerce")
        rates["value"] = pd.to_numeric(rates["value"], errors="coerce")
        rates = rates.dropna(subset=["date", "value"]).sort_values("date")
    else:
        rates = pd.DataFrame(columns=["date", "value"])

    rows: list[dict[str, object]] = []
    for date in pivot.index:
        rate_value = None
        if not rates.empty:
            prior = rates[rates["date"] <= date]
            if not prior.empty:
                rate_value = float(prior.iloc[-1]["value"])
        rows.append(
            {
                "date": date,
                "growth": _bucket(
                    _optional(growth_scores.get(date)),
                    "weak",
                    growth_cfg.threshold("weak_below"),
                    "strong",
                    growth_cfg.threshold("strong_above"),
                    "neutral",
                ),
                "inflation": _bucket(
                    _optional(inflation_scores.get(date)),
                    "low",
                    inflation_cfg.threshold("weak_below"),
                    "high",
                    inflation_cfg.threshold("strong_above"),
                    "neutral",
                ),
                "credit": _bucket(
                    _optional(credit_scores.get(date)),
                    "tight",
                    credit_cfg.threshold("tight_below"),
                    "loose",
                    credit_cfg.threshold("loose_above"),
                    "neutral",
                ),
                "rate_band": _bucket(
                    rate_value,
                    "low",
                    rate_cfg.threshold("low_below"),
                    "high",
                    rate_cfg.threshold("high_above"),
                    "mid",
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def current_state(state_frame: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, str]:
    if state_frame.empty:
        return {}
    frame = state_frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[frame["date"] <= normalize_asof(as_of)]
    if frame.empty:
        return {}
    row = frame.sort_values("date").iloc[-1]
    return {
        key: str(row[key])
        for key in ("growth", "inflation", "rate_band", "credit")
        if key in row and pd.notna(row[key])
    }


def _optional(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)  # type: ignore[arg-type]


# ── Valuation panel ───────────────────────────────────────────────────────────


def load_panel_meta(config: AnchorConfig) -> dict[str, object]:
    """Sidecar metadata from the panel builder, so its caveats reach the payload."""
    path = config.multiple_bands.panel.meta_path
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {"meta_error": f"panel meta not found at {path}"}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"meta_error": f"panel meta unreadable ({exc})"}
    return payload if isinstance(payload, dict) else {}


def load_valuation_panel(
    *,
    config: AnchorConfig,
    sector_map: dict[str, str],
) -> tuple[pd.DataFrame, str | None, list[str]]:
    """Read the configured valuation panel. Returns (frame, panel_source, reasons).

    Absent or unusable configuration returns an empty frame with
    panel_source = None, which the caller must render as `unavailable`.
    """
    panel = config.multiple_bands.panel
    if panel.source != "external_valuation_panel" or not panel.path:
        return pd.DataFrame(), None, []
    path = Path(panel.path)
    if not path.exists():
        return pd.DataFrame(), None, [f"panel: no valuation panel file at {path}"]
    try:
        if path.suffix.lower() in {".parquet", ".pq"}:
            frame = pd.read_parquet(path)
        else:
            frame = pd.read_csv(path)
    except (OSError, ValueError) as exc:
        return pd.DataFrame(), None, [f"panel: valuation panel unreadable ({exc})"]

    if panel.date_column not in frame.columns:
        return pd.DataFrame(), None, [f"panel: missing date column {panel.date_column!r}"]
    frame["date"] = pd.to_datetime(frame[panel.date_column], errors="coerce")

    if panel.multiple_column in frame.columns:
        frame["multiple"] = pd.to_numeric(frame[panel.multiple_column], errors="coerce")
    elif panel.eps_column in frame.columns and panel.price_column in frame.columns:
        eps = pd.to_numeric(frame[panel.eps_column], errors="coerce")
        price = pd.to_numeric(frame[panel.price_column], errors="coerce")
        # A non-positive forward EPS cannot produce a meaningful P/E; refuse it rather
        # than emit a negative or infinite multiple that would poison the quantiles.
        frame["multiple"] = (price / eps).where((eps > 0) & (price > 0))
    else:
        return pd.DataFrame(), None, [
            f"panel: needs {panel.multiple_column!r} or "
            f"{panel.eps_column!r}+{panel.price_column!r}"
        ]

    if panel.sector_column in frame.columns:
        frame["sector_id"] = frame[panel.sector_column].astype(str)
    elif panel.ticker_column in frame.columns:
        frame["sector_id"] = (
            frame[panel.ticker_column].astype(str).str.upper().map(
                {ticker.upper(): sector for sector, ticker in sector_map.items()}
            )
        )
    else:
        return pd.DataFrame(), None, [
            f"panel: needs {panel.sector_column!r} or {panel.ticker_column!r}"
        ]

    frame = frame.dropna(subset=["date", "multiple", "sector_id"])
    if frame.empty:
        return pd.DataFrame(), None, ["panel: valuation panel holds no usable rows"]
    return frame[["date", "sector_id", "multiple"]].reset_index(drop=True), "external_valuation_panel", []


# ── Bands ─────────────────────────────────────────────────────────────────────


def conditional_bands(
    *,
    panel: pd.DataFrame,
    state_frame: pd.DataFrame,
    sectors: list[str],
    config: AnchorConfig,
    as_of: pd.Timestamp,
) -> tuple[dict[str, SectorMultipleBand], list[str]]:
    """Conditional quantiles per sector, walking the configured fallback ladder."""
    reasons: list[str] = []
    bands: dict[str, SectorMultipleBand] = {}
    if panel.empty:
        return bands, reasons

    target_state = current_state(state_frame, as_of)
    levels = config.multiple_bands.conditioning_ladder
    quantiles = config.multiple_bands.quantiles
    labels = ["p25", "median", "p75"]

    enriched = _attach_state(panel, state_frame, keys=all_state_keys(levels))
    if enriched.empty:
        return bands, ["bands: no panel observation could be matched to a macro state"]

    for sector_id in sectors:
        sector_rows = enriched[enriched["sector_id"] == sector_id]
        if sector_rows.empty:
            reasons.append(f"bands: no panel observations for sector {sector_id}")
            bands[sector_id] = SectorMultipleBand(
                sector_id=sector_id, regime_state=target_state, ntm_pe={}, n_obs=0
            )
            continue
        selected = None
        used_level: list[str] = []
        for level in levels:
            if not level:
                # The unconditional rung is still a sample. Taking it without the
                # minimum-observations test would publish a "band" fitted to a handful
                # of points the moment every conditioned cell came up thin — the exact
                # artefact the floor exists to prevent.
                if len(sector_rows) >= config.multiple_bands.min_observations:
                    selected = sector_rows
                    used_level = []
                break
            if any(key not in target_state for key in level):
                continue
            mask = pd.Series(True, index=sector_rows.index)
            for key in level:
                mask &= sector_rows[f"state_{key}"] == target_state[key]
            candidate = sector_rows[mask]
            if len(candidate) >= config.multiple_bands.min_observations:
                selected = candidate
                used_level = list(level)
                break
        if selected is None:
            reasons.append(
                f"bands: {sector_id} has no conditioning cell with "
                f"{config.multiple_bands.min_observations}+ observations "
                f"(current state {target_state})"
            )
            bands[sector_id] = SectorMultipleBand(
                sector_id=sector_id, regime_state=target_state, ntm_pe={}, n_obs=0
            )
            continue
        values = selected["multiple"]
        quantile_values = values.quantile(quantiles)
        bands[sector_id] = SectorMultipleBand(
            sector_id=sector_id,
            regime_state=target_state,
            ntm_pe={
                label: round(float(quantile_values.iloc[index]), 3)
                for index, label in enumerate(labels[: len(quantiles)])
            },
            n_obs=int(len(selected)),
            conditioning_level=used_level,
        )
    return bands, reasons


def all_state_keys(levels: list[list[str]]) -> list[str]:
    """Every state key referenced anywhere in the ladder.

    The ladder selects among coarse and fine conditioning, so the panel must carry the
    UNION of the keys any rung asks for — attaching only the finest rung's keys would
    silently make the coarser rungs unreachable.
    """
    return sorted({key for level in levels for key in level})


def _attach_state(
    panel: pd.DataFrame,
    state_frame: pd.DataFrame,
    *,
    keys: list[str],
) -> pd.DataFrame:
    """Attach the macro state prevailing AT each panel date (no look-ahead)."""
    if state_frame.empty or not keys:
        return pd.DataFrame(columns=[*panel.columns, *[f"state_{key}" for key in keys]])
    state = state_frame[["date", *keys]].copy()
    state["date"] = pd.to_datetime(state["date"], errors="coerce")
    state = state.dropna(subset=["date"]).sort_values("date")
    frame = panel.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    merged = pd.merge_asof(frame, state, on="date", direction="backward")
    merged = merged.rename(columns={key: f"state_{key}" for key in keys})
    return merged.dropna(subset=[f"state_{key}" for key in keys])


# ── Arithmetic cross-check ────────────────────────────────────────────────────


def gordon_justified_multiple(
    *,
    cost_of_equity: float | None,
    growth: float | None,
    payout_ratio: float,
    min_spread: float,
    sanity_band: tuple[float, float],
) -> tuple[dict[str, float | None], list[str]]:
    """Gordon justified P/E = payout / (CoE - g), refusing an exploding denominator.

    A denominator thinner than `min_spread` does not describe a real multiple, it
    describes the Gordon form breaking down, so the multiple is withheld rather than
    published as a huge number that a consumer might act on.
    """
    reasons: list[str] = []
    check: dict[str, float | None] = {
        "coe": None if cost_of_equity is None else round(cost_of_equity, 4),
        "g": None if growth is None else round(growth, 4),
        "payout_ratio": payout_ratio,
        "justified_pe": None,
        "sanity_low": sanity_band[0],
        "sanity_high": sanity_band[1],
    }
    if cost_of_equity is None or growth is None:
        reasons.append("gordon: cost of equity or growth unavailable; no justified multiple")
        return check, reasons
    spread = cost_of_equity - growth
    if spread < min_spread:
        reasons.append(
            f"gordon: CoE - g = {spread:.4f} is below the {min_spread:.4f} floor; "
            "the Gordon form has broken down and no justified multiple is published"
        )
        return check, reasons
    justified = payout_ratio / spread
    check["justified_pe"] = round(justified, 3)
    low, high = sanity_band
    if not (low <= justified <= high):
        reasons.append(
            f"gordon: justified multiple {justified:.1f}x sits outside the "
            f"[{low:.0f}, {high:.0f}] sanity band -- treat the arithmetic leg as unreliable"
        )
    return check, reasons


def build_multiple_bands_payload(
    *,
    panel: pd.DataFrame,
    state_frame: pd.DataFrame,
    sectors: list[str],
    config: AnchorConfig,
    as_of: pd.Timestamp,
    built_at: str,
    cost_of_equity: float | None,
    growth: float | None,
    panel_source: str | None,
    scoring_mode: str = "calendar_asof",
    source_files: list[str] | None = None,
) -> SectorMultipleBandsPayload:
    reasons: list[str] = []
    bands, band_reasons = conditional_bands(
        panel=panel,
        state_frame=state_frame,
        sectors=sectors,
        config=config,
        as_of=as_of,
    )
    reasons.extend(band_reasons)

    gordon = config.multiple_bands.gordon
    check, check_reasons = gordon_justified_multiple(
        cost_of_equity=cost_of_equity,
        growth=growth,
        payout_ratio=gordon.payout_ratio,
        min_spread=gordon.min_spread,
        sanity_band=gordon.sanity_band,
    )
    reasons.extend(check_reasons)

    target_state = current_state(state_frame, as_of)
    ordered = [
        bands.get(sector_id)
        or SectorMultipleBand(sector_id=sector_id, regime_state=target_state, ntm_pe={}, n_obs=0)
        for sector_id in sorted(sectors)
    ]
    panel_config = config.multiple_bands.panel
    for band in ordered:
        band.arithmetic_check = dict(check)
        band.measure = panel_config.multiple_measure
        band.measure_definition = panel_config.multiple_definition

    panel_meta = load_panel_meta(config)
    for caveat in panel_meta.get("caveats") or []:
        reasons.append(f"panel caveat: {caveat}")

    resolved_panel_source = "external_valuation_panel" if panel_source else "unavailable"
    if resolved_panel_source == "unavailable":
        reasons.append(PANEL_REQUIRED_NOTE)
    if not target_state:
        reasons.append("regime_state: no stored macro state at or before the as-of date")
    degraded = resolved_panel_source == "unavailable" or check["justified_pe"] is None

    return SectorMultipleBandsPayload(
        asof=as_of.date().isoformat(),
        built_at=built_at,
        panel_source=resolved_panel_source,  # type: ignore[arg-type]
        bands=ordered,
        degraded=degraded,
        source_files=[*(source_files or []), *([panel_config.path] if panel_source and panel_config.path else [])],
        panel_measure=panel_config.multiple_measure,
        panel_measure_definition=panel_config.multiple_definition,
        panel_meta=panel_meta or None,
        provenance=AnchorProvenance(
            scoring_mode=scoring_mode,
            input_dates={},
            degradation_reasons=reasons,
            notes=[
                "Market-observed quantiles come only from a configured valuation panel. "
                "The arithmetic Gordon leg is an independent cross-check, never a "
                "substitute for observation.",
                PANEL_REQUIRED_NOTE,
            ],
        ),
    )
