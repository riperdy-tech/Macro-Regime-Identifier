from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.reports.config import ReportConfig

NOT_INVESTMENT_ADVICE = (
    "This is an experimental macro regime diagnostic based on structured data. "
    "It is not investment advice and does not provide trading, allocation, or portfolio guidance."
)
REVISED_DATA_DISCLAIMER = (
    "Historical outputs are revised-data diagnostics, not ALFRED/vintage point-in-time backtests."
)

# C3 (MRI_S1_APPROVAL.md S9): "none:softmax_v1" per P0_0_TARGET_ARCHITECTURE.md §1.3.1 --
# the HMM fit id (S3) replaces this once S3 promotes a fitted persistence model.
PARAMETER_VINTAGE_PRE_S3 = "none:softmax_v1"


def compute_composition_signature(dimension_rows: pd.DataFrame) -> tuple[str | None, str]:
    """The header `composition_id` (§1.2 rule 3) for one date: a single id summarizing the
    S1.4 composition registry's declared set for every dimension scored on that date.

    There is no single "the" composition_id the way there is per dimension (S1.4 registers
    segments per dimension, not one for the whole engine), so this derives a stable one: a
    sha256 of the sorted `dimension_id:composition_id` pairs actually present. Returns
    `(None, "composition_registry_unavailable")` when no dimension on the date carries a
    registered composition_id (composition_id column absent/entirely null) -- registration
    is additive (S1.4), so this is a real, disclosed absence, not a bug.
    """
    if dimension_rows.empty or "composition_id" not in dimension_rows.columns:
        return None, "composition_registry_unavailable"
    pairs = sorted(
        f"{row['dimension_id']}:{row['composition_id']}"
        for row in dimension_rows.to_dict(orient="records")
        if row.get("composition_id")
    )
    if not pairs:
        return None, "composition_registry_unavailable"
    digest = hashlib.sha256(",".join(pairs).encode("utf-8")).hexdigest()[:16]
    return digest, "ok"


