"""Anchor orchestration: build the three anchors, publish them, record them.

Precedent followed deliberately — this mirrors the secular-theme layer (independent
YAML config -> independent service -> independent CLI command -> registered in the
export list -> exposed by regime_status.py with missing-file tolerance). The anchors
are an ADDITIVE output surface: nothing here changes an existing artifact, and the
export layer keeps them out of `data_status` (see dashboard_export.OPTIONAL_OUTPUT_FILES).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from macro_engine.anchors.config import load_anchor_config
from macro_engine.anchors.cost_of_capital import build_cost_of_capital_anchor
from macro_engine.anchors.growth import build_long_run_growth_anchor, current_regime_label
from macro_engine.anchors.models import (
    AnchorBundle,
    CostOfCapitalAnchor,
    LongRunGrowthAnchor,
)
from macro_engine.anchors.multiples import (
    build_multiple_bands_payload,
    build_regime_state_frame,
    load_valuation_panel,
)
from macro_engine.anchors.pit_calendar import vintage_staleness_days
from macro_engine.evaluation.asof import normalize_asof
from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.sectors.validation import load_sector_validation_config
from macro_engine.storage.duckdb_store import DuckDBStore

COST_OF_CAPITAL_JSON = "cost_of_capital_anchor.json"
LONG_RUN_GROWTH_JSON = "long_run_growth_anchor.json"
SECTOR_MULTIPLE_BANDS_JSON = "sector_multiple_bands.json"
REPAIR_PACKAGE_JSON = "rs2_repair_package.json"

# Constants in the downstream underwriting engine that these anchors exist to replace.
# Kept here (not read from the consumer) so the mapping is auditable from MRI alone;
# RS2_REPAIR_PACKAGE.md carries the human-readable counterpart.
DOWNSTREAM_CONSTANTS: list[dict[str, Any]] = [
    {
        "constant": "TERMINAL_G",
        "location": "valuation_backbone.py",
        "current": 0.025,
        "replacement": "long_run_growth_anchor.terminal_g_suggestion",
        "fallback": 0.025,
    },
    {
        "constant": "SECTOR_WACC",
        "location": "valuation_backbone.py",
        "current": "integer table, 7-11 (%)",
        "replacement": "cost_of_capital_anchor level (risk_free + ERP)",
        "fallback": "current table",
        "note": "Equity cost of capital LEVEL only. Sector SPREADS are preserved: the "
                "anchors publish loadings, never a replacement spread table.",
    },
    {
        "constant": "DEFAULT_WACC",
        "location": "valuation_backbone.py",
        "current": 10.0,
        "replacement": "cost_of_capital_anchor level",
        "fallback": 10.0,
    },
    {
        "constant": "UTIL_COE",
        "location": "valuation_backbone.py",
        "current": 0.07,
        "replacement": "cost_of_capital_anchor level, utilities loading",
        "fallback": 0.07,
    },
    {
        "constant": "FIN_COE",
        "location": "valuation_backbone.py",
        "current": 0.10,
        "replacement": "cost_of_capital_anchor level, financials loading",
        "fallback": 0.10,
    },
]


def build_anchors(
    *,
    config_path: str | Path = "config/anchors.yaml",
    macro_config_path: str | Path = "config/phase_b_sources.yaml",
    sector_config_path: str | Path = "config/sectors.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    as_of: str | None = None,
    write: bool = True,
    only: Sequence[str] | None = None,
) -> AnchorBundle:
    """Build all three anchors from the stored DuckDB state.

    All three are always computed, even when `only` narrows which artifacts are
    written: the cost-of-capital and multiple-band anchors both consume the growth
    anchor's terminal rate, so a partial build that skipped it would publish a number
    computed against a different growth assumption than the one shipped beside it.

    Deterministic and LLM-free by design: an anchor that cannot be reproduced cannot be
    audited, and an anchor that is not audited has no business setting a discount rate.
    """
    config = load_anchor_config(config_path)
    evaluation = load_evaluation_config(macro_config_path)

    store = DuckDBStore(db_path)
    store.initialize()
    observations = store.read_raw_observations()
    resolved_as_of = _resolve_as_of(as_of, observations)
    # The HYBRID boundary is applied here, per build, from the config -- not inferred. Point-in-
    # time is the configured basis, but it only covers dates ALFRED actually archives: a blanket
    # switch would not upgrade a 1995 build, it would empty it (no risk-free curve before 2005-07,
    # no breakeven before 2014-02). Earlier dates resolve by the calendar rule and the provenance
    # says so. See docs/ANCHOR_METHODOLOGY.md §6.2b.
    scoring_mode = evaluation.effective_scoring_mode(resolved_as_of)
    scoring_mode_applied = evaluation.scoring_mode_applied(resolved_as_of)
    vintages = (
        # Pushdown of the resolution rule: only the vintage visible on this as-of. Loading the
        # whole archive to answer one date cost ~8 s and ~900 MB per build.
        store.read_vintages_as_of(resolved_as_of)
        if scoring_mode == "point_in_time"
        else None
    )
    vintage_lag = vintage_staleness_days(vintages, resolved_as_of)
    built_at = datetime.now(UTC).isoformat()

    sector_validation = load_sector_validation_config(config.cost_of_capital.sector_loadings.source_config)
    proxies = dict(sector_validation.proxies)
    benchmark = sector_validation.benchmark_ticker
    sectors = _tracked_sectors(sector_config_path, proxies)

    timeline = store.read_table("historical_regime_timeline")
    regime_label = current_regime_label(timeline, resolved_as_of)

    growth_anchor = build_long_run_growth_anchor(
        observations=observations,
        config=config,
        as_of=resolved_as_of,
        built_at=built_at,
        regime_label=regime_label,
        scoring_mode=scoring_mode,
        vintages=vintages,
        source_files=[macro_config_path, config_path],
    )

    prices = store.read_sector_proxy_prices()
    coc_anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=prices,
        proxies=proxies,
        benchmark_ticker=benchmark,
        config=config,
        as_of=resolved_as_of,
        built_at=built_at,
        terminal_growth=growth_anchor.terminal_g_suggestion,
        scoring_mode=scoring_mode,
        vintages=vintages,
        source_files=[macro_config_path, config_path, config.cost_of_capital.sector_loadings.source_config],
    )

    panel, panel_source, panel_reasons = load_valuation_panel(
        config=config,
        sector_map=proxies,
    )
    state_frame = build_regime_state_frame(
        dimension_scores=store.read_table("dimension_scores"),
        observations=observations,
        config=config,
    )
    bands_payload = build_multiple_bands_payload(
        panel=panel,
        state_frame=state_frame,
        sectors=sectors,
        config=config,
        as_of=resolved_as_of,
        built_at=built_at,
        # The MEASURED cost of equity, or None. Never the risk-free fallback: the Gordon
        # cross-check divides by (CoE - g), and substituting an observed 10y for the cost of
        # equity understates the denominator by the whole equity premium. That inflates the
        # justified multiple (it produced 63.2x against a real 10y of 4.45%) and would publish
        # an "arithmetic check" that checks nothing. Withheld is the correct answer.
        cost_of_equity=_level_cost_of_equity(coc_anchor),
        growth=growth_anchor.terminal_g_suggestion,
        panel_source=panel_source,
        scoring_mode=scoring_mode,
        source_files=[config_path],
    )
    if panel_reasons:
        bands_payload.provenance.degradation_reasons.extend(panel_reasons)

    # STALENESS GATE. A point-in-time build is only as good as the newest vintage it can see: if
    # the pipeline has not refreshed vintages, the archive resolves to an older as-of and every
    # leg is quietly older than the build claims. The inputs would look perfectly well-formed --
    # measured at −54 bp on the 10-year nominal for a 4.5-month-old archive -- so the staleness
    # has to be stated, not inferred by the reader.
    if scoring_mode == "point_in_time" and vintage_lag is not None:
        if vintage_lag > evaluation.point_in_time_max_lag_days:
            note = (
                f"point_in_time: newest stored ALFRED vintage is {vintage_lag} days before the "
                f"as-of {resolved_as_of.date().isoformat()} (limit "
                f"{evaluation.point_in_time_max_lag_days}); the 'as published' basis is stale -- "
                "refresh vintages in the daily pipeline"
            )
            for payload in (coc_anchor, growth_anchor, bands_payload):
                payload.degraded = True
                payload.provenance.degradation_reasons.append(note)

    reasons = [
        *coc_anchor.provenance.degradation_reasons,
        *growth_anchor.provenance.degradation_reasons,
        *bands_payload.provenance.degradation_reasons,
    ]
    bundle = AnchorBundle(
        asof=resolved_as_of.date().isoformat(),
        built_at=built_at,
        scoring_mode=scoring_mode_applied,
        cost_of_capital=coc_anchor,
        long_run_growth=growth_anchor,
        sector_multiple_bands=bands_payload,
        degraded=coc_anchor.degraded or growth_anchor.degraded or bands_payload.degraded,
        degradation_reasons=reasons,
        disclaimer=config.anchors.report_disclaimer.strip(),
    )

    if write:
        kept = _guarded_files(bundle=bundle, output_dir=config.output_dir, only=only)
        if kept:
            bundle.degradation_reasons.extend(kept)
        write_anchor_outputs(bundle=bundle, output_dir=config.output_dir, only=only)
        store.upsert_anchor_run(
            {
                "run_id": f"{bundle.asof}:{scoring_mode_applied}",
                "built_at": bundle.built_at,
                "as_of": bundle.asof,
                "scoring_mode": scoring_mode_applied,
                "cost_of_capital_json": coc_anchor.model_dump(mode="json"),
                "long_run_growth_json": growth_anchor.model_dump(mode="json"),
                "sector_multiple_bands_json": bands_payload.model_dump(mode="json"),
                "degraded": bundle.degraded,
                "degradation_reasons": reasons,
            }
        )
    return bundle


def _resolve_as_of(as_of: str | None, observations: pd.DataFrame) -> pd.Timestamp:
    """As-of date for the build, always a naive calendar date and NEVER in the future.

    Defaults to the newest stored observation rather than `today`: an anchor dated today but
    built from April data would misreport its own freshness, and every input date is recorded
    in provenance so the consumer can see the real lag.

    The cap at today is load-bearing, not cosmetic. Several FRED series carry PROJECTIONS as
    rows alongside realised history — `GDPPOT` (CBO Real Potential GDP) extends roughly a
    decade past the present. Without the cap the newest stored observation is a forecast a
    decade out, the as-of lands in the future, and every `date <= as_of` filter silently
    admits projected values. That would make the growth anchor a restatement of CBO's forecast
    while still presenting itself as an observed measurement. An anchor describes what IS
    KNOWN, so an observation dated after the build date cannot enter one.
    """
    today = normalize_asof(None)
    if as_of:
        return min(normalize_asof(as_of), today)
    if observations.empty:
        return today
    dates = pd.to_datetime(observations["date"], errors="coerce").dropna()
    if dates.empty:
        return today
    return min(normalize_asof(dates.max()), today)


def _tracked_sectors(sector_config_path: str | Path, proxies: dict[str, str]) -> list[str]:
    """Sectors the anchors cover: the tracked sector config, restricted to those with
    a price proxy, so a band is never published for something unmeasurable."""
    path = Path(sector_config_path)
    if not path.exists():
        return sorted(proxies)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    tracked = [
        str(item["sector_id"])
        for item in data.get("sectors", [])
        if item.get("enabled", True) and item.get("sector_id")
    ]
    return sorted(set(tracked) & set(proxies)) or sorted(proxies)


def _level_cost_of_equity(anchor: CostOfCapitalAnchor) -> float | None:
    """The market cost-of-equity LEVEL the anchors own, or None.

    `implied_cost_of_equity` when an aggregate solve produced one — whatever its basis, since
    the payload records the basis alongside via `erp_basis`. The observed nominal 10y is a
    RISK-FREE rate and is deliberately NOT substituted here: a consumer that accepts it as a
    cost of equity would understate the discount rate by the whole equity premium. The
    risk-free leg is published separately as `risk_free.nominal_10y`, where it is labelled.
    """
    return anchor.implied_cost_of_equity


def write_anchor_outputs(
    *,
    bundle: AnchorBundle,
    output_dir: str | Path = "outputs",
    only: Sequence[str] | None = None,
) -> dict[str, Path]:
    """Idempotent JSON + Markdown writes (deterministic key order, no timestamps that
    are not part of the payload).

    PUBLISH GUARD: a degraded build never overwrites a non-degraded artifact. These files
    are consumed by another system, and a run against a thin, empty or broken database
    would otherwise publish a set of nulls straight over good evidence. A stale-but-real
    anchor is strictly better than a fresh-but-empty one, and staleness is already bounded
    by the consumer's own age gate. The refusal is recorded so it is never silent.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    selected = None if only is None else {str(item) for item in only}
    payloads = {
        COST_OF_CAPITAL_JSON: ("cost_of_capital", bundle.cost_of_capital.model_dump(mode="json")),
        LONG_RUN_GROWTH_JSON: ("long_run_growth", bundle.long_run_growth.model_dump(mode="json")),
        SECTOR_MULTIPLE_BANDS_JSON: (
            "sector_multiple_bands",
            bundle.sector_multiple_bands.model_dump(mode="json"),
        ),
    }
    markdown = {
        COST_OF_CAPITAL_JSON: cost_of_capital_markdown(bundle),
        LONG_RUN_GROWTH_JSON: long_run_growth_markdown(bundle),
        SECTOR_MULTIPLE_BANDS_JSON: multiple_bands_markdown(bundle),
    }
    for filename, (key, payload) in payloads.items():
        if selected is not None and key not in selected:
            continue
        json_path = directory / filename
        if _publish_guard_blocks(json_path, payload):
            continue
        json_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=_json_default),
            encoding="utf-8",
        )
        written[filename] = json_path
        md_path = directory / filename.replace(".json", ".md")
        md_path.write_text(markdown[filename], encoding="utf-8")
        written[md_path.name] = md_path

    # The repair package is published alongside the anchors, guarded the same way. It is
    # meaningful even from a degraded build -- arguably more so, since it itemises exactly
    # what could not be measured and what the consumer must therefore keep doing.
    if selected is None:
        repair_payload = build_repair_package(bundle)
        repair_path = directory / REPAIR_PACKAGE_JSON
        if not _publish_guard_blocks(repair_path, repair_payload):
            repair_path.write_text(
                json.dumps(repair_payload, indent=2, sort_keys=True, default=_json_default),
                encoding="utf-8",
            )
            written[REPAIR_PACKAGE_JSON] = repair_path
    return written


