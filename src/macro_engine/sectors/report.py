from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.reports.config import load_report_config
from macro_engine.reports.writer import (
    PARAMETER_VINTAGE_PRE_S3,
    compute_composition_signature,
    require_schema_v2_fields,
)
from macro_engine.sectors.config import SectorConfig, load_sector_config
from macro_engine.storage.duckdb_store import DuckDBStore

SECTOR_DISCLAIMER = (
    "This sector ranking is an experimental macro diagnostic. It is not investment "
    "advice and does not provide trading, allocation, portfolio sizing, or security "
    "selection guidance. Proxy tickers are reporting references only."
)

# C1 (MRI_S1_APPROVAL.md §6): the method description published on the `validation` block,
# so a consumer reading the block does not have to go find sectors/validation.py to know
# what `rank_ic`/`t_overlap_corrected` mean.
_VALIDATION_METHOD = {
    "ic": (
        "mean over dates of Spearman(tilt_score, relative forward return), 11 GICS rows "
        "ranked among themselves"
    ),
    "t_overlap_corrected": "Newey-West (Bartlett kernel, lag = horizon_months - 1) on the per-date IC series",
}
_VALIDATION_T_METHOD = "newey_west_bartlett_lag_h_minus_1"


def write_current_sector_report(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    sector_config_path: str | Path = "config/sectors.yaml",
    exposure_config_path: str | Path = "config/sector_exposures.yaml",
    prior_config_path: str | Path = "config/sector_regime_priors.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    report_config = load_report_config(config_path)
    sector_config = load_sector_config(
        macro_config_path=config_path,
        sector_config_path=sector_config_path,
        exposure_config_path=exposure_config_path,
        prior_config_path=prior_config_path,
    )
    scoring_mode = load_evaluation_config(config_path).scoring_mode
    store = DuckDBStore(db_path)
    payload = build_current_sector_report(
        sector_scores=store.read_table("sector_scores"),
        components=store.read_table("sector_score_components"),
        health=store.read_table("sector_health"),
        dimension_scores=store.read_table("dimension_scores"),
        validation_summary=store.read_table("sector_validation_summary"),
        config=sector_config,
        max_contributors=report_config.max_contributors,
        scoring_mode=scoring_mode,
    )
    require_schema_v2_fields(payload)
    markdown = current_sector_report_markdown(payload)
    output_dir = Path(report_config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "current_sector_ranking.json"
    markdown_path = output_dir / "current_sector_ranking.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_current_sector_report(
    *,
    sector_scores: pd.DataFrame,
    components: pd.DataFrame,
    health: pd.DataFrame,
    config: SectorConfig,
    max_contributors: int = 5,
    dimension_scores: pd.DataFrame | None = None,
    validation_summary: pd.DataFrame | None = None,
    scoring_mode: str = "calendar_asof",
) -> dict[str, Any]:
    if sector_scores.empty:
        return {
            "valid": False,
            "reason": "no_sector_scores",
            "disclaimer": SECTOR_DISCLAIMER,
        }
    scores = sector_scores.copy()
    scores["date"] = pd.to_datetime(scores["date"], errors="coerce")
    valid_scores = scores[scores["valid"]].sort_values(["date", "rank"])
    if valid_scores.empty:
        return {
            "valid": False,
            "reason": "no_valid_sector_scores",
            "disclaimer": SECTOR_DISCLAIMER,
        }
    latest_date = valid_scores["date"].max()
    latest_scores = valid_scores[valid_scores["date"] == latest_date].sort_values("rank")
    latest_components = components.copy()
    if not latest_components.empty:
        latest_components["date"] = pd.to_datetime(latest_components["date"], errors="coerce")
        latest_components = latest_components[latest_components["date"] == latest_date]
    latest_health = health.copy()
    if not latest_health.empty:
        latest_health["date"] = pd.to_datetime(latest_health["date"], errors="coerce")
        latest_health = latest_health[latest_health["date"] == latest_date]
    sector_lookup = {sector.sector_id: sector for sector in config.sectors}
    all_ranking = [
        _sector_rank_record(row, sector_lookup, latest_components, max_contributors)
        for row in latest_scores.to_dict(orient="records")
    ]
    # S1.5 (P0_0 §1.3.2): two ranked blocks, not one pooled 17-row cross-section. A
    # sub-industry is any sector whose config carries a parent_sector_id.
    ranking = [item for item in all_ranking if sector_lookup[item["sector_id"]].parent_sector_id is None]
    subindustry_ranking = [
        item for item in all_ranking if sector_lookup[item["sector_id"]].parent_sector_id is not None
    ]
    # top_supported/top_pressured are highlights, not the ranked cross-section, so they keep
    # drawing from every scored sector (both blocks) as before S1.5. Sorted explicitly by
    # score rather than relying on list order: `rank` is no longer a single 1..17 sequence
    # once S1.5 ranks the two blocks separately (a parent and a sub-industry can share a
    # rank), so slicing pre-sorted-by-rank order would no longer mean sorted-by-score order.
    top_supported = sorted(
        [
            item for item in all_ranking if item["confidence_adjusted_score"] is not None
            and item["confidence_adjusted_score"] > 0
        ],
        key=lambda item: (-item["confidence_adjusted_score"], item["sector_id"]),
    )[:max_contributors]
    top_pressured = sorted(
        [
            item for item in all_ranking if item["confidence_adjusted_score"] is not None
            and item["confidence_adjusted_score"] < 0
        ],
        key=lambda item: (item["confidence_adjusted_score"], item["sector_id"]),
    )[:max_contributors]
    macro_confidence = _to_float(latest_scores.iloc[0]["macro_confidence"])
    warnings = []
    if macro_confidence is None or macro_confidence < 0.05:
        warnings.append(
            "Macro confidence is low; sector ranking should be treated as a weak diagnostic signal."
        )
    invalid_health = latest_health[~latest_health["valid"]] if not latest_health.empty else pd.DataFrame()
    warnings.extend(
        f"Sector {row['sector_id']} invalid: {row['reason']}"
        for row in invalid_health.to_dict(orient="records")
    )
    latest = latest_scores.iloc[0]
    source_run_id = latest.get("source_run_id")

    reasons: list[str] = []
    macro_peakedness = _to_float(latest.get("macro_peakedness"))
    if macro_peakedness is None:
        reasons.append("peakedness_undefined")

    composition_id = None
    if dimension_scores is not None and not dimension_scores.empty:
        dims = dimension_scores.copy()
        dims["date"] = pd.to_datetime(dims["date"], errors="coerce")
        composition_id, composition_reason = compute_composition_signature(
            dims[dims["date"] == latest_date]
        )
        if composition_id is None:
            reasons.append(composition_reason)
    else:
        reasons.append("composition_registry_unavailable")

    validation_block = _build_validation_block(validation_summary, source_run_id)
    reasons.extend(validation_block["reasons"])

    return _json_safe(
        {
            "schema_version": 2,
            "valid": True,
            "process_id": "MRI-07",
            "date": str(latest_date.date()),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_run_id": source_run_id,
            "scoring_mode": scoring_mode,
            "composition_id": composition_id,
            "parameter_vintage": PARAMETER_VINTAGE_PRE_S3,
            "reported_macro_regime": latest["macro_reported_regime"],
            "raw_macro_leader": latest["macro_raw_dominant_regime"],
            "macro_confidence": macro_confidence,
            # S1.2 (P0_0 §2.5 / §1.3.2): copied from Layer 1, same owner semantics as
            # current_regime.json -- neither is multiplied into a score here.
            "coverage": _to_float(latest.get("macro_coverage")),
            "peakedness": macro_peakedness,
            # C5 (MRI_S1_APPROVAL.md §5): present only when peakedness is null. The macro
            # date frame does not carry the Layer-1 row's specific reason through to the
            # sector artifact (only `macro_peakedness` itself is copied), so this is a
            # generic marker rather than the precise `peakedness_undefined:<n>_valid_
            # regimes` current_regime.json carries; it exists so the schema-2 guard can
            # tell "undefined, and named as such" from "silently missing".
            **({"peakedness_reason": "peakedness_undefined"} if macro_peakedness is None else {}),
            "sector_ranking": ranking,
            "subindustry_ranking": subindustry_ranking,
            # C1 (MRI_S1_APPROVAL.md §6): always present. The screener's gate reads
            # `validation.horizon_3m.rank_ic` and `validation.horizon_3m.t_overlap_corrected`
            # and fails closed without them.
            "validation": validation_block,
            "top_macro_supported_sectors": top_supported,
            "top_macro_pressured_sectors": top_pressured,
            "warnings": warnings,
            "reasons": reasons,
            "deprecations": [
                "raw_sector_score and confidence_adjusted_score are v1 aliases for tilt_score "
                "(S1.2: the confidence multiplier is deleted, so all three are now the same "
                "value); will be removed in schema 3.",
                "macro_confidence is a v1 alias for peakedness * coverage; removed in schema 3. "
                "Use coverage and peakedness separately -- neither is a multiplier.",
            ],
            "disclaimer": SECTOR_DISCLAIMER,
        }
    )


def _build_validation_block(
    validation_summary: pd.DataFrame | None,
    source_run_id: Any,
) -> dict[str, Any]:
    """C1 (MRI_S1_APPROVAL.md §6). Always returns a dict with the full shape -- the
    screener's guard (require_schema_v2_fields) refuses to publish without the key at all,
    but a missing or stale validation is a normal, disclosed state: every numeric leaf null,
    with a reason."""
    null_horizons = {
        "horizon_3m": _validation_horizon_payload(None),
        "horizon_1m": _validation_horizon_payload(None),
        "subindustry_6": {
            "horizon_3m": _validation_horizon_payload(None),
            "horizon_1m": _validation_horizon_payload(None),
        },
    }
    if validation_summary is None or validation_summary.empty or "cross_section" not in validation_summary.columns:
        return {
            "cross_section": "gics_11",
            "score_end_date": None,
            "validated_run_id": None,
            "method": _VALIDATION_METHOD,
            "t_method": _VALIDATION_T_METHOD,
            **null_horizons,
            "reasons": ["validation_missing"],
        }

    gics = validation_summary[validation_summary["cross_section"] == "gics_11"]
    subindustry = validation_summary[validation_summary["cross_section"] == "subindustry_6"]
    if gics.empty:
        return {
            "cross_section": "gics_11",
            "score_end_date": None,
            "validated_run_id": None,
            "method": _VALIDATION_METHOD,
            "t_method": _VALIDATION_T_METHOD,
            **null_horizons,
            "reasons": ["validation_missing"],
        }

    validated_run_ids = gics["run_id"].dropna().unique().tolist() if "run_id" in gics.columns else []
    validated_run_id = str(validated_run_ids[0]) if len(validated_run_ids) == 1 else None
    score_end_dates = gics["score_end_date"].dropna().unique().tolist() if "score_end_date" in gics.columns else []
    # `score_end_date` round-trips through a DuckDB DATE column, which pandas reads back as
    # a Timestamp -- str() on that carries a spurious "00:00:00", the same class of defect
    # C3 fixed on current_regime.json's own `date` field.
    score_end_date = str(pd.Timestamp(score_end_dates[0]).date()) if score_end_dates else None

    # C1 rule 4: a validation that ran against a DIFFERENT sector-scoring run than the one
    # this ranking was just built from describes stale numbers -- every numeric field is
    # null, named, rather than silently presented as current.
    stale = (
        validated_run_id is not None
        and source_run_id is not None
        and validated_run_id != str(source_run_id)
    )
    if stale:
        return {
            "cross_section": "gics_11",
            "score_end_date": score_end_date,
            "validated_run_id": validated_run_id,
            "method": _VALIDATION_METHOD,
            "t_method": _VALIDATION_T_METHOD,
            **null_horizons,
            "reasons": [f"validation_stale:{validated_run_id}"],
        }

    def row_for(frame: pd.DataFrame, horizon: str) -> dict[str, Any] | None:
        match = frame[frame["horizon"] == horizon]
        return None if match.empty else match.iloc[0].to_dict()

    return {
        "cross_section": "gics_11",
        "score_end_date": score_end_date,
        "validated_run_id": validated_run_id,
        "method": _VALIDATION_METHOD,
        "t_method": _VALIDATION_T_METHOD,
        "horizon_3m": _validation_horizon_payload(row_for(gics, "3m")),
        "horizon_1m": _validation_horizon_payload(row_for(gics, "1m")),
        "subindustry_6": {
            "horizon_3m": _validation_horizon_payload(row_for(subindustry, "3m")),
            "horizon_1m": _validation_horizon_payload(row_for(subindustry, "1m")),
        },
        "reasons": [],
    }


def _validation_horizon_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "rank_ic": None,
            "t_naive": None,
            "t_overlap_corrected": None,
            "n_dates": 0,
            "n_obs": 0,
            "positive_share": None,
            "sd_per_date_ic": None,
        }
    return {
        "rank_ic": _to_float(row.get("rank_ic_spearman")),
        "t_naive": _to_float(row.get("t_naive")),
        "t_overlap_corrected": _to_float(row.get("t_overlap_corrected")),
        "n_dates": int(row.get("n_dates") or 0),
        "n_obs": int(row.get("observation_count") or 0),
        "positive_share": _to_float(row.get("positive_share")),
        "sd_per_date_ic": _to_float(row.get("sd_per_date_ic")),
    }


