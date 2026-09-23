"""Pydantic payloads for the three capital-market anchors.

Two rules shape every field here:

1. **A missing leg is null, never a plausible number.** The spec's payload sketch
   types legs as ``float``; they are ``float | None`` here because "we could not
   measure this" and "this measured zero" are different facts and a downstream
   underwriting engine must be able to tell them apart. `degraded` plus
   `degradation_reasons` say why.

2. **Provenance travels with the number.** Every payload carries `asof`, `built_at`,
   `source_files`, the observation date of each input, and the scoring mode used, so a
   stale or approximated input is visible at the point of consumption instead of
   silently ageing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ErpSource = Literal["implied", "regime_estimate", "unavailable"]
# What the solve was performed OVER. `index` is a market aggregate; `universe` is a screener
# corpus. The two are not interchangeable and the payload must not blur them.
ErpBasis = Literal["index", "universe", "regime_estimate", "unavailable"]
PanelSource = Literal["external_valuation_panel", "unavailable"]


class AnchorProvenance(BaseModel):
    """Where each anchor's inputs came from, and what was missing."""

    scoring_mode: str = "calendar_asof"
    # series_id -> ISO observation date actually used for that leg
    input_dates: dict[str, str] = Field(default_factory=dict)
    # Human-readable, one entry per leg that could not be measured.
    degradation_reasons: list[str] = Field(default_factory=list)
    # Anything a consumer would need to reproduce the build.
    notes: list[str] = Field(default_factory=list)
    # P0.3 / P0_0 §1.3.5: whether this anchor still reads the regime layer, and what it read.
    # Growth (v0.3): {"used": false, "reason": "label_channel_removed_v0.3"} -- the label no
    # longer reaches this anchor. Multiple bands: {"used": true, "date", "regime_or_state",
    # "age_days"} -- the bands still condition on the dimension-derived `regime_state`, so its
    # staleness must still be disclosed. None when an anchor has no regime leg at all
    # (cost_of_capital).
    regime_leg: dict[str, Any] | None = None


class CostOfCapitalAnchor(BaseModel):
    """Market cost of EQUITY level — never a firm-level WACC.

    The cash-flow definition this anchors (``NI + D&A - capex`` discounted against
    MARKET CAP) is a levered flow, so pairing it with an enterprise-level WACC would
    double-count the debt claim. See docs/ANCHOR_METHODOLOGY.md §1.
    """

    anchor_id: Literal["cost_of_capital"] = "cost_of_capital"
    version: str = "0.2"
    asof: str
    built_at: str
    # nominal_10y, real_10y, breakeven_10y, term_premium
    risk_free: dict[str, float | None]
    # cpi_yoy, pce_yoy
    inflation: dict[str, float | None]
    term_premium_source: str | None = None
    erp_source: ErpSource = "unavailable"
    # What the solve was performed over: an index aggregate or a screener universe. Published
    # separately from the value because they carry different authority.
    erp_basis: ErpBasis = "unavailable"
    # The solved premium and rate, whatever the basis. Basis-neutral names, so a universe solve
    # is not published as though it were a market solve.
    implied_erp: float | None = None
    implied_cost_of_equity: float | None = None
    # Reserved for a TRUE market/index aggregate. Null for a universe solve: the two are not
    # interchangeable, and a consumer reading "market_implied" must be able to trust the word.
    market_implied_erp: float | None = None
    market_implied_coe: float | None = None
    erp_percentile_vs_history: float | None = None
    # Coverage and provenance of the aggregate the solve used, so the authority of the number
    # travels with it.
    equity_aggregate: dict[str, Any] | None = None
    # The documented historical ERP distribution, published when no implied value could be
    # measured. A DIFFERENT object from market_implied_erp and labelled as one: n, start, end,
    # p10/p25/median/p75/p90, latest, min, max (start/end are ISO dates, the rest are rates).
    erp_history: dict[str, Any] | None = None
    erp_history_source: str | None = None
    # nominal_10y + market_implied_erp. Null unless the ERP was actually measured.
    market_implied_coe: float | None = None
    # Sector equity beta vs the benchmark, measured from the local ETF panel.
    sector_loadings: dict[str, float] = Field(default_factory=dict)
    loading_source: str | None = None
    degraded: bool = False
    source_files: list[str] = Field(default_factory=list)
    provenance: AnchorProvenance = Field(default_factory=AnchorProvenance)


