"""NBER recession benchmark for the stored historical regime diagnostic.

Compares stored monthly regime probabilities and reported regime labels
against the public NBER business-cycle reference dates. This is a
revised-data sanity benchmark: it shows whether the configured formulas
line up with consensus recession history. It is not a point-in-time
backtest and it does not establish predictive value.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, Field, model_validator

from macro_engine.storage.duckdb_store import DuckDBStore

DISCLAIMER = (
    "This NBER benchmark is a revised-data diagnostic comparison against "
    "public NBER business-cycle dates. It is not a point-in-time backtest, "
    "not evidence of predictive value, and not investment advice."
)


class NberRecession(BaseModel):
    start: str = Field(pattern=r"^\d{4}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}$")

    @model_validator(mode="after")
    def end_not_before_start(self) -> NberRecession:
        if pd.Period(self.end, freq="M") < pd.Period(self.start, freq="M"):
            raise ValueError(f"NBER recession end {self.end} before start {self.start}")
        return self


class NberBenchmarkConfig(BaseModel):
    recessions: list[NberRecession]
    recession_regime_ids: list[str] = Field(default_factory=lambda: ["recession"])
    probability_thresholds: list[float] = Field(
        default_factory=lambda: [0.20, 0.25, 0.30, 0.35]
    )
    detection_threshold: float = Field(default=0.25, gt=0, lt=1)
    lead_lag_window_months: int = Field(default=9, ge=0)
    output_dir: str = "outputs"

    @model_validator(mode="after")
    def has_recessions(self) -> NberBenchmarkConfig:
        if not self.recessions:
            raise ValueError("nber_recessions must list at least one recession window")
        return self


def load_nber_benchmark_config(
    path: str | Path = "config/nber_recessions.yaml",
) -> NberBenchmarkConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    recessions = [NberRecession.model_validate(item) for item in data.get("nber_recessions", [])]
    benchmark = data.get("benchmark", {}) or {}
    return NberBenchmarkConfig(recessions=recessions, **benchmark)


def build_monthly_benchmark_frame(
    regime_scores: pd.DataFrame,
    timeline: pd.DataFrame,
    config: NberBenchmarkConfig,
) -> pd.DataFrame:
    """One row per valid timeline month: recession probability, labels, NBER flag."""
    if timeline.empty:
        return pd.DataFrame(
            columns=[
                "month",
                "recession_probability",
                "raw_dominant_regime",
                "reported_regime",
                "in_nber_recession",
            ]
        )
    scores = regime_scores.copy()
    scores["date"] = pd.to_datetime(scores["date"], errors="coerce")
    scores = scores[
        scores["regime_id"].isin(config.recession_regime_ids)
        & scores["valid"]
        & scores["probability"].notna()
    ]
    probability_by_month = (
        scores.assign(month=scores["date"].dt.to_period("M"))
        .groupby("month")["probability"]
        .sum()
        .to_dict()
    )

    frame = timeline.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[frame["valid"] & frame["date"].notna()].sort_values("date")
    months = frame["date"].dt.to_period("M")
    nber_months = _nber_month_set(config)
    return pd.DataFrame(
        {
            "month": months.astype(str),
            "recession_probability": [
                float(probability_by_month.get(month, 0.0)) for month in months
            ],
            "raw_dominant_regime": frame["raw_dominant_regime"].tolist(),
            "reported_regime": frame["reported_regime"].tolist(),
            "in_nber_recession": [month in nber_months for month in months],
        }
    )


def run_nber_benchmark(
    regime_scores: pd.DataFrame,
    timeline: pd.DataFrame,
    config: NberBenchmarkConfig,
) -> dict:
    monthly = build_monthly_benchmark_frame(regime_scores, timeline, config)
    recession_months = int(monthly["in_nber_recession"].sum()) if not monthly.empty else 0
    expansion_months = int(len(monthly) - recession_months)

    summary: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": "revised-data NBER benchmark, not a point-in-time backtest",
        "disclaimer": DISCLAIMER,
        "recession_regime_ids": config.recession_regime_ids,
        "month_count": int(len(monthly)),
        "nber_recession_month_count": recession_months,
        "expansion_month_count": expansion_months,
        "nber_recessions": [
            {"start": recession.start, "end": recession.end} for recession in config.recessions
        ],
    }
    if monthly.empty or recession_months == 0 or expansion_months == 0:
        summary["status"] = "insufficient_overlap"
        summary["auroc"] = None
        summary["threshold_metrics"] = []
        summary["label_metrics"] = {}
        summary["recession_detection"] = []
        return summary

    summary["status"] = "ok"
    summary["auroc"] = _auroc(
        monthly["recession_probability"].tolist(), monthly["in_nber_recession"].tolist()
    )
    summary["threshold_metrics"] = [
        _threshold_metrics(monthly, threshold) for threshold in config.probability_thresholds
    ]
    summary["label_metrics"] = {
        "raw_dominant": _label_metrics(monthly, "raw_dominant_regime", config),
        "reported": _label_metrics(monthly, "reported_regime", config),
    }
    summary["recession_detection"] = [
        _detection_for_recession(monthly, recession, config) for recession in config.recessions
    ]
    return summary


def run_stored_nber_benchmark(
    *,
    benchmark_config_path: str | Path = "config/nber_recessions.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    output_dir: str | Path | None = None,
) -> dict:
    config = load_nber_benchmark_config(benchmark_config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    summary = run_nber_benchmark(
        store.read_table("regime_scores"),
        store.read_table("historical_regime_timeline"),
        config,
    )
    target_dir = Path(output_dir) if output_dir is not None else Path(config.output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "nber_benchmark.json"
    md_path = target_dir / "nber_benchmark.md"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_markdown_report(summary), encoding="utf-8")
    summary["outputs"] = [str(json_path), str(md_path)]
    return summary


def build_markdown_report(summary: dict) -> str:
    lines: list[str] = []
    lines.append("# NBER Recession Benchmark")
    lines.append("")
    lines.append(summary["disclaimer"])
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- months evaluated: {summary['month_count']}")
    lines.append(f"- NBER recession months: {summary['nber_recession_month_count']}")
    lines.append(f"- expansion months: {summary['expansion_month_count']}")
    lines.append(f"- recession regime ids: {', '.join(summary['recession_regime_ids'])}")
    lines.append("")
    if summary.get("status") != "ok":
        lines.append(f"Status: {summary.get('status')}")
        lines.append("")
        return "\n".join(lines)

    auroc = summary.get("auroc")
    lines.append("## Separation")
    lines.append("")
    lines.append(
        f"- AUROC of monthly recession probability vs NBER months: "
        f"{auroc:.3f}" if auroc is not None else "- AUROC: n/a"
    )
    lines.append("")
    lines.append("## Probability Thresholds")
    lines.append("")
    lines.append("| threshold | recession months flagged | expansion months flagged |")
    lines.append("| --- | --- | --- |")
    for row in summary["threshold_metrics"]:
        lines.append(
            f"| {row['threshold']:.2f} "
            f"| {row['recession_hit_rate']:.1%} ({row['recession_hits']}/{row['recession_months']}) "
            f"| {row['expansion_flag_rate']:.1%} ({row['expansion_flags']}/{row['expansion_months']}) |"
        )
    lines.append("")
    lines.append("## Dominant-Label Agreement")
    lines.append("")
    lines.append("| label source | recession months labeled recession | expansion months labeled recession |")
    lines.append("| --- | --- | --- |")
    for name, metrics in summary["label_metrics"].items():
        lines.append(
            f"| {name} | {metrics['recession_hit_rate']:.1%} | {metrics['expansion_flag_rate']:.1%} |"
        )
    lines.append("")
    lines.append("## Per-Recession Detection")
    lines.append("")
    lines.append(
        f"First month at/above the detection threshold, searched from "
        f"{summary['recession_detection'][0]['window_months_before_start']} months before the "
        "NBER start through the NBER end. Negative lead = earlier than NBER start."
    )
    lines.append("")
    lines.append("| NBER window | detected month | lead/lag (months) |")
    lines.append("| --- | --- | --- |")
    for row in summary["recession_detection"]:
        detected = row["detected_month"] or "missed"
        lead = "n/a" if row["lead_lag_months"] is None else f"{row['lead_lag_months']:+d}"
        lines.append(f"| {row['start']} to {row['end']} | {detected} | {lead} |")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Inputs are revised FRED data with approximate publication lags.")
    lines.append("- NBER dates are themselves declared with long delays in real time.")
    lines.append("- Agreement with NBER history is a sanity check, not validation.")
    lines.append("")
    return "\n".join(lines)


def _nber_month_set(config: NberBenchmarkConfig) -> set[pd.Period]:
    months: set[pd.Period] = set()
    for recession in config.recessions:
        months.update(
            pd.period_range(recession.start, recession.end, freq="M").tolist()
        )
    return months


def _auroc(scores: list[float], labels: list[bool]) -> float | None:
    positives = sum(1 for label in labels if label)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    frame = pd.DataFrame({"score": scores, "label": labels})
    frame["rank"] = frame["score"].rank(method="average")
    positive_rank_sum = float(frame.loc[frame["label"], "rank"].sum())
    u_statistic = positive_rank_sum - positives * (positives + 1) / 2
    return float(u_statistic / (positives * negatives))


def _threshold_metrics(monthly: pd.DataFrame, threshold: float) -> dict:
    in_recession = monthly[monthly["in_nber_recession"]]
    in_expansion = monthly[~monthly["in_nber_recession"]]
    recession_hits = int((in_recession["recession_probability"] >= threshold).sum())
    expansion_flags = int((in_expansion["recession_probability"] >= threshold).sum())
    return {
        "threshold": threshold,
        "recession_months": int(len(in_recession)),
        "recession_hits": recession_hits,
        "recession_hit_rate": recession_hits / len(in_recession) if len(in_recession) else 0.0,
        "expansion_months": int(len(in_expansion)),
        "expansion_flags": expansion_flags,
        "expansion_flag_rate": expansion_flags / len(in_expansion) if len(in_expansion) else 0.0,
    }


def _label_metrics(monthly: pd.DataFrame, column: str, config: NberBenchmarkConfig) -> dict:
    labeled = monthly[column].isin(config.recession_regime_ids)
    in_recession = monthly["in_nber_recession"]
    recession_months = int(in_recession.sum())
    expansion_months = int((~in_recession).sum())
    recession_hits = int((labeled & in_recession).sum())
    expansion_flags = int((labeled & ~in_recession).sum())
    return {
        "recession_months": recession_months,
        "recession_hits": recession_hits,
        "recession_hit_rate": recession_hits / recession_months if recession_months else 0.0,
        "expansion_months": expansion_months,
        "expansion_flags": expansion_flags,
        "expansion_flag_rate": expansion_flags / expansion_months if expansion_months else 0.0,
    }


def _detection_for_recession(
    monthly: pd.DataFrame,
    recession: NberRecession,
    config: NberBenchmarkConfig,
) -> dict:
    start = pd.Period(recession.start, freq="M")
    end = pd.Period(recession.end, freq="M")
    window_start = start - config.lead_lag_window_months
    months = monthly.assign(period=pd.PeriodIndex(monthly["month"], freq="M"))
    window = months[
        (months["period"] >= window_start)
        & (months["period"] <= end)
        & (months["recession_probability"] >= config.detection_threshold)
    ].sort_values("period")
    detected_period = None if window.empty else window.iloc[0]["period"]
    return {
        "start": recession.start,
        "end": recession.end,
        "detection_threshold": config.detection_threshold,
        "window_months_before_start": config.lead_lag_window_months,
        "detected_month": None if detected_period is None else str(detected_period),
        "lead_lag_months": None
        if detected_period is None
        else int((detected_period - start).n),
    }
