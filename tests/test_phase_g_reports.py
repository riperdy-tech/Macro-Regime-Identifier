from __future__ import annotations

import json

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.reports.config import ReportConfig, load_report_config
from macro_engine.reports.service import (
    write_current_regime_report,
    write_historical_diagnostic_report,
)
from macro_engine.reports.writer import (
    build_current_regime_report,
    build_historical_diagnostic_report,
    current_report_markdown,
    diagnostic_report_markdown,
)
from macro_engine.storage.duckdb_store import DuckDBStore


def _regime_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "regime_id": "goldilocks",
                "date": "2026-01-01",
                "raw_score": 0.8,
                "probability": 0.6,
                "rank": 1,
                "valid_dimension_count": 3,
                "configured_dimension_count": 3,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
            {
                "regime_id": "recession",
                "date": "2026-01-01",
                "raw_score": 0.2,
                "probability": 0.4,
                "rank": 2,
                "valid_dimension_count": 3,
                "configured_dimension_count": 3,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def _regime_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "valid": True,
                "dominant_regime": "goldilocks",
                "dominant_probability": 0.6,
                "confidence": 0.2,
                "entropy": 0.67,
                "valid_regime_count": 2,
                "reason": "ok",
            }
        ]
    )


def _regime_contributions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "regime_id": "goldilocks",
                "dimension_id": "growth_momentum",
                "date": "2026-01-01",
                "dimension_score": 1.0,
                "weight": 0.5,
                "normalized_weight": 0.5,
                "polarity": "positive",
                "transformed_dimension_value": 1.0,
                "contribution": 0.5,
                "valid": True,
                "reason": "ok",
            },
            {
                "regime_id": "goldilocks",
                "dimension_id": "inflation_pressure",
                "date": "2026-01-01",
                "dimension_score": 0.8,
                "weight": 0.5,
                "normalized_weight": 0.5,
                "polarity": "penalize_positive_only",
                "transformed_dimension_value": -0.8,
                "contribution": -0.4,
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def _dimension_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "dimension_id": "growth_momentum",
                "date": "2026-01-01",
                "score": 1.0,
                "valid_feature_count": 2,
                "configured_feature_count": 2,
                "total_configured_weight": 1.0,
                "used_weight": 1.0,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
            {
                "dimension_id": "policy_stance",
                "date": "2026-01-01",
                "score": None,
                "valid_feature_count": 0,
                "configured_feature_count": 2,
                "total_configured_weight": 1.0,
                "used_weight": 0.0,
                "coverage_ratio": 0.0,
                "valid": False,
                "reason": "below_min_valid_features",
            },
        ]
    )


def _dimension_contributions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "dimension_id": "growth_momentum",
                "feature_id": "industrial_production_yoy_z",
                "date": "2026-01-01",
                "normalized_value": 1.0,
                "weight": 1.0,
                "normalized_weight": 1.0,
                "polarity": "positive",
                "signed_value": 1.0,
                "contribution": 1.0,
                "valid": True,
                "reason": "ok",
            }
        ]
    )


def _feature_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "feature_id": "bad_feature",
                "series_id": "BAD",
                "enabled": True,
                "valid_count": 0,
                "invalid_count": 3,
                "latest_valid_date": None,
                "usable": False,
                "reason": "insufficient_normalization_history",
                "reason_counts": "{}",
            }
        ]
    )


def _source_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "series_id": "BAD",
                "last_observation_date": None,
                "days_since_last_observation": None,
                "expected_frequency": "monthly",
                "stale_flag": True,
                "missing_count": 0,
                "usable": False,
                "reason": "no_observations",
                "checked_at": "2026-01-01",
            }
        ]
    )


def _timeline() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "dominant_regime": "goldilocks",
                "dominant_probability": 0.6,
                "second_regime": "recession",
                "second_probability": 0.4,
                "confidence": 0.2,
                "entropy": 0.67,
                "valid_regime_count": 2,
                "valid": True,
                "reason": "revised_data_diagnostic",
            }
        ]
    )


def _transitions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "transition_date": "2026-01-01",
                "from_regime": "recession",
                "to_regime": "goldilocks",
                "from_probability": 0.5,
                "to_probability": 0.6,
                "confidence": 0.2,
                "reason": "dominant_regime_changed",
            }
        ]
    )


def _summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "start_date": "2026-01-01",
                "end_date": "2026-01-01",
                "mode": "revised_data",
                "valid_date_count": 1,
                "invalid_date_count": 0,
                "regime_switch_count": 1,
                "average_regime_duration": 1.0,
                "average_confidence": 0.2,
                "dominant_regime_distribution": json.dumps({"goldilocks": 1.0}),
                "low_confidence_period_count": 0,
                "label": "historical revised-data diagnostic, not a point-in-time backtest",
            }
        ]
    )