def current_sector_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Current Sector Macro Ranking\n\nNo valid sector ranking.\n\n{payload['disclaimer']}\n"
    ranking = "\n".join(
        "- {rank}. {label} ({sector_id}): adjusted {adjusted:.3f}, raw {raw:.3f}".format(
            rank=item["rank"],
            label=item["label"],
            sector_id=item["sector_id"],
            adjusted=item["confidence_adjusted_score"],
            raw=item["raw_sector_score"],
        )
        for item in payload["sector_ranking"]
    )
    subindustry_ranking = "\n".join(
        "- {rank}. {label} ({sector_id}): adjusted {adjusted:.3f}, raw {raw:.3f}".format(
            rank=item["rank"],
            label=item["label"],
            sector_id=item["sector_id"],
            adjusted=item["confidence_adjusted_score"],
            raw=item["raw_sector_score"],
        )
        for item in payload.get("subindustry_ranking", [])
    ) or "- None"
    supported = "\n".join(
        f"- {item['label']}: positive macro tailwind score {item['confidence_adjusted_score']:.3f}"
        for item in payload["top_macro_supported_sectors"]
    ) or "- None"
    pressured = "\n".join(
        f"- {item['label']}: negative macro sensitivity score {item['confidence_adjusted_score']:.3f}"
        for item in payload["top_macro_pressured_sectors"]
    ) or "- None"
    warnings = "\n".join(f"- {warning}" for warning in payload["warnings"]) or "- None"
    explanations = "\n\n".join(
        _sector_explanation_markdown(item) for item in payload["top_macro_supported_sectors"]
    )
    return f"""# Current Sector Macro Ranking

Date: {payload["date"]}
Reported macro regime: {payload["reported_macro_regime"]}
Raw macro leader: {payload["raw_macro_leader"]}
Macro confidence: {payload["macro_confidence"]:.3f}

## Sector Ranking

{ranking}

## Sub-Industry Ranking

Ranked among the six sub-industries only, not against their parent GICS sector (P0_0 §1.3.2 /
review N4).

{subindustry_ranking}

## Top Macro-Supported Sectors

{supported}

## Top Macro-Pressured Sectors

{pressured}

## Top Sector Explanations

{explanations}

## Warnings

{warnings}

{payload["disclaimer"]}
"""


