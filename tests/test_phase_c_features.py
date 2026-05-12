from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.features.config import FeatureDefinition, load_feature_config
from macro_engine.features.feature_builder import (
    apply_feature_normalization,
    apply_feature_transform,
    build_features_from_raw,
)
from macro_engine.ingest.schemas import IngestionSource
from macro_engine.storage.duckdb_store import DuckDBStore


def _monthly_source(series_id: str = "TEST", enabled: bool = True) -> IngestionSource:
    return IngestionSource(
        series_id=series_id,
        name=series_id,
        provider="FRED",
        dimension="test",
        frequency="monthly",
        required=False,
        enabled=enabled,
        reason_disabled=None if enabled else "disabled_for_test",
        stale_after_days=45,
        unusable_after_days=120,
    )


def _feature(
    feature_id: str = "test_feature",
    series_id: str = "TEST",
    transform: str = "level",
    normalization: str = "none",
    enabled: bool = True,
    min_observations: int = 3,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id,
        series_id=series_id,
        transform=transform,
        normalization=normalization,
        direction="higher_is_test_positive",
        enabled=enabled,
        reason_disabled=None if enabled else "disabled_for_test",
        min_observations=min_observations,
    )


def _raw_monthly(series_id: str = "TEST", periods: int = 24) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=periods, freq="MS")
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": dates,
            "value": [float(index + 1) for index in range(periods)],
            "realtime_start": [pd.Timestamp("2026-05-12").date()] * periods,
            "realtime_end": [pd.Timestamp("9999-12-31").date()] * periods,
            "source": ["FRED"] * periods,
            "fetched_at": [pd.Timestamp("2026-05-12")] * periods,
            "frequency": ["monthly"] * periods,
            "units": ["Index"] * periods,
        }
    )


def test_level_transform_works():
    values = pd.Series([1.0, 2.0, 3.0])

    result = apply_feature_transform(values, "level", "monthly")

    assert result.tolist() == [1.0, 2.0, 3.0]


def test_diff_6m_transform_works():
    values = pd.Series([float(index) for index in range(7)])

    result = apply_feature_transform(values, "diff_6m", "monthly")

    assert result.iloc[6] == 6.0


def test_yoy_pct_change_works():
    values = pd.Series([100.0] * 12 + [112.0])

    result = apply_feature_transform(values, "yoy_pct_change", "monthly")

    assert result.iloc[12] == pytest.approx(12.0)


def test_rolling_z_5y_uses_no_future_data():
    dates = pd.Series(pd.date_range("2020-01-01", periods=72, freq="MS"))
    original = pd.Series([float(index) for index in range(72)])
    mutated_future = original.copy()
    mutated_future.iloc[-1] = 10000.0

    normalized_original, _ = apply_feature_normalization(
        original, dates, "rolling_z_5y", "monthly", min_observations=12
    )
    normalized_mutated, _ = apply_feature_normalization(
        mutated_future, dates, "rolling_z_5y", "monthly", min_observations=12
    )

    assert normalized_original.iloc[30] == pytest.approx(normalized_mutated.iloc[30])


def test_insufficient_history_is_explicitly_invalid():
    result = build_features_from_raw(
        _raw_monthly(periods=5),
        [_monthly_source()],
        [_feature(transform="diff_6m", normalization="rolling_z_5y", min_observations=6)],
    )

    assert result.features["valid"].sum() == 0
    assert set(result.features["reason"]) == {"insufficient_transform_history"}
    assert bool(result.feature_health.iloc[0]["usable"]) is False


def test_disabled_source_does_not_produce_active_features():
    result = build_features_from_raw(
        _raw_monthly(periods=12),
        [_monthly_source(enabled=False)],
        [_feature()],
    )

    assert len(result.features) == 1
    assert bool(result.features.iloc[0]["valid"]) is False
    assert result.features.iloc[0]["reason"] == "disabled_source"


def test_duplicate_feature_id_fails_validation(tmp_path):
    config_path = tmp_path / "features.yaml"
    config_path.write_text(
        """
sources:
  - series_id: TEST
    name: Test
    provider: FRED
    dimension: test
    frequency: monthly
    required: false
    enabled: true
    stale_after_days: 45
    unusable_after_days: 120
features:
  - feature_id: duplicate
    series_id: TEST
    transform: level
    normalization: none
    direction: higher_is_test_positive
  - feature_id: duplicate
    series_id: TEST
    transform: diff_6m
    normalization: none
    direction: higher_is_test_positive
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate feature_id"):
        load_feature_config(config_path)


def test_one_source_can_support_multiple_feature_ids():
    result = build_features_from_raw(
        _raw_monthly(periods=24),
        [_monthly_source()],
        [
            _feature(feature_id="test_level", transform="level"),
            _feature(feature_id="test_diff_6m", transform="diff_6m"),
        ],
    )

    assert set(result.feature_health["feature_id"]) == {"test_level", "test_diff_6m"}
    assert bool(result.feature_health.set_index("feature_id").loc["test_level", "usable"]) is True
    assert bool(result.feature_health.set_index("feature_id").loc["test_diff_6m", "usable"]) is True


def test_feature_cli_commands_work(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    raw = pd.concat(
        [
            _raw_monthly("INDPRO", 90),
            _raw_monthly("PAYEMS", 90),
            _raw_monthly("UNRATE", 90),
            _raw_monthly("CPIAUCSL", 90),
            _raw_monthly("PCEPI", 90),
            _raw_monthly("FEDFUNDS", 130),
        ],
        ignore_index=True,
    )
    store.upsert_raw_observations(raw)
    runner = CliRunner()

    build_result = runner.invoke(
        app,
        [
            "build-features",
            "--config",
            "config/phase_b_sources.yaml",
            "--db-path",
            str(db_path),
            "--parquet-dir",
            str(tmp_path / "fred"),
        ],
    )
    inspect_result = runner.invoke(
        app,
        ["inspect-feature", "unemployment_6m_change_z", "--db-path", str(db_path)],
    )
    health_result = runner.invoke(app, ["feature-health", "--db-path", str(db_path)])

    assert build_result.exit_code == 0
    assert inspect_result.exit_code == 0
    assert health_result.exit_code == 0
    assert "unemployment_6m_change_z" in inspect_result.output
    assert Path(tmp_path / "fred" / "features.parquet").exists()