def _guarded_files(
    *,
    bundle: AnchorBundle,
    output_dir: str | Path,
    only: Sequence[str] | None = None,
) -> list[str]:
    """Report the artifacts the publish guard will refuse to overwrite."""
    directory = Path(output_dir)
    selected = None if only is None else {str(item) for item in only}
    payloads = {
        COST_OF_CAPITAL_JSON: ("cost_of_capital", bundle.cost_of_capital),
        LONG_RUN_GROWTH_JSON: ("long_run_growth", bundle.long_run_growth),
        SECTOR_MULTIPLE_BANDS_JSON: ("sector_multiple_bands", bundle.sector_multiple_bands),
        REPAIR_PACKAGE_JSON: ("repair_package", None),
    }
    notes: list[str] = []
    for filename, (key, payload) in payloads.items():
        if selected is not None and key not in selected:
            continue
        if key == "repair_package":
            if selected is None and _publish_guard_blocks(directory / filename, build_repair_package(bundle)):
                notes.append(_GUARD_NOTE.format(filename=filename))
            continue
        if _publish_guard_blocks(directory / filename, payload.model_dump(mode="json")):
            notes.append(_GUARD_NOTE.format(filename=filename))
    return notes


_GUARD_NOTE = (
    "publish guard: kept the existing non-degraded {filename} rather than "
    "overwriting it with a degraded build (a stale-but-real anchor beats a "
    "fresh-but-empty one; staleness is bounded by the consumer's age gate)"
)


