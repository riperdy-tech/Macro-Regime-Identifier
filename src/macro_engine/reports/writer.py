from __future__ import annotations

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
    latest_scores = regime_scores[
        (regime_scores["date"] == latest_date) & regime_scores["probability"].notna()
    ].sort_values("probability", ascending=False)
    dominant = latest["dominant_regime"]
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
    payload = {
        "valid": True,
        "date": str(latest_date),
        "dominant_regime": dominant,
        "dominant_probability": _to_float(latest["dominant_probability"]),
        "confidence": _to_float(latest["confidence"]),
        "regime_probabilities": {
            row["regime_id"]: _to_float(row["probability"])
            for row in latest_scores.to_dict(orient="records")
        },
        "top_supporting_dimensions": _contribution_records(
            supporting.head(config.max_contributors)
        ),
        "top_opposing_dimensions": _contribution_records(opposing.head(config.max_contributors)),
        "invalid_or_missing_dimensions": invalid_dimensions[
            ["dimension_id", "reason", "coverage_ratio", "valid_feature_count"]
        ].to_dict(orient="records")
        if not invalid_dimensions.empty
        else [],
        "data_health_warnings": _health_warnings(feature_health, source_health),
        "explanation": _build_explanation(dominant, supporting, opposing, config.max_contributors),
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
Dominant regime: {payload["dominant_regime"]}
Probability: {payload["dominant_probability"]:.1%}
Confidence: {payload["confidence"]:.3f}

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


def write_report_outputs(
    *,
    output_dir: str | Path,
    json_name: str,
    markdown_name: str,
    payload: dict[str, Any],
    markdown: str,
) -> tuple[Path, Path]:
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