def build_current_regime_report(
    *,
    regime_scores: pd.DataFrame,
    regime_health: pd.DataFrame,
    regime_contributions: pd.DataFrame,
    dimension_scores: pd.DataFrame,
    dimension_contributions: pd.DataFrame,
    feature_health: pd.DataFrame,
    source_health: pd.DataFrame,
    config: ReportConfig,
    timeline: pd.DataFrame | None = None,
    scoring_mode: str = "calendar_asof",
    recession_threshold: float = 0.25,
) -> dict[str, Any]:
    valid_health = regime_health[regime_health["valid"]].sort_values("date")
    if valid_health.empty:
        return {
            "valid": False,
            "reason": "no_valid_regime",
            "disclaimer": NOT_INVESTMENT_ADVICE,
        }
    latest = valid_health.iloc[-1]
    latest_date = latest["date"]
    reported = _reported_state_for_date(timeline, latest)
    latest_scores = regime_scores[
        (regime_scores["date"] == latest_date) & regime_scores["probability"].notna()
    ].sort_values("probability", ascending=False)
    dominant = reported["reported_regime"]
    contributions = regime_contributions[
        (regime_contributions["date"] == latest_date)
        & (regime_contributions["regime_id"] == dominant)
    ].copy()
    valid_contributions = contributions[contributions["valid"]].copy()
    supporting = valid_contributions[valid_contributions["contribution"] > 0].sort_values(
        "contribution", ascending=False
    )
    opposing = valid_contributions[valid_contributions["contribution"] < 0].sort_values(
        "contribution"
    )
    latest_dimensions = dimension_scores[dimension_scores["date"] == latest_date]
    invalid_dimensions = latest_dimensions[~latest_dimensions["valid"]]

    reasons: list[str] = []

    peakedness = _to_float(latest.get("peakedness"))
    if peakedness is None:
        # C5 (MRI_S1_APPROVAL.md §5): `regimes/scoring.py` sets `reason` to
        # `peakedness_undefined:<n>_valid_regimes` for this case.
        reasons.append(str(latest.get("reason")))

    composition_id, composition_reason = compute_composition_signature(latest_dimensions)
    if composition_id is None:
        reasons.append(composition_reason)

    # C3 (P0_0 §1.3.1): season_posterior carries calibration_status per regime -- only the
    # recession leg has ground truth to calibrate against (the NBER benchmark, S1.7).
    season_posterior = {
        row["regime_id"]: {
            "probability": _to_float(row["probability"]),
            "calibration_status": (
                "calibrated_vs_nber"
                if row["regime_id"] == "recession"
                else "uncalibrated_partition_weight"
            ),
        }
        for row in latest_scores.to_dict(orient="records")
    }
    recession_row = next(
        (row for row in latest_scores.to_dict(orient="records") if row["regime_id"] == "recession"),
        None,
    )
    recession_probability = _to_float(recession_row["probability"]) if recession_row else None
    if recession_probability is None:
        reasons.append("recession_regime_not_scored")

    probabilities_desc = [
        _to_float(value) for value in latest_scores["probability"].tolist()
    ]
    if len(probabilities_desc) >= 2 and probabilities_desc[0] is not None and probabilities_desc[1] is not None:
        headline_margin = probabilities_desc[0] - probabilities_desc[1]
    else:
        headline_margin = None
        reasons.append("headline_margin_undefined:fewer_than_2_valid_regimes")

    # C3 (P0_0 §1.3.4): Layer 2 (the shock register, S4) has not been built yet -- null is
    # the spec-defined representation for "the register was unavailable", not an omission.
    reasons.append("shock_register_not_built:S4_not_shipped")

    payload = {
        "schema_version": 2,
        "valid": True,
        "process_id": "MRI-05",
        "date": pd.Timestamp(latest_date).strftime("%Y-%m-%d"),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": latest.get("source_run_id"),
        "scoring_mode": scoring_mode,
        "composition_id": composition_id,
        "parameter_vintage": PARAMETER_VINTAGE_PRE_S3,
        "dominant_regime": dominant,
        "dominant_probability": _to_float(reported["reported_regime_probability"]),
        # S1.2 (P0_0 §2.5): confidence was `peakedness * coverage`, a data-completeness
        # fact multiplied by a distribution-shape fact, then used downstream as a
        # magnitude multiplier. coverage and peakedness are now published separately
        # below; confidence/reported_confidence/raw_confidence are kept, computed the
        # same way, as deprecated v1 aliases for one release (removed in schema 3).
        "confidence": _to_float(reported["reported_confidence"]),
        "coverage": _to_float(latest.get("coverage")),
        "peakedness": peakedness,
        # C5 (MRI_S1_APPROVAL.md §5): present only when peakedness is null, naming why --
        # `regimes/scoring.py` sets `reason` to `peakedness_undefined:<n>_valid_regimes`
        # for that case (the row's overall `reason` is "ok" whenever peakedness is
        # defined, so this never fires on a routine valid row).
        **({"peakedness_reason": latest.get("reason")} if peakedness is None else {}),
        "reported_regime": dominant,
        "reported_regime_probability": _to_float(reported["reported_regime_probability"]),
        "reported_confidence": _to_float(reported["reported_confidence"]),
        "raw_dominant_regime": latest["dominant_regime"],
        "raw_dominant_probability": _to_float(latest["dominant_probability"]),
        "raw_confidence": _to_float(latest["confidence"]),
        "deprecations": [
            "confidence, reported_confidence and raw_confidence are v1 aliases for "
            "peakedness * coverage; removed in schema 3. Use coverage and peakedness "
            "separately -- neither is a multiplier.",
            "raw_confidence, dominant_regime, dominant_probability, regime_probabilities, "
            "reported_regime and reported_regime_probability are v1 keys kept for one "
            "release (P0_0 §1.3.1); use season_posterior, season_headline and "
            "season_headline_probability.",
        ],
        "regime_probabilities": {
            row["regime_id"]: _to_float(row["probability"])
            for row in latest_scores.to_dict(orient="records")
        },
        # C3 (P0_0 §1.3.1): the primary object -- consumers read the recession leg's
        # probability and calibration_status, not the argmax headline (§1.2 rule 5).
        "season_posterior": season_posterior,
        "recession_probability": recession_probability,
        "threshold_configured": recession_threshold,
        "above_threshold": (
            None if recession_probability is None else recession_probability >= recession_threshold
        ),
        "season_headline": dominant,
        "season_headline_probability": _to_float(reported["reported_regime_probability"]),
        "headline_margin": headline_margin,
        "transition_filter_applied": reported["transition_filter_applied"],
        "transition_filter_reason": reported["transition_filter_reason"],
        "transition_filter": {
            "applied": reported["transition_filter_applied"],
            "reason": reported["transition_filter_reason"],
        },
        "active_shocks_on_date": None,
        "top_supporting_dimensions": _contribution_records(
            supporting.head(config.max_contributors)
        ),
        "top_opposing_dimensions": _contribution_records(opposing.head(config.max_contributors)),
        "invalid_or_missing_dimensions": invalid_dimensions[
            ["dimension_id", "reason", "coverage_ratio", "valid_feature_count"]
        ].to_dict(orient="records")
        if not invalid_dimensions.empty
        else [],
        # C3 (P0_0 §1.3.1): the numeric interface -- one entry per dimension scored on this
        # date, carrying the composition_id (S1.4) it was scored under.
        "factors": {
            row["dimension_id"]: {
                "score": _to_float(row.get("score")),
                "valid": bool(row["valid"]),
                "coverage": _to_float(row.get("coverage_ratio")),
                "reason": row.get("reason"),
                "composition_id": row.get("composition_id"),
            }
            for row in latest_dimensions.to_dict(orient="records")
        },
        "data_health_warnings": _health_warnings(feature_health, source_health),
        "explanation": _build_explanation(dominant, supporting, opposing, config.max_contributors),
        "reasons": reasons,
        "disclaimer": NOT_INVESTMENT_ADVICE,
    }
    if config.include_dimension_details:
        payload["dimension_scores"] = latest_dimensions.to_dict(orient="records")
        payload["dimension_feature_contributions"] = dimension_contributions[
            dimension_contributions["date"] == latest_date
        ].to_dict(orient="records")
    if config.include_feature_details:
        payload["feature_health"] = feature_health.to_dict(orient="records")
    return _json_safe(payload)