def _seed_store(db_path):
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_regime_outputs(_regime_contributions(), _regime_scores(), _regime_health())
    store.replace_dimension_outputs(_dimension_contributions(), _dimension_scores(), pd.DataFrame())
    store.upsert_feature_health(_feature_health())
    store.upsert_source_health(_source_health())
    store.replace_diagnostic_outputs(_timeline(), _transitions(), _summary())
    return store


def test_report_config_loads():
    config = load_report_config("config/phase_b_sources.yaml")

    assert config.output_dir == "outputs"
    assert config.max_contributors >= 1


def test_current_report_explains_from_contribution_rows():
    payload = build_current_regime_report(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        regime_contributions=_regime_contributions(),
        dimension_scores=_dimension_scores(),
        dimension_contributions=_dimension_contributions(),
        feature_health=_feature_health(),
        source_health=_source_health(),
        config=ReportConfig(max_contributors=3),
    )

    assert payload["dominant_regime"] == "goldilocks"
    assert payload["top_supporting_dimensions"][0]["dimension_id"] == "growth_momentum"
    assert payload["top_opposing_dimensions"][0]["dimension_id"] == "inflation_pressure"
    assert "growth_momentum supported" in "\n".join(payload["explanation"])
    assert "not investment advice" in payload["disclaimer"]


def test_current_markdown_is_deterministic():
    payload = build_current_regime_report(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        regime_contributions=_regime_contributions(),
        dimension_scores=_dimension_scores(),
        dimension_contributions=_dimension_contributions(),
        feature_health=_feature_health(),
        source_health=_source_health(),
        config=ReportConfig(max_contributors=3),
    )
    first = current_report_markdown(payload)
    second = current_report_markdown(payload)

    assert first == second
    assert "Current Macro Regime" in first
    assert "Data Health Warnings" in first


def test_diagnostic_report_labels_revised_data():
    payload = build_historical_diagnostic_report(
        timeline=_timeline(),
        transitions=_transitions(),
        summary=_summary(),
        config=ReportConfig(max_contributors=3),
    )
    markdown = diagnostic_report_markdown(payload)

    assert payload["mode"] == "revised_data"
    assert payload["dominant_regime_distribution"]["goldilocks"] == 1.0
    assert "not ALFRED/vintage point-in-time backtests" in payload["disclaimer"]
    assert markdown == diagnostic_report_markdown(payload)


def test_report_services_write_valid_json_and_markdown(tmp_path, monkeypatch):
    db_path = tmp_path / "macro.duckdb"
    _seed_store(db_path)
    output_dir = tmp_path / "outputs"
    config_path = tmp_path / "report_config.yaml"
    config_path.write_text(
        f"""
reports:
  output_dir: {output_dir.as_posix()}
  include_feature_details: true
  include_dimension_details: true
  include_diagnostic_summary: true
  max_contributors: 3
""",
        encoding="utf-8",
    )

    current_json, current_md = write_current_regime_report(config_path=config_path, db_path=db_path)
    diagnostic_json, diagnostic_md = write_historical_diagnostic_report(
        config_path=config_path,
        db_path=db_path,
    )

    assert json.loads(current_json.read_text(encoding="utf-8"))["dominant_regime"] == "goldilocks"
    assert "Current Macro Regime" in current_md.read_text(encoding="utf-8")
    assert json.loads(diagnostic_json.read_text(encoding="utf-8"))["mode"] == "revised_data"
    assert "Historical Diagnostic" in diagnostic_md.read_text(encoding="utf-8")


def test_report_cli_commands_work(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    _seed_store(db_path)
    output_dir = tmp_path / "outputs"
    config_path = tmp_path / "report_config.yaml"
    config_path.write_text(
        f"""
reports:
  output_dir: {output_dir.as_posix()}
  include_feature_details: true
  include_dimension_details: true
  include_diagnostic_summary: true
  max_contributors: 3
""",
        encoding="utf-8",
    )
    runner = CliRunner()

    current = runner.invoke(
        app,
        ["write-current-report", "--config", str(config_path), "--db-path", str(db_path)],
    )
    diagnostic = runner.invoke(
        app,
        ["write-diagnostic-report", "--config", str(config_path), "--db-path", str(db_path)],
    )

    assert current.exit_code == 0
    assert diagnostic.exit_code == 0
    assert (output_dir / "current_regime.json").exists()
    assert (output_dir / "current_regime.md").exists()
    assert (output_dir / "historical_diagnostic.json").exists()
    assert (output_dir / "historical_diagnostic.md").exists()
