from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.diagnostics.config import load_historical_diagnostic_config
from macro_engine.diagnostics.runner import (
    build_diagnostic_summary,
    build_regime_timeline,
    build_regime_transitions,
)
from macro_engine.experiments.config import (
    CalibrationExperimentConfig,
    ExperimentVariant,
    RegimeExperimentOverride,
    TransitionFilterConfig,
    load_calibration_experiment_config,
)
from macro_engine.regimes.config import (
    RegimeDefinition,
    RegimeScoringConfig,
    load_regime_config,
)
from macro_engine.regimes.scoring import (
    build_regimes_from_dimensions,
    transform_dimension_value,
)
from macro_engine.storage.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class CalibrationExperimentResult:
    output_dir: Path
    variant_results: list[dict[str, Any]]
    comparison: pd.DataFrame
    markdown_path: Path


def run_calibration_experiments(
    *,
    experiment_config_path: str | Path = "config/experiments/phase_l.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> CalibrationExperimentResult:
    experiment_config = load_calibration_experiment_config(experiment_config_path)
    base_config_path = experiment_config.experiment.base_config
    base_regime_config = load_regime_config(base_config_path)
    diagnostic_config = load_historical_diagnostic_config(base_config_path)
    store = DuckDBStore(db_path)
    dimension_scores = store.read_dimension_scores()
    output_dir = Path(experiment_config.experiment.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    variant_results: list[dict[str, Any]] = []
    for variant in experiment_config.variants:
        result = _run_variant(
            dimension_scores=dimension_scores,
            base_regimes=base_regime_config.regimes,
            variant=variant,
            diagnostic_config=diagnostic_config,
        )
        variant_results.append(result)
        _write_variant_json(output_dir / f"{variant.variant_id}.json", result)

    comparison = _build_comparison_frame(variant_results)
    comparison_path = output_dir / "comparison.json"
    comparison_path.write_text(
        json.dumps(comparison.to_dict(orient="records"), indent=2, default=str),
        encoding="utf-8",
    )
    markdown = _build_comparison_markdown(experiment_config, variant_results, comparison)
    markdown_path = output_dir / "comparison.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    return CalibrationExperimentResult(
        output_dir=output_dir,
        variant_results=variant_results,
        comparison=comparison,
        markdown_path=markdown_path,
    )


def _run_variant(
    *,
    dimension_scores: pd.DataFrame,
    base_regimes: list[RegimeDefinition],
    variant: ExperimentVariant,
    diagnostic_config,
) -> dict[str, Any]:
    regimes, interaction_overrides = _build_variant_regimes(base_regimes, variant)
    scoring = RegimeScoringConfig(softmax_temperature=variant.softmax_temperature)
    regime_result = build_regimes_from_dimensions(dimension_scores, regimes, scoring)
    if interaction_overrides:
        regime_result = _apply_interaction_overrides(
            dimension_scores=dimension_scores,
            regime_scores=regime_result.regime_scores,
            regime_health=regime_result.regime_health,
            interaction_overrides=interaction_overrides,
            scoring=scoring,
        )
    diagnostic_scores = regime_result.regime_scores.copy()
    diagnostic_health = regime_result.regime_health.copy()
    diagnostic_scores["date"] = pd.to_datetime(diagnostic_scores["date"], errors="coerce")
    diagnostic_health["date"] = pd.to_datetime(diagnostic_health["date"], errors="coerce")
    timeline = build_regime_timeline(diagnostic_scores, diagnostic_health, diagnostic_config)
    transitions = build_regime_transitions(timeline)
    filtered_timeline = _apply_transition_filter(timeline, variant.transition_filter)
    filtered_transitions = build_regime_transitions(filtered_timeline)
    summary = build_diagnostic_summary(timeline, transitions, diagnostic_config)
    filtered_summary = build_diagnostic_summary(
        filtered_timeline,
        filtered_transitions,
        diagnostic_config,
    )
    correlations = _raw_score_correlations(regime_result.regime_scores)
    latest = _latest_regime(regime_result.regime_scores, regime_result.regime_health)
    metrics = _metrics_from_summary_and_health(summary, regime_result.regime_health)
    transition_review = _transition_review(transitions)
    filtered_transition_review = _filtered_transition_review(
        raw_timeline=timeline,
        filtered_timeline=filtered_timeline,
        filtered_transitions=filtered_transitions,
        filtered_summary=filtered_summary,
    )
    return {
        "variant_id": variant.variant_id,
        "description": variant.description,
        "softmax_temperature": variant.softmax_temperature,
        "transition_filter": None
        if variant.transition_filter is None
        else variant.transition_filter.model_dump(),
        "latest": latest,
        "metrics": metrics,
        "dominant_regime_distribution": summary["dominant_regime_distribution"],
        "pairwise_raw_score_correlations": correlations,
        "transition_review": transition_review,
        "filtered_transition_review": filtered_transition_review,
        "diagnostic_label": summary["label"],
    }


def _build_variant_regimes(
    base_regimes: list[RegimeDefinition],
    variant: ExperimentVariant,
) -> tuple[list[RegimeDefinition], dict[str, RegimeExperimentOverride]]:
    regimes: list[RegimeDefinition] = []
    interaction_overrides: dict[str, RegimeExperimentOverride] = {}
    for regime in base_regimes:
        override = variant.regime_overrides.get(regime.regime_id)
        if override is None:
            regimes.append(regime)
            continue
        dimensions = override.dimensions if override.dimensions is not None else regime.dimensions
        regimes.append(
            RegimeDefinition(
                regime_id=regime.regime_id,
                enabled=regime.enabled,
                min_valid_dimensions=regime.min_valid_dimensions,
                min_coverage_ratio=regime.min_coverage_ratio,
                dimensions=dimensions,
            )
        )
        if override.interactions:
            interaction_overrides[regime.regime_id] = override
    return regimes, interaction_overrides


def _apply_interaction_overrides(
    *,
    dimension_scores: pd.DataFrame,
    regime_scores: pd.DataFrame,
    regime_health: pd.DataFrame,
    interaction_overrides: dict[str, RegimeExperimentOverride],
    scoring: RegimeScoringConfig,
):
    scores = regime_scores.copy()
    dimensions = dimension_scores.copy()
    dimensions["date"] = pd.to_datetime(dimensions["date"], errors="coerce")
    dimension_lookup = {
        (row["dimension_id"], pd.Timestamp(row["date"]).date()): row
        for row in dimensions.to_dict(orient="records")
    }
    for index, row in scores.iterrows():
        if not bool(row["valid"]) or pd.isna(row["raw_score"]):
            continue
        regime_id = row["regime_id"]
        override = interaction_overrides.get(regime_id)
        if override is None:
            continue
        date = pd.Timestamp(row["date"]).date()
        dimensions_weight = sum(dimension.weight for dimension in override.dimensions or [])
        interaction_values: list[tuple[float, float]] = []
        for interaction in override.interactions:
            value = _interaction_value(interaction, date, dimension_lookup)
            if value is not None:
                interaction_values.append((interaction.weight, value))
        total_interaction_weight = sum(weight for weight, _ in interaction_values)
        if total_interaction_weight <= 0:
            continue
        raw_score = float(row["raw_score"])
        base_share = dimensions_weight / (dimensions_weight + total_interaction_weight)
        interaction_score = sum(weight * value for weight, value in interaction_values) / (
            total_interaction_weight
        )
        interaction_share = total_interaction_weight / (
            dimensions_weight + total_interaction_weight
        )
        scores.loc[index, "raw_score"] = raw_score * base_share + interaction_score * interaction_share
    scores = _reapply_probabilities(scores, scoring)
    health = _build_health_from_scores(scores)
    from macro_engine.regimes.scoring import RegimeBuildResult

    return RegimeBuildResult(contributions=pd.DataFrame(), regime_scores=scores, regime_health=health)


def _interaction_value(interaction, date, dimension_lookup) -> float | None:
    values: list[float] = []
    for component in interaction.components:
        row = dimension_lookup.get((component.dimension_id, date))
        if row is None or not bool(row["valid"]) or pd.isna(row["score"]):
            return None
        transformed = transform_dimension_value(float(row["score"]), component.polarity)
        if transformed is None:
            return None
        values.append(float(transformed))
    if not values:
        return None
    return min(values)


def _reapply_probabilities(
    scores: pd.DataFrame,
    scoring: RegimeScoringConfig,
) -> pd.DataFrame:
    frame = scores.copy()
    frame["probability"] = None
    frame["rank"] = None
    for date, group in frame.groupby("date", dropna=False):
        valid = group[group["valid"] & group["raw_score"].notna()]
        if valid.empty:
            continue
        probabilities = _softmax(
            valid.set_index("regime_id")["raw_score"].astype(float).to_dict(),
            scoring.softmax_temperature,
        )
        ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
        for rank, (regime_id, probability) in enumerate(ranked, start=1):
            mask = (frame["date"] == date) & (frame["regime_id"] == regime_id)
            frame.loc[mask, "probability"] = probability
            frame.loc[mask, "rank"] = rank
    return frame


def _build_health_from_scores(scores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for date, group in scores.groupby("date", dropna=False):
        valid = group[group["valid"] & group["probability"].notna()].copy()
        if valid.empty:
            rows.append(
                {
                    "date": pd.Timestamp(date).date(),
                    "valid": False,
                    "dominant_regime": None,
                    "dominant_probability": None,
                    "confidence": None,
                    "entropy": None,
                    "valid_regime_count": 0,
                    "reason": "no_valid_regimes",
                }
            )
            continue
        valid = valid.sort_values("probability", ascending=False)
        top = valid.iloc[0]
        second_probability = float(valid.iloc[1]["probability"]) if len(valid) > 1 else 0.0
        probabilities = valid["probability"].astype(float).tolist()
        rows.append(
            {
                "date": pd.Timestamp(date).date(),
                "valid": True,
                "dominant_regime": top["regime_id"],
                "dominant_probability": float(top["probability"]),
                "confidence": float(top["probability"]) - second_probability,
                "entropy": _entropy(probabilities),
                "valid_regime_count": int(len(valid)),
                "reason": "ok",
            }
        )
    return pd.DataFrame(rows)


def _softmax(scores: dict[str, float], temperature: float) -> dict[str, float]:
    max_score = max(scores.values())
    exp_scores = {
        name: math.exp((score - max_score) / temperature) for name, score in scores.items()
    }
    total = sum(exp_scores.values())
    return {name: value / total for name, value in exp_scores.items()}


def _entropy(probabilities: list[float]) -> float:
    return float(-sum(probability * math.log(probability) for probability in probabilities if probability > 0))


def _latest_regime(regime_scores: pd.DataFrame, regime_health: pd.DataFrame) -> dict[str, Any]:
    valid_health = regime_health[regime_health["valid"]].sort_values("date")
    if valid_health.empty:
        return {"valid": False}
    latest = valid_health.iloc[-1]
    latest_scores = regime_scores[
        (pd.to_datetime(regime_scores["date"]) == pd.Timestamp(latest["date"]))
        & regime_scores["probability"].notna()
    ].sort_values("rank")
    return {
        "valid": True,
        "date": str(latest["date"]),
        "dominant_regime": latest["dominant_regime"],
        "dominant_probability": float(latest["dominant_probability"]),
        "confidence": float(latest["confidence"]),
        "regime_probabilities": {
            row["regime_id"]: float(row["probability"])
            for row in latest_scores.to_dict(orient="records")
        },
    }


def _metrics_from_summary_and_health(summary: dict, regime_health: pd.DataFrame) -> dict[str, Any]:
    valid = regime_health[regime_health["valid"]].copy()
    confidence = pd.to_numeric(valid["confidence"], errors="coerce")
    return {
        "valid_date_count": summary["valid_date_count"],
        "invalid_date_count": summary["invalid_date_count"],
        "regime_switch_count": summary["regime_switch_count"],
        "average_regime_duration": summary["average_regime_duration"],
        "average_confidence": summary["average_confidence"],
        "median_confidence": None if confidence.empty else float(confidence.median()),
        "p25_confidence": None if confidence.empty else float(confidence.quantile(0.25)),
        "p75_confidence": None if confidence.empty else float(confidence.quantile(0.75)),
        "low_confidence_period_count": summary["low_confidence_period_count"],
    }


def _raw_score_correlations(regime_scores: pd.DataFrame) -> dict[str, float | None]:
    valid = regime_scores[regime_scores["valid"] & regime_scores["raw_score"].notna()]
    if valid.empty:
        return {}
    pivot = valid.pivot_table(
        index="date",
        columns="regime_id",
        values="raw_score",
        aggfunc="last",
    )
    correlations = pivot.corr()
    pairs: dict[str, float | None] = {}
    for left in correlations.columns:
        for right in correlations.columns:
            if left >= right:
                continue
            value = correlations.loc[left, right]
            pairs[f"{left}__{right}"] = None if pd.isna(value) else float(value)
    return pairs


def _transition_review(transitions: pd.DataFrame) -> dict[str, Any]:
    if transitions.empty:
        return {
            "latest_transitions": [],
            "near_zero_confidence_transitions": [],
            "near_zero_confidence_transition_count": 0,
        }
    frame = transitions.copy()
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce")
    latest = frame.sort_values("transition_date").tail(10)
    near_zero = frame[frame["confidence"].fillna(0.0) < 0.01].sort_values("transition_date")
    return {
        "latest_transitions": _records_for_json(latest),
        "near_zero_confidence_transitions": _records_for_json(near_zero.tail(10)),
        "near_zero_confidence_transition_count": int(len(near_zero)),
    }


def _apply_transition_filter(
    timeline: pd.DataFrame,
    transition_filter: TransitionFilterConfig | None,
) -> pd.DataFrame:
    if transition_filter is None or timeline.empty:
        return timeline.copy()

    rows: list[dict[str, Any]] = []
    current_regime: str | None = None
    pending_regime: str | None = None
    pending_count = 0

    for row in timeline.sort_values("date").to_dict(orient="records"):
        filtered_row = row.copy()
        if not row.get("valid") or row.get("dominant_regime") is None:
            rows.append(filtered_row)
            continue

        raw_regime = row["dominant_regime"]
        confidence = float(row.get("confidence") or 0.0)
        if current_regime is None:
            current_regime = raw_regime
            pending_regime = None
            pending_count = 0
        elif raw_regime == current_regime:
            pending_regime = None
            pending_count = 0
        elif confidence < transition_filter.min_confidence_to_switch:
            pending_regime = None
            pending_count = 0
        else:
            required_confirmation = _required_confirmation_months(
                confidence,
                transition_filter,
            )
            if pending_regime == raw_regime:
                pending_count += 1
            else:
                pending_regime = raw_regime
                pending_count = 1
            if pending_count >= required_confirmation:
                current_regime = raw_regime
                pending_regime = None
                pending_count = 0

        filtered_row["raw_dominant_regime"] = raw_regime
        filtered_row["dominant_regime"] = current_regime
        rows.append(filtered_row)

    return pd.DataFrame(rows)


def _required_confirmation_months(
    confidence: float,
    transition_filter: TransitionFilterConfig,
) -> int:
    if transition_filter.only_when_confidence_below is None:
        return transition_filter.confirmation_months
    if confidence < transition_filter.only_when_confidence_below:
        return transition_filter.confirmation_months
    return 1


def _filtered_transition_review(
    *,
    raw_timeline: pd.DataFrame,
    filtered_timeline: pd.DataFrame,
    filtered_transitions: pd.DataFrame,
    filtered_summary: dict,
) -> dict[str, Any]:
    transitions = filtered_transitions.copy()
    near_zero_count = 0
    low_confidence_count = 0
    latest_transitions: list[dict[str, Any]] = []
    if not transitions.empty:
        transitions["confidence"] = pd.to_numeric(transitions["confidence"], errors="coerce")
        near_zero_count = int((transitions["confidence"].fillna(0.0) < 0.01).sum())
        low_confidence_count = int((transitions["confidence"].fillna(0.0) < 0.03).sum())
        latest_transitions = _records_for_json(transitions.sort_values("transition_date").tail(10))
    return {
        "raw_regime_switches": int(len(build_regime_transitions(raw_timeline))),
        "filtered_regime_switches": int(len(transitions)),
        "average_filtered_regime_duration": filtered_summary["average_regime_duration"],
        "near_zero_filtered_transition_count": near_zero_count,
        "low_confidence_filtered_transition_count": low_confidence_count,
        "reversal_pairs_within_1_month": _reversal_pair_count(transitions, max_months=1),
        "reversal_pairs_within_2_months": _reversal_pair_count(transitions, max_months=2),
        "latest_filtered_transitions": latest_transitions,
        "march_2020_crisis_transition_delayed": _march_2020_crisis_transition_delayed(
            raw_timeline,
            filtered_timeline,
        ),
    }


def _reversal_pair_count(transitions: pd.DataFrame, *, max_months: int) -> int:
    if transitions.empty:
        return 0
    count = 0
    rows = transitions.sort_values("transition_date").to_dict(orient="records")
    for index, first in enumerate(rows):
        first_date = pd.Timestamp(first["transition_date"])
        for second in rows[index + 1:]:
            second_date = pd.Timestamp(second["transition_date"])
            months = (second_date.year - first_date.year) * 12 + second_date.month - first_date.month
            if months > max_months:
                break
            if (
                second["from_regime"] == first["to_regime"]
                and second["to_regime"] == first["from_regime"]
            ):
                count += 1
                break
    return count


def _march_2020_crisis_transition_delayed(
    raw_timeline: pd.DataFrame,
    filtered_timeline: pd.DataFrame,
) -> bool:
    raw_date = _first_recession_date_on_or_after(raw_timeline, "2020-03-01")
    filtered_date = _first_recession_date_on_or_after(filtered_timeline, "2020-03-01")
    if raw_date is None or filtered_date is None:
        return False
    return filtered_date > raw_date


def _first_recession_date_on_or_after(
    timeline: pd.DataFrame,
    start_date: str,
) -> pd.Timestamp | None:
    if timeline.empty:
        return None
    frame = timeline.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[
        (frame["date"] >= pd.Timestamp(start_date))
        & (frame["valid"])
        & (frame["dominant_regime"] == "recession")
    ].sort_values("date")
    if frame.empty:
        return None
    return pd.Timestamp(frame.iloc[0]["date"])


def _records_for_json(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        records.append(
            {
                "transition_date": str(row.get("transition_date")),
                "from_regime": row.get("from_regime"),
                "to_regime": row.get("to_regime"),
                "from_probability": None
                if pd.isna(row.get("from_probability"))
                else float(row.get("from_probability")),
                "to_probability": None
                if pd.isna(row.get("to_probability"))
                else float(row.get("to_probability")),
                "confidence": None
                if pd.isna(row.get("confidence"))
                else float(row.get("confidence")),
                "reason": row.get("reason"),
            }
        )
    return records


def _build_comparison_frame(variant_results: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for result in variant_results:
        latest = result["latest"]
        metrics = result["metrics"]
        filtered = result.get("filtered_transition_review") or {}
        rows.append(
            {
                "variant_id": result["variant_id"],
                "softmax_temperature": result["softmax_temperature"],
                "latest_dominant_regime": latest.get("dominant_regime"),
                "latest_dominant_probability": latest.get("dominant_probability"),
                "latest_confidence": latest.get("confidence"),
                "average_confidence": metrics["average_confidence"],
                "median_confidence": metrics["median_confidence"],
                "p25_confidence": metrics["p25_confidence"],
                "p75_confidence": metrics["p75_confidence"],
                "low_confidence_period_count": metrics["low_confidence_period_count"],
                "regime_switch_count": metrics["regime_switch_count"],
                "average_regime_duration": metrics["average_regime_duration"],
                "filtered_regime_switch_count": filtered.get("filtered_regime_switches"),
                "near_zero_filtered_transition_count": filtered.get(
                    "near_zero_filtered_transition_count"
                ),
                "low_confidence_filtered_transition_count": filtered.get(
                    "low_confidence_filtered_transition_count"
                ),
                "reversal_pairs_within_2_months": filtered.get(
                    "reversal_pairs_within_2_months"
                ),
                "march_2020_crisis_transition_delayed": filtered.get(
                    "march_2020_crisis_transition_delayed"
                ),
            }
        )
    return pd.DataFrame(rows)


def _write_variant_json(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def _build_comparison_markdown(
    config: CalibrationExperimentConfig,
    variant_results: list[dict[str, Any]],
    comparison: pd.DataFrame,
) -> str:
    lines = [
        f"# {config.experiment.name} Calibration Experiment Comparison",
        "",
        "These outputs are experimental calibration diagnostics. They do not overwrite production regime outputs.",
        "",
        "## Variants",
        "",
    ]
    for result in variant_results:
        lines.append(f"- {result['variant_id']}: {result['description']}")
    lines.extend(["", "## Metrics", ""])
    for row in comparison.to_dict(orient="records"):
        lines.append(
            "- {variant}: latest {regime} at {prob:.1%}, confidence {conf:.3f}, "
            "avg confidence {avg:.3f}, raw switches {switches}, filtered switches {filtered_switches}".format(
                variant=row["variant_id"],
                regime=row["latest_dominant_regime"],
                prob=row["latest_dominant_probability"] or 0.0,
                conf=row["latest_confidence"] or 0.0,
                avg=row["average_confidence"] or 0.0,
                switches=row["regime_switch_count"],
                filtered_switches=row.get("filtered_regime_switch_count"),
            )
        )
    lines.extend(
        [
            "",
            f"Output directory: `{config.experiment.output_dir}`",
            "",
        ]
    )
    return "\n".join(lines)