def _publish_guard_blocks(path: Path, payload: dict[str, Any]) -> bool:
    """True when writing `payload` would replace real evidence with nulls."""
    if not payload.get("degraded"):
        return False
    existing = _read_json(path)
    if not existing or existing.get("degraded"):
        return False
    return True


def build_repair_package(bundle: AnchorBundle) -> dict[str, Any]:
    """Machine-readable 'constant -> anchor' mapping for the downstream consumer.

    This carries the evidence, not just the mapping: the live anchor values, the
    versioned prior each replaces, and the degradation state, so a consumer can decide
    whether to adopt the anchor on facts rather than on faith.
    """
    growth: LongRunGrowthAnchor = bundle.long_run_growth
    coc: CostOfCapitalAnchor = bundle.cost_of_capital
    return {
        "package_version": "0.2",
        "generated_from": {
            "asof": bundle.asof,
            "built_at": bundle.built_at,
            "scoring_mode": bundle.scoring_mode,
        },
        "anchor_files": {
            "cost_of_capital": COST_OF_CAPITAL_JSON,
            "long_run_growth": LONG_RUN_GROWTH_JSON,
            "sector_multiple_bands": SECTOR_MULTIPLE_BANDS_JSON,
        },
        "constants": DOWNSTREAM_CONSTANTS,
        "values": {
            "level_cost_of_equity": _level_cost_of_equity(coc),
            "risk_free_10y": coc.risk_free.get("nominal_10y"),
            "market_implied_erp": coc.market_implied_erp,
            "terminal_g_suggestion": growth.terminal_g_suggestion,
            "terminal_g_prior_in_use": growth.downstream_prior_in_use,
            "terminal_g_delta": growth.delta,
            "sector_loadings": coc.sector_loadings,
        },
        "degraded": bundle.degraded,
        "degradation_reasons": bundle.degradation_reasons,
        "adoption_gates": [
            "All legs the consumer uses must be non-null; a null leg means the input "
            "was not measurable, and the consumer must keep its existing constant.",
            "terminal_g_suggestion must be adopted only when long_run_growth.degraded "
            "is false.",
            "Sector spreads stay with the consumer: adopt the LEVEL, keep the spread "
            "structure, and treat sector_loadings as information.",
            "Re-run the MoS rank-correlation impact audit before and after adoption.",
        ],
        "rollback": [
            "Delete or rename the anchor JSON files: the consumer falls back to its own "
            "constants, which remain in the code unchanged.",
            "The anchors are additive outputs; no MRI output schema changed, so no MRI "
            "rollback is required.",
        ],
        "known_findings": [
            "outputs/current_regime.json can carry a synthetic future `date` "
            "(observed 2031-08-01). The anchors avoid it by dating themselves from the "
            "stored observation history. Recorded here, deliberately NOT fixed in the "
            "anchor change.",
        ],
    }


