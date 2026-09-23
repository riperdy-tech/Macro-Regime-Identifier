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
                    "reported_regime": None,
                    "reported_regime_probability": None,
                    "reported_confidence": None,
                    "raw_dominant_regime": None,
                    "raw_dominant_probability": None,
                    "raw_confidence": health_row.get("confidence"),
                    "second_regime": None,
                    "second_probability": None,
                    "confidence": health_row.get("confidence"),
                    "coverage": health_row.get("coverage"),
                    "peakedness": health_row.get("peakedness"),
                    "entropy": health_row.get("entropy"),
                    "valid_regime_count": int(health_row.get("valid_regime_count") or 0),
                    "valid": False,
                    "transition_filter_applied": config.transition_filter.enabled,
                    "transition_filter_reason": "invalid",
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
                "reported_regime": top["regime_id"],
                "reported_regime_probability": float(top["probability"]),
                "reported_confidence": health_row.get("confidence"),
                "raw_dominant_regime": top["regime_id"],
                "raw_dominant_probability": float(top["probability"]),
                "raw_confidence": health_row.get("confidence"),
                "second_regime": None if second is None else second["regime_id"],
                "second_probability": None if second is None else float(second["probability"]),
                "confidence": health_row.get("confidence"),
                "coverage": health_row.get("coverage"),
                "peakedness": health_row.get("peakedness"),
                "entropy": health_row.get("entropy"),
                "valid_regime_count": int(health_row.get("valid_regime_count") or 0),
                "valid": True,
                "transition_filter_applied": config.transition_filter.enabled,
                "transition_filter_reason": "no_filter",
                "reason": "revised_data_diagnostic",
            }
        )
    timeline = pd.DataFrame(rows, columns=_timeline_columns())
    if config.transition_filter.enabled:
        timeline = _apply_transition_filter(timeline, regime_scores, config)
    return timeline


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
        "reported_regime",
        "reported_regime_probability",
        "reported_confidence",
        "raw_dominant_regime",
        "raw_dominant_probability",
        "raw_confidence",
        "second_regime",
        "second_probability",
        "confidence",
        "coverage",
        "peakedness",
        "entropy",
        "valid_regime_count",
        "valid",
        "transition_filter_applied",
        "transition_filter_reason",
        "reason",
    ]


def _apply_transition_filter(
    timeline: pd.DataFrame,
    regime_scores: pd.DataFrame,
    config: HistoricalDiagnosticConfig,
) -> pd.DataFrame:
    rows: list[dict] = []
    current_regime = None
    pending_regime = None
    pending_count = 0
    filter_config = config.transition_filter
    threshold = filter_config.min_confidence_to_switch
    margin_threshold = filter_config.margin_threshold
    scores = regime_scores.copy()
    scores["date"] = pd.to_datetime(scores["date"], errors="coerce")

    for row in timeline.sort_values("date").to_dict(orient="records"):
        filtered = row.copy()
        filtered["transition_filter_applied"] = True
        if not row.get("valid") or row.get("raw_dominant_regime") is None:
            filtered["transition_filter_reason"] = "invalid"
            rows.append(filtered)
            continue

        raw_regime = row["raw_dominant_regime"]
        raw_confidence = float(row.get("raw_confidence") or 0.0)
        raw_peakedness = float(row.get("peakedness") or 0.0)
        date = pd.Timestamp(row["date"])
        if current_regime is None:
            current_regime = raw_regime
            pending_regime = None
            pending_count = 0
            filtered["transition_filter_reason"] = "initial_state"
        elif raw_regime == current_regime:
            pending_regime = None
            pending_count = 0
            filtered["transition_filter_reason"] = "raw_signal_confirmed"
        else:
            # C4a (MRI_S1_APPROVAL.md §3): the confirmation counter counts consecutive
            # months of the same raw leader and is NOT reset by a sub-threshold month --
            # only a change of raw leader (or a revert to current_regime, handled above)
            # resets it. Previously a single month below `threshold` reset the counter to
            # zero even when the raw leader was unchanged, so a persistent-but-quiet raw
            # leader (for example six straight months of `recession` at 2019-10..2020-03)
            # never accumulated enough consecutive months to confirm.
            pending_count = pending_count + 1 if pending_regime == raw_regime else 1
            pending_regime = raw_regime
            required_months = _required_confirmation_months(raw_confidence, filter_config)

            # C4b (MRI_S1_APPROVAL.md §4, measurement only): margin hysteresis, gated
            # entirely behind margin_threshold so the default (0.0) reproduces C4a
            # exactly. `margin` is the challenger's lead over the incumbent's own
            # probability on this date -- the quantity the entropy-only filter cannot
            # see, since peakedness gates on the shape of the whole distribution, not on
            # the gap between the top two.
            margin_ok = True
            if margin_threshold > 0:
                incumbent_probability = _probability_for_regime(scores, date, current_regime)
                raw_leader_probability = row.get("raw_dominant_probability")
                margin = (
                    float(raw_leader_probability) - float(incumbent_probability)
                    if incumbent_probability is not None and raw_leader_probability is not None
                    else None
                )
                margin_ok = margin is not None and margin >= margin_threshold
                if margin is not None and margin >= 2 * margin_threshold and raw_peakedness >= 0.15:
                    required_months = 1

            if raw_confidence >= threshold and margin_ok:
                if pending_count >= required_months:
                    current_regime = raw_regime
                    pending_regime = None
                    pending_count = 0
                    filtered["transition_filter_reason"] = "switch_confirmed"
                else:
                    filtered["transition_filter_reason"] = "awaiting_confirmation"
            elif (
                margin_threshold > 0
                and filter_config.persistence_months is not None
                and pending_count >= filter_config.persistence_months
            ):
                # C4b persistence fallback: the same raw leader has persisted long enough
                # that a margin which never quite clears should stop freezing the label.
                current_regime = raw_regime
                pending_regime = None
                pending_count = 0
                filtered["transition_filter_reason"] = "switch_confirmed_persistence"
            elif margin_threshold > 0 and not margin_ok:
                filtered["transition_filter_reason"] = "held_below_margin"
            else:
                filtered["transition_filter_reason"] = "held_below_min_confidence"

        reported_probability = _probability_for_regime(scores, date, current_regime)
        filtered["dominant_regime"] = current_regime
        filtered["reported_regime"] = current_regime
        filtered["dominant_probability"] = reported_probability
        filtered["reported_regime_probability"] = reported_probability
        # Confidence is the peakedness of the regime distribution; it does not
        # depend on WHICH regime the transition filter reports. Using the raw
        # peakedness keeps reported_confidence consistent and never negative
        # (the old gap formula went negative when a held regime was not the
        # raw leader).
        filtered["reported_confidence"] = raw_confidence
        filtered["confidence"] = raw_confidence
        rows.append(filtered)

    return pd.DataFrame(rows, columns=_timeline_columns())


def _required_confirmation_months(
    confidence: float,
    filter_config,
) -> int:
    if filter_config.only_when_confidence_below is None:
        return filter_config.confirmation_months
    if confidence < filter_config.only_when_confidence_below:
        return filter_config.confirmation_months
    return 1


def _probability_for_regime(
    regime_scores: pd.DataFrame,
    date: pd.Timestamp,
    regime_id: str,
) -> float | None:
    rows = regime_scores[
        (regime_scores["date"] == date)
        & (regime_scores["regime_id"] == regime_id)
        & (regime_scores["probability"].notna())
    ]
    if rows.empty:
        return None
    return float(rows.iloc[0]["probability"])




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