def _sector_rank_record(
    row: dict,
    sector_lookup: dict[str, Any],
    components: pd.DataFrame,
    max_contributors: int,
) -> dict[str, Any]:
    sector_id = row["sector_id"]
    sector = sector_lookup[sector_id]
    sector_components = components[components["sector_id"] == sector_id].copy()
    valid_components = sector_components[sector_components["valid"]].copy()
    supporting = valid_components[valid_components["contribution"] > 0].sort_values(
        "contribution",
        ascending=False,
    )
    opposing = valid_components[valid_components["contribution"] < 0].sort_values(
        "contribution"
    )
    return {
        "sector_id": sector_id,
        "label": sector.label,
        "proxy_ticker": sector.proxy_ticker,
        "parent_sector_id": sector.parent_sector_id,
        "rank": int(row["rank"]),
        # S1.2 (P0_0 §2.5 / §1.3.2): tilt_score is the un-multiplied score and the schema-2
        # field name; raw_sector_score and confidence_adjusted_score are kept, populated with
        # the same value, as deprecated v1 aliases now that the confidence multiplier is gone.
        "tilt_score": _to_float(row["confidence_adjusted_score"]),
        "raw_sector_score": _to_float(row["raw_sector_score"]),
        "confidence_adjusted_score": _to_float(row["confidence_adjusted_score"]),
        "macro_reported_regime": row["macro_reported_regime"],
        "macro_raw_dominant_regime": row["macro_raw_dominant_regime"],
        "macro_confidence": _to_float(row["macro_confidence"]),
        # C3 (P0_0 §1.3.2): every component that fed the score, not just the top N -- so a
        # consumer can see everything that moved, not only the highlights.
        "components": _component_records(valid_components),
        "top_supporting_components": _component_records(supporting.head(max_contributors)),
        "top_opposing_components": _component_records(opposing.head(max_contributors)),
        # C3 (P0_0 §1.3.2): S6 may promote fitted exposures; until then every sector uses
        # the hand-set v1 exposure table (config/sector_exposures.yaml).
        "exposure_source": "hand_set_v1",
    }


def _component_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "component_type": row["component_type"],
            "component_id": row["component_id"],
            "input_value": _to_float(row["input_value"]),
            "weight_or_exposure": _to_float(row["weight_or_exposure"]),
            "contribution": _to_float(row["contribution"]),
            "reason": row["reason"],
        }
        for row in frame.to_dict(orient="records")
    ]


def _sector_explanation_markdown(item: dict[str, Any]) -> str:
    supporting = "\n".join(
        "- {component_id} ({component_type}) contributed {contribution:.3f}".format(**component)
        for component in item["top_supporting_components"]
    ) or "- None"
    opposing = "\n".join(
        "- {component_id} ({component_type}) contributed {contribution:.3f}".format(**component)
        for component in item["top_opposing_components"]
    ) or "- None"
    return f"""### {item["label"]}

This sector has a macro diagnostic score of {item["confidence_adjusted_score"]:.3f}.

Supporting components:
{supporting}

Opposing components:
{opposing}
"""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