def build_historical_diagnostic_report(
    *,
    timeline: pd.DataFrame,
    transitions: pd.DataFrame,
    summary: pd.DataFrame,
    config: ReportConfig,
) -> dict[str, Any]:
    if summary.empty:
        return {
            "valid": False,
            "reason": "no_diagnostic_summary",
            "disclaimer": REVISED_DATA_DISCLAIMER,
        }
    summary_row = summary.iloc[-1].to_dict()
    distribution = summary_row.get("dominant_regime_distribution") or "{}"
    if isinstance(distribution, str):
        distribution = json.loads(distribution)
    latest_transitions = transitions.sort_values("transition_date").tail(
        config.max_contributors
    )
    payload = {
        "valid": True,
        "mode": summary_row["mode"],
        "start_date": summary_row["start_date"],
        "end_date": summary_row["end_date"],
        "dominant_regime_distribution": distribution,
        "regime_switch_count": int(summary_row["regime_switch_count"]),
        "average_regime_duration": _to_float(summary_row["average_regime_duration"]),
        "average_confidence": _to_float(summary_row["average_confidence"]),
        "low_confidence_period_count": int(summary_row["low_confidence_period_count"]),
        "invalid_date_count": int(summary_row["invalid_date_count"]),
        "latest_transitions": latest_transitions.to_dict(orient="records"),
        "label": summary_row["label"],
        "disclaimer": REVISED_DATA_DISCLAIMER,
    }
    if config.include_diagnostic_summary:
        payload["timeline_tail"] = timeline.sort_values("date").tail(config.max_contributors).to_dict(
            orient="records"
        )
    return _json_safe(payload)


def current_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Current Macro Regime\n\nNo valid current regime.\n\n{payload['disclaimer']}\n"
    probabilities = "\n".join(
        f"- {regime}: {probability:.1%}"
        for regime, probability in payload["regime_probabilities"].items()
    )
    supporting = "\n".join(
        f"- {item['dimension_id']}: contribution {item['contribution']:.3f}"
        for item in payload["top_supporting_dimensions"]
    ) or "- None"
    opposing = "\n".join(
        f"- {item['dimension_id']}: contribution {item['contribution']:.3f}"
        for item in payload["top_opposing_dimensions"]
    ) or "- None"
    warnings = "\n".join(f"- {warning}" for warning in payload["data_health_warnings"]) or "- None"
    explanation = "\n".join(f"- {line}" for line in payload["explanation"])
    return f"""# Current Macro Regime

Date: {payload["date"]}
Reported regime: {payload["reported_regime"]}
Reported probability: {payload["reported_regime_probability"]:.1%}
Reported confidence: {payload["reported_confidence"]:.3f}
Transition filter: {payload["transition_filter_reason"]}

## Raw Monthly Signal

Raw dominant regime: {payload["raw_dominant_regime"]}
Raw probability: {payload["raw_dominant_probability"]:.1%}
Raw confidence: {payload["raw_confidence"]:.3f}

## Why

{explanation}

## Regime Probabilities

{probabilities}

## Top Supporting Dimensions

{supporting}

## Top Opposing Dimensions

{opposing}

## Data Health Warnings

{warnings}

{payload["disclaimer"]}
"""


