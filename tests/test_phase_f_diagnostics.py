from __future__ import annotations

import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.diagnostics.config import (
    HistoricalDiagnosticConfig,
    load_historical_diagnostic_config,
)
from macro_engine.diagnostics.runner import run_historical_diagnostic, summary_to_frame
from macro_engine.storage.duckdb_store import DuckDBStore


def _regime_scores() -> pd.DataFrame:
    rows = []
    data = [
        ("2026-01-01", "goldilocks", 0.60, 1, True),
        ("2026-01-01", "recession", 0.30, 2, True),
        ("2026-01-01", "stagflation", 0.10, 3, True),
        ("2026-02-01", "goldilocks", 0.55, 1, True),
        ("2026-02-01", "recession", 0.35, 2, True),
        ("2026-02-01", "stagflation", 0.10, 3, True),
        ("2026-03-01", "recession", 0.58, 1, True),
        ("2026-03-01", "goldilocks", 0.32, 2, True),
        ("2026-03-01", "stagflation", 0.10, 3, True),
        ("2026-04-01", "recession", None, None, False),
        ("2026-04-01", "goldilocks", None, None, False),
    ]
    for date, regime, probability, rank, valid in data:
        rows.append(
            {
                "regime_id": regime,
                "date": date,
                "raw_score": probability,
                "probability": probability,
                "rank": rank,
                "valid_dimension_count": 5 if valid else 0,
                "configured_dimension_count": 5,
                "coverage_ratio": 1.0 if valid else 0.0,
                "valid": valid,
                "reason": "ok" if valid else "below_min_valid_dimensions",
            }
        )
    return pd.DataFrame(rows)


def _regime_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "valid": True,
                "dominant_regime": "goldilocks",
                "dominant_probability": 0.60,
                "confidence": 0.30,
                "entropy": 0.9,
                "valid_regime_count": 3,
                "reason": "ok",
            },
            {
                "date": "2026-02-01",
                "valid": True,
                "dominant_regime": "goldilocks",
                "dominant_probability": 0.55,
                "confidence": 0.20,
                "entropy": 1.0,
                "valid_regime_count": 3,
                "reason": "ok",
            },
            {
                "date": "2026-03-01",
                "valid": True,
                "dominant_regime": "recession",
                "dominant_probability": 0.58,
                "confidence": 0.26,
                "entropy": 0.95,
                "valid_regime_count": 3,
                "reason": "ok",
            },
            {
                "date": "2026-04-01",
                "valid": False,
                "dominant_regime": None,
                "dominant_probability": None,
                "confidence": None,
                "entropy": None,
                "valid_regime_count": 0,
                "reason": "no_valid_regimes",
            },
        ]
    )


def _config(min_valid_regimes: int = 2) -> HistoricalDiagnosticConfig:
    return HistoricalDiagnosticConfig(
        start_date="2026-01-01",
        end_date="2026-04-01",
        mode="revised_data",
        min_valid_regimes=min_valid_regimes,
        low_confidence_threshold=0.25,
    )


def test_historical_diagnostic_config_validates():
    config = load_historical_diagnostic_config("config/phase_b_sources.yaml")

    assert config.mode == "revised_data"
    assert config.smoothing.enabled is False


def test_timeline_stores_dominant_second_confidence_entropy_and_invalid_dates():
    result = run_historical_diagnostic(_regime_scores(), _regime_health(), _config())
    timeline = result.timeline.set_index("date")

    jan = timeline.loc[pd.Timestamp("2026-01-01").date()]
    apr = timeline.loc[pd.Timestamp("2026-04-01").date()]
    assert jan["dominant_regime"] == "goldilocks"
    assert jan["second_regime"] == "recession"
    assert jan["confidence"] == 0.30
    assert jan["entropy"] == 0.9
    assert bool(apr["valid"]) is False
    assert apr["reason"] == "below_min_valid_regimes"


def test_transitions_detect_change_and_ignore_no_change():
    result = run_historical_diagnostic(_regime_scores(), _regime_health(), _config())

    assert len(result.transitions) == 1
    transition = result.transitions.iloc[0]
    assert transition["from_regime"] == "goldilocks"
    assert transition["to_regime"] == "recession"
    assert transition["transition_date"] == pd.Timestamp("2026-03-01").date()


def test_no_transition_when_dominant_regime_does_not_change():
    health = _regime_health()
    health.loc[health["date"] == "2026-03-01", "dominant_regime"] = "goldilocks"
    scores = _regime_scores()
    scores.loc[(scores["date"] == "2026-03-01") & (scores["regime_id"] == "goldilocks"), "probability"] = 0.58
    scores.loc[(scores["date"] == "2026-03-01") & (scores["regime_id"] == "recession"), "probability"] = 0.32

    result = run_historical_diagnostic(scores, health, _config())

    assert result.transitions.empty


def test_summary_metrics_are_deterministic():
    result = run_historical_diagnostic(_regime_scores(), _regime_health(), _config())
    summary = result.summary

    assert summary["mode"] == "revised_data"
    assert summary["valid_date_count"] == 3
    assert summary["invalid_date_count"] == 1
    assert summary["regime_switch_count"] == 1
    assert summary["average_regime_duration"] == pytest.approx(1.5)
    assert summary["average_confidence"] == pytest.approx((0.30 + 0.20 + 0.26) / 3)
    assert summary["low_confidence_period_count"] == 1
    assert "not a point-in-time backtest" in summary["label"]


def test_diagnostic_rows_are_stored(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    result = run_historical_diagnostic(_regime_scores(), _regime_health(), _config())

    store.replace_diagnostic_outputs(
        result.timeline,
        result.transitions,
        summary_to_frame(result.summary),
    )

    assert len(store.read_table("historical_regime_timeline")) == 4
    assert len(store.read_table("regime_transitions")) == 1
    summary = store.read_table("diagnostic_summary").iloc[0]
    assert json.loads(summary["dominant_regime_distribution"])["goldilocks"] == pytest.approx(2 / 3)


def test_diagnostic_cli_commands_work(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_regime_outputs(pd.DataFrame(), _regime_scores(), _regime_health())
    runner = CliRunner()

    run_result = runner.invoke(
        app,
        [
            "run-historical-diagnostic",
            "--config",
            "config/phase_b_sources.yaml",
            "--db-path",
            str(db_path),
            "--parquet-dir",
            str(tmp_path / "fred"),
        ],
    )
    timeline_result = runner.invoke(app, ["regime-timeline", "--db-path", str(db_path)])
    transitions_result = runner.invoke(app, ["regime-transitions", "--db-path", str(db_path)])
    summary_result = runner.invoke(app, ["diagnostic-summary", "--db-path", str(db_path)])

    assert run_result.exit_code == 0
    assert timeline_result.exit_code == 0
    assert transitions_result.exit_code == 0
    assert summary_result.exit_code == 0
    assert "historical revised-data diagnostic" in run_result.output
    assert "goldilocks" in timeline_result.output
    assert "recession" in transitions_result.output
