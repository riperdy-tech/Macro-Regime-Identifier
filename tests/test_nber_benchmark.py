"""Tests for the NBER recession benchmark harness."""

from __future__ import annotations

import pandas as pd

from macro_engine.evaluation.nber import (
    NberBenchmarkConfig,
    NberRecession,
    _auroc,
    build_markdown_report,
    build_monthly_benchmark_frame,
    load_nber_benchmark_config,
    run_nber_benchmark,
)


def _config(**overrides) -> NberBenchmarkConfig:
    values = {
        "recessions": [NberRecession(start="2020-02", end="2020-04")],
        "recession_regime_ids": ["recession"],
        "probability_thresholds": [0.25],
        "detection_threshold": 0.25,
        "lead_lag_window_months": 3,
    }
    values.update(overrides)
    return NberBenchmarkConfig(**values)


def _scores(months: dict[str, float]) -> pd.DataFrame:
    rows = []
    for month, probability in months.items():
        rows.append(
            {
                "regime_id": "recession",
                "date": f"{month}-01",
                "raw_score": 0.0,
                "probability": probability,
                "rank": 1,
                "valid": True,
                "reason": "ok",
            }
        )
        rows.append(
            {
                "regime_id": "goldilocks",
                "date": f"{month}-01",
                "raw_score": 0.0,
                "probability": 1.0 - probability,
                "rank": 2,
                "valid": True,
                "reason": "ok",
            }
        )
    return pd.DataFrame(rows)


def _timeline(months: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": f"{month}-01",
                "raw_dominant_regime": regime,
                "reported_regime": regime,
                "valid": True,
            }
            for month, regime in months.items()
        ]
    )


def test_monthly_frame_marks_nber_months():
    config = _config()
    monthly = build_monthly_benchmark_frame(
        _scores({"2020-01": 0.1, "2020-02": 0.6, "2020-05": 0.2}),
        _timeline({"2020-01": "goldilocks", "2020-02": "recession", "2020-05": "goldilocks"}),
        config,
    )
    flags = dict(zip(monthly["month"], monthly["in_nber_recession"]))
    assert flags == {"2020-01": False, "2020-02": True, "2020-05": False}
    probabilities = dict(zip(monthly["month"], monthly["recession_probability"]))
    assert probabilities["2020-02"] == 0.6


def test_perfect_separation_gives_auroc_one():
    months_probability = {
        "2019-11": 0.05,
        "2019-12": 0.10,
        "2020-01": 0.12,
        "2020-02": 0.70,
        "2020-03": 0.80,
        "2020-04": 0.75,
        "2020-05": 0.15,
    }
    months_regime = {
        month: ("recession" if probability >= 0.5 else "goldilocks")
        for month, probability in months_probability.items()
    }
    summary = run_nber_benchmark(
        _scores(months_probability), _timeline(months_regime), _config()
    )
    assert summary["status"] == "ok"
    assert summary["auroc"] == 1.0
    threshold = summary["threshold_metrics"][0]
    assert threshold["recession_hit_rate"] == 1.0
    assert threshold["expansion_flag_rate"] == 0.0
    labels = summary["label_metrics"]["raw_dominant"]
    assert labels["recession_hit_rate"] == 1.0
    assert labels["expansion_flag_rate"] == 0.0


def test_detection_lead_lag():
    # Probability crosses the threshold one month before the NBER start.
    months_probability = {
        "2019-12": 0.10,
        "2020-01": 0.30,
        "2020-02": 0.70,
        "2020-03": 0.80,
        "2020-04": 0.75,
    }
    months_regime = dict.fromkeys(months_probability, "goldilocks")
    summary = run_nber_benchmark(
        _scores(months_probability), _timeline(months_regime), _config()
    )
    detection = summary["recession_detection"][0]
    assert detection["detected_month"] == "2020-01"
    assert detection["lead_lag_months"] == -1


def test_missed_recession_reports_none():
    months_probability = {"2020-01": 0.05, "2020-02": 0.06, "2020-03": 0.04, "2020-04": 0.05}
    months_regime = dict.fromkeys(months_probability, "goldilocks")
    summary = run_nber_benchmark(
        _scores(months_probability), _timeline(months_regime), _config()
    )
    detection = summary["recession_detection"][0]
    assert detection["detected_month"] is None
    assert detection["lead_lag_months"] is None


def test_no_overlap_reports_insufficient():
    summary = run_nber_benchmark(
        _scores({"2026-01": 0.2}),
        _timeline({"2026-01": "goldilocks"}),
        _config(),
    )
    assert summary["status"] == "insufficient_overlap"
    assert summary["auroc"] is None


def test_auroc_handles_ties():
    assert _auroc([0.5, 0.5, 0.5, 0.5], [True, False, True, False]) == 0.5


def test_markdown_report_contains_disclaimer_and_tables():
    months_probability = {"2020-01": 0.1, "2020-02": 0.7, "2020-03": 0.8, "2020-04": 0.7, "2020-05": 0.1}
    months_regime = {
        month: ("recession" if probability >= 0.5 else "goldilocks")
        for month, probability in months_probability.items()
    }
    summary = run_nber_benchmark(
        _scores(months_probability), _timeline(months_regime), _config()
    )
    report = build_markdown_report(summary)
    assert "not investment advice" in report
    assert "Probability Thresholds" in report
    assert "Per-Recession Detection" in report
    assert "not a point-in-time backtest" in report


def test_production_benchmark_config_loads():
    config = load_nber_benchmark_config("config/nber_recessions.yaml")
    assert len(config.recessions) >= 4
    assert config.recession_regime_ids == ["recession"]
    starts = [recession.start for recession in config.recessions]
    assert "2007-12" in starts