def cost_of_capital_markdown(bundle: AnchorBundle) -> str:
    anchor = bundle.cost_of_capital
    lines = [
        "# Cost of Capital Anchor",
        "",
        f"- as-of: {anchor.asof}",
        f"- built at: {anchor.built_at}",
        f"- scoring mode: {bundle.scoring_mode}",
        f"- degraded: {str(anchor.degraded).lower()}",
        "",
        "## Risk-free decomposition",
        "",
        _table(["leg", "value"], [
            ["nominal 10y", _pct(anchor.risk_free.get("nominal_10y"))],
            ["real 10y (TIPS)", _pct(anchor.risk_free.get("real_10y"))],
            ["breakeven 10y", _pct(anchor.risk_free.get("breakeven_10y"))],
            ["term premium", _pct(anchor.risk_free.get("term_premium"))],
        ]),
        f"Term premium source: {anchor.term_premium_source or 'unavailable'}",
        "",
        "## Inflation backdrop",
        "",
        _table(["leg", "value"], [
            ["CPI yoy", _pct(anchor.inflation.get("cpi_yoy"))],
            ["PCE yoy", _pct(anchor.inflation.get("pce_yoy"))],
        ]),
        "## Equity risk premium",
        "",
        _table(["leg", "value"], [
            ["erp source", anchor.erp_source],
            ["market-implied ERP", _pct(anchor.market_implied_erp)],
            ["ERP percentile vs history", _pct(anchor.erp_percentile_vs_history)],
            ["market-implied cost of equity", _pct(anchor.market_implied_coe)],
        ]),
        "## Sector loadings (equity beta vs benchmark)",
        "",
        _table(["sector", "loading"],
               [[key, f"{value:.3f}"] for key, value in sorted(anchor.sector_loadings.items())]),
        f"Loading source: {anchor.loading_source or 'unavailable'}",
        "",
        "## Provenance",
        "",
        _table(["input series", "observation date"],
               [[key, value] for key, value in sorted(anchor.provenance.input_dates.items())]),
    ]
    if anchor.provenance.degradation_reasons:
        lines += ["## Degradation", ""] + [
            f"- {reason}" for reason in anchor.provenance.degradation_reasons
        ] + [""]
    if anchor.provenance.notes:
        lines += ["## Notes", ""] + [f"- {note}" for note in anchor.provenance.notes] + [""]
    lines += [bundle.disclaimer, ""]
    return "\n".join(lines)