class SectorMultipleBand(BaseModel):
    """Market-observed forward-multiple band for one sector, in one macro state."""

    sector_id: str
    # growth / inflation / rate_band / credit
    regime_state: dict[str, str] = Field(default_factory=dict)
    # p25 / median / p75 — null when the panel could not supply them.
    #
    # NAMING: the key is `ntm_pe` because that is the published contract, but the MEASURE is
    # whatever `measure` says. The shipped panel is a TRAILING multiple (`pe_ttm`), and a
    # trailing number sitting under a forward-sounding key without a label would be exactly the
    # mislabelling these anchors exist to remove.
    ntm_pe: dict[str, float | None] = Field(default_factory=dict)
    measure: str | None = None
    measure_definition: str | None = None
    n_obs: int = 0
    # Which rung of the conditioning ladder produced this band.
    conditioning_level: list[str] = Field(default_factory=list)
    # Gordon justified multiple from the CoE and growth anchors: coe, g, justified_pe.
    # Computed from FRED-provable inputs, so it is available even when ntm_pe is null.
    arithmetic_check: dict[str, float | None] = Field(default_factory=dict)


class SectorMultipleBandsPayload(BaseModel):
    anchor_id: Literal["sector_multiple_bands"] = "sector_multiple_bands"
    version: str = "0.2"
    asof: str
    built_at: str
    panel_source: PanelSource = "unavailable"
    bands: list[SectorMultipleBand] = Field(default_factory=list)
    # What the panel measured, and the builder's caveats (survivorship, publication lag, ...),
    # carried on the payload so a consumer inherits the limitations with the numbers.
    panel_measure: str | None = None
    panel_measure_definition: str | None = None
    panel_meta: dict[str, Any] | None = None
    degraded: bool = False
    source_files: list[str] = Field(default_factory=list)
    provenance: AnchorProvenance = Field(default_factory=AnchorProvenance)


class LongRunGrowthAnchor(BaseModel):
    """Long-run NOMINAL growth for the economy, and the terminal-g it implies.

    No company can outgrow its economy in perpetuity, so a uniform terminal rate
    across issuers is physically right. The defect this anchor removes is not the
    uniformity — it is that the constant had no source, no version and no revision
    path while feeding every issuer's expectations gap.
    """

    anchor_id: Literal["long_run_growth"] = "long_run_growth"
    version: str = "0.3"
    asof: str
    built_at: str
    nominal_gdp_trend: float | None = None
    # real_potential, inflation_expectation (the smoothed, trailing-12m leg that now DRIVES
    # the trend -- see inflation_expectation_spot for the un-smoothed single-observation leg)
    components: dict[str, float | None] = Field(default_factory=dict)
    # The single latest observation of the inflation-expectation series, published beside the
    # smoothed leg so a reader can see how far the smoothing has pulled the number. Not
    # consumed by any computation below.
    inflation_expectation_spot: float | None = None
    # v0.3: equals terminal_g_rung. The consumer (rs2 terminal_g()) reads this field by name;
    # the name is preserved even though the value is now the confirmed rung, not a freshly
    # rounded one-shot number.
    terminal_g_suggestion: float | None = None
    # The published rung after the dead-band + confirmation rule (P0_0 §5.2). Same value as
    # terminal_g_suggestion; carried as its own field because it is the object the rung_state
    # below is state for.
    terminal_g_rung: float | None = None
    # Dead-band + confirmation state, persisted across builds (anchor_runs) so a rebuild does
    # not silently reset the confirmation counter. See advance_rung_state().
    rung_state: dict[str, Any] = Field(default_factory=dict)
    # v0.3 DEPRECATED: removed from the computation. Kept for one release so a v1/v0.2 reader
    # (rs2_data.anchor_terminal_g() reads only terminal_g_suggestion/degraded/asof and is
    # unaffected either way) does not see the keys vanish outright. See `deprecations`.
    regime_sensitivity: dict[str, float] = Field(default_factory=dict)
    regime_applied: str | None = None
    regime_adjustment: float | None = None
    # 0.85 x the smoothed nominal trend, UNCLAMPED and UNROUNDED -- the input to the rung, not
    # the published rung itself.
    raw_trend_g: float | None = None
    clamp: dict[str, float] = Field(default_factory=dict)
    # The downstream constant this anchor exists to replace, disclosed for delta.
    downstream_prior_in_use: float | None = None
    delta: float | None = None
    degraded: bool = False
    source_files: list[str] = Field(default_factory=list)
    provenance: AnchorProvenance = Field(default_factory=AnchorProvenance)
    deprecations: list[str] = Field(default_factory=list)


class AnchorBundle(BaseModel):
    """The three anchors as one atomic build result."""

    asof: str
    built_at: str
    scoring_mode: str = "calendar_asof"
    cost_of_capital: CostOfCapitalAnchor
    long_run_growth: LongRunGrowthAnchor
    sector_multiple_bands: SectorMultipleBandsPayload
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
    disclaimer: str = ""

    def json_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
