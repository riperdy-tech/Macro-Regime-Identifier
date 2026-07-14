"""Tests for the validate-config CLI command against production configs."""

from typer.testing import CliRunner

from macro_engine.cli import app

runner = CliRunner()


def test_validate_config_passes_on_production_config():
    result = runner.invoke(app, ["validate-config"])
    assert result.exit_code == 0, result.output
    assert "Config valid" in result.output
    assert "config/phase_b_sources.yaml" in result.output


def test_validate_config_reports_enabled_counts():
    result = runner.invoke(app, ["validate-config"])
    assert result.exit_code == 0, result.output
    assert "5 regimes" in result.output
    assert "scoring_mode=calendar_asof" in result.output


def test_validate_config_accepts_explicit_config_path():
    result = runner.invoke(
        app, ["validate-config", "--config", "config/phase_b_sources.yaml"]
    )
    assert result.exit_code == 0, result.output


def test_validate_config_fails_on_non_pipeline_config():
    result = runner.invoke(
        app, ["validate-config", "--config", "config/daily_pipeline.yaml"]
    )
    assert result.exit_code == 1
    assert "Config invalid" in result.output


def test_validate_config_fails_on_missing_file():
    result = runner.invoke(
        app, ["validate-config", "--config", "config/does_not_exist.yaml"]
    )
    assert result.exit_code == 1
    assert "Config invalid" in result.output