def long_run_growth_markdown(bundle: AnchorBundle) -> str:
    anchor = bundle.long_run_growth
    lines = [
        "# Long-Run Growth Anchor",
        "",
        f"- as-of: {anchor.asof}",
        f"- built at: {anchor.built_at}",
        f"- scoring mode: {bundle.scoring_mode}",
        f"- degraded: {str(anchor.degraded).lower()}",
        "",
        "## Components",
        "",
        _table(["component", "value"], [
            ["real potential growth", _pct(anchor.components.get("real_potential"))],
            ["long-run inflation expectation", _pct(anchor.components.get("inflation_expectation"))],
            ["nominal GDP trend", _pct(anchor.nominal_gdp_trend)],
        ]),
        "## Terminal growth",
        "",
        _table(["leg", "value"], [
            ["raw trend g (after share cap)", _pct(anchor.raw_trend_g)],
            ["regime applied", anchor.regime_applied or "none"],
            ["regime adjustment", _pct(anchor.regime_adjustment)],
            ["terminal g suggestion", _pct(anchor.terminal_g_suggestion)],
            ["downstream constant in use", _pct(anchor.downstream_prior_in_use)],
            ["delta vs downstream constant", _pct(anchor.delta)],
        ]),
        "## Regime sensitivity table",
        "",
        _table(["regime", "adjustment"],
               [[key, _pct(value)] for key, value in sorted(anchor.regime_sensitivity.items())]),
        "## Provenance",
        "",
        _table(["input series", "observation date"],
               [[key, value] for key, value in sorted(anchor.provenance.input_dates.items())]),
    ]
    if anchor.provenance.degradation_reasons:
        lines += ["## Degradation", ""] + [
            f"- {reason}" for reason in anchor.provenance.degradation_reasons
        ] + [""]
    lines += [anchor.provenance.notes[0] if anchor.provenance.notes else "", "", bundle.disclaimer, ""]
    return "\n".join(lines)