def diagnostic_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Historical Diagnostic\n\nNo diagnostic summary.\n\n{payload['disclaimer']}\n"
    distribution = "\n".join(
        f"- {regime}: {share:.1%}"
        for regime, share in payload["dominant_regime_distribution"].items()
    )
    transitions = "\n".join(
        f"- {item['transition_date']}: {item['from_regime']} -> {item['to_regime']}"
        for item in payload["latest_transitions"]
    ) or "- None"
    return f"""# Historical Diagnostic

Mode: {payload["mode"]}
Date range: {payload["start_date"]} to {payload["end_date"]}

## Summary

- Regime switches: {payload["regime_switch_count"]}
- Average regime duration: {payload["average_regime_duration"]}
- Average confidence: {payload["average_confidence"]}
- Low-confidence periods: {payload["low_confidence_period_count"]}
- Invalid dates: {payload["invalid_date_count"]}

## Dominant Regime Distribution

{distribution}

## Latest Transitions

{transitions}

{payload["disclaimer"]}
"""


class SchemaVersionFieldsMissing(ValueError):
    """A payload declares `schema_version: 2` but a field that defines version 2 is null
    without a reason, or a non-nullable field is null at all.

    S1.2 (P0_0 S1.2b) split `confidence` into `coverage` and `peakedness`. A consumer that
    branches on `schema_version` reads a null field as a real value, so a v2 payload with
    a silently-null field must not publish -- that is worse than not having shipped the
    split. `coverage` is a data-completeness fact and is always defined when `valid` is
    true, so it is never nullable. `peakedness` (C5, MRI_S1_APPROVAL.md §5) is undefined
    -- not maximal -- when fewer than two regimes are valid; the architecture makes it
    nullable, but only alongside a reason naming why."""


# The fields that define schema_version 2 across the artifacts that declare it
# (current_regime.json, current_sector_ranking.json) and are never nullable once the
# payload claims the version and `valid` is true. `peakedness` is checked separately
# below: it may be null, but only with a `peakedness_reason` explaining why (C5).
# C3 (MRI_S1_APPROVAL.md §9): widened from `coverage` alone to the §1.2 header fields
# every artifact owes (process_id, built_at, source_run_id, scoring_mode,
# parameter_vintage) plus each artifact's own non-nullable §1.3 fields, so a payload that
# over-claims schema_version 2 without actually carrying its fields refuses to publish --
# the defect class this guard exists to catch (S1.2's own `schema_version: 2` claim was
# exactly this, on two of roughly twenty required fields).
_HEADER_REQUIRED_FIELDS = (
    "process_id",
    "built_at",
    "source_run_id",
    "scoring_mode",
    "parameter_vintage",
    "coverage",
    "reasons",
    "deprecations",
)
SCHEMA_V2_REGIME_REQUIRED_FIELDS = _HEADER_REQUIRED_FIELDS + (
    "factors",
    "season_posterior",
    "season_headline",
    "transition_filter",
)
# C1 (MRI_S1_APPROVAL.md §6): `validation` must always be present -- the screener's loader
# reads `validation.horizon_3m.rank_ic` and `validation.horizon_3m.t_overlap_corrected` and
# fails closed without them, so a v2 sector payload missing the key entirely must not
# publish (its numeric leaves may still be null, with reasons, when validation is stale or
# missing -- only the key itself is required here).
SCHEMA_V2_SECTOR_REQUIRED_FIELDS = _HEADER_REQUIRED_FIELDS + ("validation",)
# Backward-compatible alias for the pre-C3 name.
SCHEMA_V2_REQUIRED_FIELDS = SCHEMA_V2_REGIME_REQUIRED_FIELDS


