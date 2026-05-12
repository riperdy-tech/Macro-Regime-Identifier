from __future__ import annotations

import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.dimensions.config import DimensionDefinition, load_dimension_config
from macro_engine.dimensions.scoring import build_dimensions_from_features
from macro_engine.storage.duckdb_store import DuckDBStore


def _dimension(
    min_valid_features: int = 2,
    min_coverage_ratio: float = 0.6,
) -> DimensionDefinition:
    return DimensionDefinition.model_validate(
        {
            "dimension_id": "growth_momentum",
            "enabled": True,
            "required_for_regime": True,
            "min_valid_features": min_valid_features,
            "min_coverage_ratio": min_coverage_ratio,
            "features": [
                {"feature_id": "feature_a", "weight": 0.6, "polarity": "positive"},
                {"feature_id": "feature_b", "weight": 0.4, "polarity": "negative"},
            ],
        }
    )


def _features(valid_b: bool = True) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "feature_id": "feature_a",
                "series_id": "A",
                "date": "2026-01-01",
                "raw_value": 1.0,
                "transformed_value": 1.0,
                "normalized_value": 1.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-01-01",
                "window_end": "2026-01-01",
                "valid": True,
                "reason": "ok",
            },
            {
                "feature_id": "feature_b",
                "series_id": "B",
                "date": "2026-01-01",
                "raw_value": 2.0,
                "transformed_value": 2.0,
                "normalized_value": 2.0 if valid_b else None,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-01-01",
                "window_end": "2026-01-01",
                "valid": valid_b,
                "reason": "ok" if valid_b else "insufficient_normalization_history",
            },
        ]
    )


def test_dimension_config_validates():
    config = load_dimension_config("config/phase_b_sources.yaml")

    assert {dimension.dimension_id for dimension in config.dimensions} >= {
        "growth_momentum",
        "inflation_pressure",
        "policy_stance",
        "credit_liquidity",
        "yield_curve",
    }


def test_unknown_feature_id_fails_validation(tmp_path):
    config_path = tmp_path / "bad.yaml"
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
  - feature_id: known_feature
    series_id: TEST
    transform: level
    normalization: none
    direction: higher_is_test_positive
dimensions:
  - dimension_id: bad_dimension
    enabled: true
    required_for_regime: true
    min_valid_features: 1
    min_coverage_ratio: 0.5
    features:
      - feature_id: missing_feature
        weight: 1.0
        polarity: positive
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown feature_id"):
        load_dimension_config(config_path)


def test_duplicate_dimension_id_fails_validation(tmp_path):
    config_path = tmp_path / "bad.yaml"
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
  - feature_id: known_feature
    series_id: TEST
    transform: level
    normalization: none
    direction: higher_is_test_positive
dimensions:
  - dimension_id: duplicate
    enabled: true
    required_for_regime: true
    min_valid_features: 1
    min_coverage_ratio: 0.5
    features:
      - feature_id: known_feature
        weight: 1.0
        polarity: positive
  - dimension_id: duplicate
    enabled: true
    required_for_regime: true
    min_valid_features: 1
    min_coverage_ratio: 0.5
    features:
      - feature_id: known_feature
        weight: 1.0
        polarity: positive
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate dimension_id"):
        load_dimension_config(config_path)


def test_positive_and_negative_polarity_apply_correctly():
    result = build_dimensions_from_features(_features(), [_dimension()])
    contributions = result.contributions.set_index("feature_id")

    assert contributions.loc["feature_a", "signed_value"] == 1.0
    assert contributions.loc["feature_b", "signed_value"] == -2.0
    assert result.dimension_scores.iloc[0]["score"] == pytest.approx(-0.2)


def test_missing_feature_reduces_coverage_and_invalidates_below_min_features():
    result = build_dimensions_from_features(_features(valid_b=False), [_dimension()])
    score = result.dimension_scores.iloc[0]

    assert score["valid_feature_count"] == 1
    assert score["coverage_ratio"] == pytest.approx(0.6)
    assert bool(score["valid"]) is False
    assert score["reason"] == "below_min_valid_features"


def test_dimension_invalid_when_below_coverage_ratio():
    result = build_dimensions_from_features(
        _features(valid_b=False),
        [_dimension(min_valid_features=1, min_coverage_ratio=0.75)],
    )
    score = result.dimension_scores.iloc[0]

    assert bool(score["valid"]) is False
    assert score["reason"] == "below_min_coverage_ratio"


def test_remaining_weights_renormalize_only_when_coverage_sufficient():
    result = build_dimensions_from_features(
        _features(valid_b=False),
        [_dimension(min_valid_features=1, min_coverage_ratio=0.50)],
    )
    score = result.dimension_scores.iloc[0]
    contribution = result.contributions[result.contributions["feature_id"] == "feature_a"].iloc[0]

    assert bool(score["valid"]) is True
    assert contribution["normalized_weight"] == pytest.approx(1.0)
    assert score["score"] == pytest.approx(1.0)


def test_contribution_score_and_health_rows_are_stored(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    result = build_dimensions_from_features(_features(), [_dimension()])

    store.replace_dimension_outputs(
        result.contributions,
        result.dimension_scores,
        result.dimension_health,
    )

    assert len(store.read_table("dimension_feature_contributions")) == 2
    assert len(store.read_table("dimension_scores")) == 1
    assert len(store.read_table("dimension_health")) == 1


def test_dimension_cli_commands_work(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    features = _features().copy()
    features["feature_id"] = features["feature_id"].replace(
        {
            "feature_a": "industrial_production_yoy_z",
            "feature_b": "payrolls_yoy_z",
        }
    )
    extra = features.iloc[[0]].copy()
    extra["feature_id"] = "unemployment_6m_change_z"
    extra["normalized_value"] = -0.5
    features = pd.concat([features, extra], ignore_index=True)
    store.upsert_features(features)
    runner = CliRunner()

    build_result = runner.invoke(
        app,
        [
            "build-dimensions",
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
        ["inspect-dimension", "growth_momentum", "--db-path", str(db_path)],
    )
    health_result = runner.invoke(app, ["dimension-health", "--db-path", str(db_path)])

    assert build_result.exit_code == 0
    assert inspect_result.exit_code == 0
    assert health_result.exit_code == 0
    assert "growth_momentum" in inspect_result.output