def multiple_bands_markdown(bundle: AnchorBundle) -> str:
    payload = bundle.sector_multiple_bands
    lines = [
        "# Regime-Conditional Multiple Bands",
        "",
        f"- as-of: {payload.asof}",
        f"- built at: {payload.built_at}",
        f"- panel source: {payload.panel_source}",
        f"- degraded: {str(payload.degraded).lower()}",
        "",
        "## Bands",
        "",
        _table(
            ["sector", "state", "p25", "median", "p75", "n", "conditioning", "justified P/E"],
            [
                [
                    band.sector_id,
                    "/".join(f"{key}={value}" for key, value in sorted(band.regime_state.items())),
                    _num(band.ntm_pe.get("p25")),
                    _num(band.ntm_pe.get("median")),
                    _num(band.ntm_pe.get("p75")),
                    str(band.n_obs),
                    "+".join(band.conditioning_level) or "unconditional",
                    _num(band.arithmetic_check.get("justified_pe")),
                ]
                for band in payload.bands
            ],
        ),
    ]
    if payload.provenance.degradation_reasons:
        lines += ["## Degradation", ""] + [
            f"- {reason}" for reason in payload.provenance.degradation_reasons
        ] + [""]
    lines += [bundle.disclaimer, ""]
    return "\n".join(lines)


def anchor_status(*, outputs_dir: str | Path = "outputs") -> dict[str, Any]:
    """Compact anchor view for regime_status.py. Missing files degrade to nulls."""
    directory = Path(outputs_dir)
    coc = _read_json(directory / COST_OF_CAPITAL_JSON)
    growth = _read_json(directory / LONG_RUN_GROWTH_JSON)
    bands = _read_json(directory / SECTOR_MULTIPLE_BANDS_JSON)
    present = [bool(coc), bool(growth), bool(bands)]
    # The level and its provenance travel together: a risk-free-only fallback is a different
    # claim from a measured cost of equity, and a status view that showed one number without
    # saying which would be the same defect the anchors exist to remove.
    measured_coe = (coc or {}).get("implied_cost_of_equity")
    risk_free_10y = ((coc or {}).get("risk_free") or {}).get("nominal_10y")
    if measured_coe is not None:
        level = measured_coe
        level_source = f"implied_cost_of_equity({(coc or {}).get('erp_basis')})"
    elif risk_free_10y is not None:
        level, level_source = risk_free_10y, "risk_free_10y_only_not_a_cost_of_equity"
    else:
        level, level_source = None, "unavailable"
    return {
        "cost_of_capital": coc or None,
        "long_run_growth": growth or None,
        "sector_multiple_bands": bands or None,
        "level_cost_of_equity": level,
        "level_cost_of_equity_source": level_source,
        "terminal_g_suggestion": (growth or {}).get("terminal_g_suggestion"),
        "degraded": any(
            (payload or {}).get("degraded", False) for payload in (coc, growth, bands)
        ),
        "available": all(present),
        "source_files": {
            "cost_of_capital_anchor": COST_OF_CAPITAL_JSON if coc else None,
            "long_run_growth_anchor": LONG_RUN_GROWTH_JSON if growth else None,
            "sector_multiple_bands": SECTOR_MULTIPLE_BANDS_JSON if bands else None,
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pct(value: Any) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value) * 100:.2f}%"


def _num(value: Any) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value):.2f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_none_"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