def require_schema_v2_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Refuse to publish a `schema_version: 2` payload whose defining fields are null.

    Only applies to a payload that both claims the version and claims to be valid -- an
    invalid payload (`valid: False`) never carries these fields in the first place. The
    sector artifact is told apart by `process_id` ("MRI-07") or, failing that, the
    presence of `sector_ranking` -- everything else uses the regime artifact's set."""
    if payload.get("schema_version") == 2 and payload.get("valid"):
        is_sector = payload.get("process_id") == "MRI-07" or "sector_ranking" in payload
        required = SCHEMA_V2_SECTOR_REQUIRED_FIELDS if is_sector else SCHEMA_V2_REGIME_REQUIRED_FIELDS
        missing = [field for field in required if payload.get(field) is None]
        if payload.get("peakedness") is None and not payload.get("peakedness_reason"):
            missing.append("peakedness (or peakedness_reason)")
        if missing:
            raise SchemaVersionFieldsMissing(
                f"schema_version 2 payload is missing required field(s) {missing}: "
                "every field in this list must be non-null, and peakedness must be "
                "non-null or carry peakedness_reason, when schema_version is 2 and "
                "valid is true (P0_0 S1.2b guard, widened by C3)"
            )
    return payload


def write_report_outputs(
    *,
    output_dir: str | Path,
    json_name: str,
    markdown_name: str,
    payload: dict[str, Any],
    markdown: str,
) -> tuple[Path, Path]:
    require_schema_v2_fields(payload)
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    json_path = path / json_name
    markdown_path = path / markdown_name
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _contribution_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "dimension_id": row["dimension_id"],
            "dimension_score": _to_float(row["dimension_score"]),
            "weight": _to_float(row["weight"]),
            "normalized_weight": _to_float(row["normalized_weight"]),
            "polarity": row["polarity"],
            "transformed_dimension_value": _to_float(row["transformed_dimension_value"]),
            "contribution": _to_float(row["contribution"]),
            "reason": row["reason"],
        }
        for row in frame.to_dict(orient="records")
    ]


def _build_explanation(
    dominant_regime: str,
    supporting: pd.DataFrame,
    opposing: pd.DataFrame,
    max_contributors: int,
) -> list[str]:
    lines = [f"{dominant_regime} is dominant based on stored regime contributions."]
    for row in supporting.head(max_contributors).to_dict(orient="records"):
        lines.append(
            f"{row['dimension_id']} supported the regime with contribution {_to_float(row['contribution']):.3f}."
        )
    for row in opposing.head(max_contributors).to_dict(orient="records"):
        lines.append(
            f"{row['dimension_id']} opposed the regime with contribution {_to_float(row['contribution']):.3f}."
        )
    return lines


def _health_warnings(feature_health: pd.DataFrame, source_health: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if not feature_health.empty:
        unusable = feature_health[~feature_health["usable"]]
        warnings.extend(
            f"Feature {row['feature_id']} unusable: {row['reason']}"
            for row in unusable.head(5).to_dict(orient="records")
        )
    if not source_health.empty:
        unusable_sources = source_health[~source_health["usable"]]
        warnings.extend(
            f"Source {row['series_id']} unusable: {row['reason']}"
            for row in unusable_sources.head(5).to_dict(orient="records")
        )
    return warnings


def _reported_state_for_date(timeline: pd.DataFrame | None, latest_raw) -> dict[str, Any]:
    if timeline is None or timeline.empty:
        return {
            "reported_regime": latest_raw["dominant_regime"],
            "reported_regime_probability": latest_raw["dominant_probability"],
            "reported_confidence": latest_raw["confidence"],
            "transition_filter_applied": False,
            "transition_filter_reason": "no_timeline",
        }
    rows = timeline[
        (pd.to_datetime(timeline["date"], errors="coerce") == pd.Timestamp(latest_raw["date"]))
        & (timeline["valid"])
    ]
    if rows.empty:
        return {
            "reported_regime": latest_raw["dominant_regime"],
            "reported_regime_probability": latest_raw["dominant_probability"],
            "reported_confidence": latest_raw["confidence"],
            "transition_filter_applied": False,
            "transition_filter_reason": "no_matching_timeline",
        }
    row = rows.iloc[-1]
    reported_regime = row.get("reported_regime") or row.get("dominant_regime")
    reported_probability = row.get("reported_regime_probability")
    if pd.isna(reported_probability):
        reported_probability = row.get("dominant_probability")
    reported_confidence = row.get("reported_confidence")
    if pd.isna(reported_confidence):
        reported_confidence = row.get("confidence")
    return {
        "reported_regime": reported_regime,
        "reported_regime_probability": reported_probability,
        "reported_confidence": reported_confidence,
        "transition_filter_applied": bool(row.get("transition_filter_applied", False)),
        "transition_filter_reason": row.get("transition_filter_reason", "unknown"),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
