from __future__ import annotations

from dataclasses import dataclass
import json

import pandas as pd

from macro_engine.diagnostics.config import HistoricalDiagnosticConfig


@dataclass(frozen=True)
class HistoricalDiagnosticResult:
    timeline: pd.DataFrame
    transitions: pd.DataFrame
    summary: dict


def run_historical_diagnostic(
    regime_scores: pd.DataFrame,
    regime_health: pd.DataFrame,
    config: HistoricalDiagnosticConfig,
) -> HistoricalDiagnosticResult:
    scores = regime_scores.copy()
    health = regime_health.copy()
    if scores.empty:
        scores = pd.DataFrame(
            columns=["date", "regime_id", "probability", "valid", "rank", "coverage_ratio"]
        )
    if health.empty:
        health = pd.DataFrame(
            columns=[
                "date",
                "valid",
                "dominant_regime",
                "dominant_probability",
                "confidence",
                "entropy",
                "valid_regime_count",
                "reason",
            ]
        )
    scores["date"] = pd.to_datetime(scores["date"], errors="coerce")
    health["date"] = pd.to_datetime(health["date"], errors="coerce")
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date) if config.end_date else None
    scores = scores[scores["date"] >= start]
    health = health[health["date"] >= start]
    if end is not None:
        scores = scores[scores["date"] <= end]
        health = health[health["date"] <= end]

    timeline = build_regime_timeline(scores, health, config)
    transitions = build_regime_transitions(timeline)
    summary = build_diagnostic_summary(timeline, transitions, config)
    return HistoricalDiagnosticResult(
        timeline=timeline,
        transitions=transitions,
        summary=summary,
    )


def build_regime_timeline(
    regime_scores: pd.DataFrame,
    regime_health: pd.DataFrame,
    config: HistoricalDiagnosticConfig,
) -> pd.DataFrame:
    rows: list[dict] = []
    for health_row in regime_health.sort_values("date").to_dict(orient="records"):
        date = pd.Timestamp(health_row["date"])
        date_scores = regime_scores[
            (regime_scores["date"] == date)
            & (regime_scores["valid"])
            & (regime_scores["probability"].notna())
        ].sort_values("probability", ascending=False)
        valid = bool(health_row.get("valid")) and int(health_row.get("valid_regime_count") or 0) >= config.min_valid_regimes
        if not valid or date_scores.empty:
            rows.append(
                {
                    "date": date.date(),
                    "dominant_regime": None,
                    "dominant_probability": None,
                    "second_regime": None,
                    "second_probability": None,
                    "confidence": health_row.get("confidence"),
                    "entropy": health_row.get("entropy"),
                    "valid_regime_count": int(health_row.get("valid_regime_count") or 0),
                    "valid": False,
                    "reason": "below_min_valid_regimes"
                    if int(health_row.get("valid_regime_count") or 0) < config.min_valid_regimes
                    else health_row.get("reason", "invalid"),
                }
            )
            continue
        top = date_scores.iloc[0]
        second = date_scores.iloc[1] if len(date_scores) > 1 else None
        rows.append(
            {
                "date": date.date(),
                "dominant_regime": top["regime_id"],
                "dominant_probability": float(top["probability"]),
                "second_regime": None if second is None else second["regime_id"],
                "second_probability": None if second is None else float(second["probability"]),
                "confidence": health_row.get("confidence"),
                "entropy": health_row.get("entropy"),
                "valid_regime_count": int(health_row.get("valid_regime_count") or 0),
                "valid": True,
                "reason": "revised_data_diagnostic",
            }
        )
    return pd.DataFrame(rows, columns=_timeline_columns())


def build_regime_transitions(timeline: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    previous = None
    for row in timeline.sort_values("date").to_dict(orient="records"):
        if not row.get("valid") or row.get("dominant_regime") is None:
            continue
        if previous is None:
            previous = row
            continue
        if row["dominant_regime"] != previous["dominant_regime"]:
            rows.append(
                {
                    "transition_date": row["date"],
                    "from_regime": previous["dominant_regime"],
                    "to_regime": row["dominant_regime"],
                    "from_probability": previous["dominant_probability"],
                    "to_probability": row["dominant_probability"],
                    "confidence": row["confidence"],
                    "reason": "dominant_regime_changed",
                }
            )
        previous = row
    return pd.DataFrame(rows, columns=_transition_columns())


def build_diagnostic_summary(
    timeline: pd.DataFrame,
    transitions: pd.DataFrame,
    config: HistoricalDiagnosticConfig,
) -> dict:
    if timeline.empty:
        return {
            "start_date": config.start_date.isoformat(),
            "end_date": None if config.end_date is None else config.end_date.isoformat(),
            "mode": config.mode,
            "valid_date_count": 0,
            "invalid_date_count": 0,
            "regime_switch_count": 0,
            "average_regime_duration": None,
            "average_confidence": None,
            "dominant_regime_distribution": {},
            "low_confidence_period_count": 0,
            "label": "historical revised-data diagnostic, not a point-in-time backtest",
        }
    valid = timeline[timeline["valid"]].copy()
    invalid = timeline[~timeline["valid"]]
    distribution = (
        valid["dominant_regime"].value_counts(normalize=True).sort_index().to_dict()
        if not valid.empty
        else {}
    )
    average_duration = _average_regime_duration(valid)
    return {
        "start_date": str(timeline["date"].min()),
        "end_date": str(timeline["date"].max()),
        "mode": config.mode,
        "valid_date_count": int(len(valid)),
        "invalid_date_count": int(len(invalid)),
        "regime_switch_count": int(len(transitions)),
        "average_regime_duration": average_duration,
        "average_confidence": None
        if valid.empty
        else float(pd.to_numeric(valid["confidence"], errors="coerce").mean()),
        "dominant_regime_distribution": {
            key: float(value) for key, value in distribution.items()
        },
        "low_confidence_period_count": int(
            (pd.to_numeric(valid["confidence"], errors="coerce") < config.low_confidence_threshold).sum()
        ),
        "label": "historical revised-data diagnostic, not a point-in-time backtest",
    }


def summary_to_frame(summary: dict) -> pd.DataFrame:
    row = summary.copy()
    row["dominant_regime_distribution"] = json.dumps(
        row["dominant_regime_distribution"], sort_keys=True
    )
    return pd.DataFrame([row])


def _average_regime_duration(valid_timeline: pd.DataFrame) -> float | None:
    if valid_timeline.empty:
        return None
    durations: list[int] = []
    current_regime = None
    current_duration = 0
    for row in valid_timeline.sort_values("date").to_dict(orient="records"):
        regime = row["dominant_regime"]
        if current_regime is None:
            current_regime = regime
            current_duration = 1
            continue
        if regime == current_regime:
            current_duration += 1
        else:
            durations.append(current_duration)
            current_regime = regime
            current_duration = 1
    durations.append(current_duration)
    return float(sum(durations) / len(durations))


def _timeline_columns() -> list[str]:
    return [
        "date",
        "dominant_regime",
        "dominant_probability",
        "second_regime",
        "second_probability",
        "confidence",
        "entropy",
        "valid_regime_count",
        "valid",
        "reason",
    ]


def _transition_columns() -> list[str]:
    return [
        "transition_date",
        "from_regime",
        "to_regime",
        "from_probability",
        "to_probability",
        "confidence",
        "reason",
    ]
