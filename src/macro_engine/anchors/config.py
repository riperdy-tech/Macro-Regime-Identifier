"""Declarative configuration for the anchors layer (config/anchors.yaml).

Every scoring and anchoring rule stays in YAML, matching the rest of the engine. The
`scoring_mode` switch is deliberately NOT read from here: it lives once, in
config/phase_b_sources.yaml, so the anchors and the diagnostics can never disagree
about what an evaluation date was allowed to see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

PanelSourceKind = Literal["external_valuation_panel", "none"]

# Quoting convention of a source series, and the conversion applied on read.
# FRED quotes every yield/spread in PERCENT while this package works in DECIMAL
# fractions (the consumer's constants — TERMINAL_G = 0.025 — are decimal, and its
# discount rates are decimal too). Declaring the convention per series makes a unit
# mismatch impossible to introduce silently, which is the failure mode that produced a
# 445% "10-year yield" when this was left implicit.
SeriesUnits = Literal["percent", "index", "level"]


class SeriesRef(BaseModel):
    series: str
    units: SeriesUnits = "percent"

    def to_decimal(self, value: float) -> float:
        if self.units == "percent":
            return value / 100.0
        return value


class AnchorsSection(BaseModel):
    output_dir: str = "outputs"
    report_disclaimer: str = ""


class RiskFreeConsistencyConfig(BaseModel):
    max_leg_date_spread_days: int = Field(default=7, ge=0)
    breakeven_tolerance: float = Field(default=0.0025, gt=0)


class RiskFreeConfig(BaseModel):
    nominal_10y: SeriesRef
    real_10y: SeriesRef
    breakeven_10y: SeriesRef
    term_premium_candidates: list[SeriesRef] = Field(default_factory=list)
    term_premium_proxy: dict[str, SeriesRef] = Field(default_factory=dict)
    consistency: RiskFreeConsistencyConfig = RiskFreeConsistencyConfig()


class InflationConfig(BaseModel):
    cpi: str
    pce: str
    yoy_periods: int = Field(default=12, gt=0)


class ImpliedSolveConfig(BaseModel):
    rate_bounds: tuple[float, float] = (-0.02, 0.25)
    growth_bounds: tuple[float, float] = (-0.05, 0.35)
    iterations: int = Field(default=80, gt=0)
    min_constituents: int = Field(default=30, gt=0)
    min_coverage_share: float = Field(default=0.60, ge=0, le=1)


class ErpRegimeEstimateConfig(BaseModel):
    history_path: str | None = None
    source_label: str | None = None
    min_points: int = Field(default=60, gt=0)
    lookback_years: int = Field(default=30, gt=0)


class ErpConfig(BaseModel):
    method: Literal["implied", "regime_estimate", "unavailable"] = "implied"
    equity_aggregate_path: str | None = None
    implied_solve: ImpliedSolveConfig = ImpliedSolveConfig()
    regime_estimate: ErpRegimeEstimateConfig = ErpRegimeEstimateConfig()
    percentile_reference: Literal["history", "regime_band"] = "history"


class SectorLoadingsConfig(BaseModel):
    # Provenance gate: loadings are a market-measured quantity, so they are derived
    # only from a panel explicitly declared market-observed. `none` publishes no
    # loadings rather than publishing betas off an unverified file.
    price_panel_source: Literal["stored_sector_proxy_prices", "none"] = "none"
    source_config: str = "config/sector_validation.yaml"
    min_return_observations: int = Field(default=250, gt=0)
    window_years: int = Field(default=5, gt=0)
    default_loading: float = 1.0


class CostOfCapitalConfig(BaseModel):
    risk_free: RiskFreeConfig
    inflation: InflationConfig
    erp: ErpConfig = ErpConfig()
    sector_loadings: SectorLoadingsConfig = SectorLoadingsConfig()


class RealPotentialConfig(BaseModel):
    series: str = "GDPPOT"
    trend_window_years: int = Field(default=10, gt=0)
    min_observations: int = Field(default=12, gt=0)


class InflationExpectationConfig(BaseModel):
    candidates: list[SeriesRef] = Field(default_factory=list)
    min_observations: int = Field(default=12, gt=0)


class TerminalGConfig(BaseModel):
    max_share_of_nominal_trend: float = Field(default=0.85, gt=0, le=1)
    floor: float = 0.0
    ceiling: float = 0.05
    round_to: float = Field(default=0.0025, gt=0)


class GrowthConfig(BaseModel):
    real_potential: RealPotentialConfig = RealPotentialConfig()
    inflation_expectation: InflationExpectationConfig = InflationExpectationConfig()
    terminal_g: TerminalGConfig = TerminalGConfig()
    regime_sensitivity: dict[str, float] = Field(default_factory=dict)
    downstream_prior_in_use: float | None = None


class PanelConfig(BaseModel):
    source: PanelSourceKind = "none"
    path: str | None = None
    date_column: str = "date"
    sector_column: str = "sector_id"
    ticker_column: str = "ticker"
    multiple_column: str = "pe_ntm"
    eps_column: str = "eps_ntm"
    price_column: str = "close"
    sector_map_config: str = "config/sector_validation.yaml"
    # What `multiple_column` actually MEASURES. Published on every band, because the field the
    # quantiles live in is named `ntm_pe` while the shipped panel is a trailing multiple.
    multiple_measure: str | None = None
    multiple_definition: str | None = None
    # Sidecar metadata written by the panel builder; its caveats are copied into the payload so
    # a consumer inherits the limitations along with the numbers.
    meta_path: str | None = None


class GordonConfig(BaseModel):
    payout_ratio: float = Field(default=0.60, gt=0, le=1)
    min_spread: float = Field(default=0.005, gt=0)
    sanity_band: tuple[float, float] = (5.0, 60.0)


class MultipleBandsConfig(BaseModel):
    panel: PanelConfig = PanelConfig()
    quantiles: list[float] = Field(default_factory=lambda: [0.25, 0.50, 0.75])
    min_observations: int = Field(default=24, gt=0)
    conditioning_ladder: list[list[str]] = Field(
        default_factory=lambda: [["growth", "inflation", "rate_band", "credit"], ["growth"]]
    )
    gordon: GordonConfig = GordonConfig()


class ThresholdConfig(BaseModel):
    model_config = {"extra": "allow"}

    def threshold(self, name: str) -> float | None:
        value = getattr(self, name, None)
        return None if value is None else float(value)


class RegimeStateConfig(BaseModel):
    growth: ThresholdConfig = Field(
        default_factory=lambda: ThresholdConfig(weak_below=-0.5, strong_above=0.5)
    )
    inflation: ThresholdConfig = Field(
        default_factory=lambda: ThresholdConfig(weak_below=-0.5, strong_above=0.5)
    )
    credit: ThresholdConfig = Field(
        default_factory=lambda: ThresholdConfig(tight_below=-0.5, loose_above=0.5)
    )
    rate_band: ThresholdConfig = Field(
        default_factory=lambda: ThresholdConfig(low_below=2.5, high_above=4.0)
    )


class AnchorConfig(BaseModel):
    anchors: AnchorsSection = AnchorsSection()
    cost_of_capital: CostOfCapitalConfig
    growth: GrowthConfig = GrowthConfig()
    multiple_bands: MultipleBandsConfig = MultipleBandsConfig()
    regime_state: RegimeStateConfig = RegimeStateConfig()

    @property
    def output_dir(self) -> str:
        return self.anchors.output_dir


def load_anchor_config(path: str | Path = "config/anchors.yaml") -> AnchorConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle) or {}
    return AnchorConfig.model_validate(data)
