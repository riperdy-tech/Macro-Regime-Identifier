from __future__ import annotations

import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
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


def _regime(
    regime_id: str = "test_regime",
    min_valid_dimensions: int = 2,
    min_coverage_ratio: float = 0.6,
) -> RegimeDefinition:
    return RegimeDefinition.model_validate(
        {
            "regime_id": regime_id,
            "enabled": True,
            "min_valid_dimensions": min_valid_dimensions,
            "min_coverage_ratio": min_coverage_ratio,
            "dimensions": [
                {"dimension_id": "growth_momentum", "weight": 0.6, "polarity": "positive"},
                {"dimension_id": "inflation_pressure", "weight": 0.4, "polarity": "negative"},
            ],
        }
    )


def _dimension_scores(valid_inflation: bool = True) -> pd.DataFrame:
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
                "dimension_id": "inflation_pressure",
                "date": "2026-01-01",
                "score": 2.0 if valid_inflation else None,
                "valid_feature_count": 2 if valid_inflation else 0,
                "configured_feature_count": 2,
                "total_configured_weight": 1.0,
                "used_weight": 1.0 if valid_inflation else 0.0,
                "coverage_ratio": 1.0 if valid_inflation else 0.0,
                "valid": valid_inflation,
                "reason": "ok" if valid_inflation else "below_min_valid_features",
            },
        ]
    )


def test_regime_config_validates():
    config = load_regime_config("config/phase_b_sources.yaml")

    assert {regime.regime_id for regime in config.regimes} == {
        "goldilocks",
        "reflation",
        "stagflation",
        "recession",
        "tightening",
    }
    assert config.scoring.softmax_temperature == 0.6


def test_unknown_dimension_id_fails_validation(tmp_path):
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
  - dimension_id: known_dimension
    enabled: true
    required_for_regime: true
    min_valid_features: 1
    min_coverage_ratio: 0.5
    features:
      - feature_id: known_feature
        weight: 1.0
        polarity: positive
regime_scoring:
  probability_method: softmax
  softmax_temperature: 1.0
regimes:
  - regime_id: bad
    enabled: true
    min_valid_dimensions: 1
    min_coverage_ratio: 0.5
    dimensions:
      - dimension_id: missing_dimension
        weight: 1.0
        polarity: positive
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown dimension_id"):
        load_regime_config(config_path)


def test_duplicate_regime_id_fails_validation(tmp_path):
    config_path = tmp_path / "bad.yaml"
    base = """
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
  - dimension_id: known_dimension
    enabled: true
    required_for_regime: true
    min_valid_features: 1
    min_coverage_ratio: 0.5
    features:
      - feature_id: known_feature
        weight: 1.0
        polarity: positive
regime_scoring:
  probability_method: softmax
  softmax_temperature: 1.0
regimes:
  - regime_id: duplicate
    enabled: true
    min_valid_dimensions: 1
    min_coverage_ratio: 0.5
    dimensions:
      - dimension_id: known_dimension
        weight: 1.0
        polarity: positive
  - regime_id: duplicate
    enabled: true
    min_valid_dimensions: 1
    min_coverage_ratio: 0.5
    dimensions:
      - dimension_id: known_dimension
        weight: 1.0
        polarity: positive
"""
    config_path.write_text(base, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate regime_id"):
        load_regime_config(config_path)


def test_regime_polarity_functions():
    assert transform_dimension_value(2.0, "positive") == 2.0
    assert transform_dimension_value(2.0, "negative") == -2.0
    assert transform_dimension_value(-2.0, "positive_only") == 0.0
    assert transform_dimension_value(-2.0, "negative_only") == 2.0
    assert transform_dimension_value(2.0, "penalize_positive_only") == -2.0
    assert transform_dimension_value(-2.0, "penalize_negative_only") == -2.0
    assert transform_dimension_value(0.5, "reward_near_zero") == -0.5


def test_regime_contributions_and_raw_score():
    result = build_regimes_from_dimensions(
        _dimension_scores(),
        [_regime()],
        RegimeScoringConfig(softmax_temperature=1.0),
    )
    contributions = result.contributions.set_index("dimension_id")
    score = result.regime_scores.iloc[0]

    assert contributions.loc["growth_momentum", "transformed_dimension_value"] == 1.0
    assert contributions.loc["inflation_pressure", "transformed_dimension_value"] == -2.0
    assert score["raw_score"] == pytest.approx(-0.2)


def test_missing_dimension_invalidates_below_min_dimensions():
    result = build_regimes_from_dimensions(
        _dimension_scores(valid_inflation=False),
        [_regime()],
        RegimeScoringConfig(softmax_temperature=1.0),
    )
    score = result.regime_scores.iloc[0]

    assert score["valid_dimension_count"] == 1
    assert bool(score["valid"]) is False
    assert score["reason"] == "below_min_valid_dimensions"


def test_remaining_weights_renormalize_when_coverage_sufficient():
    result = build_regimes_from_dimensions(
        _dimension_scores(valid_inflation=False),
        [_regime(min_valid_dimensions=1, min_coverage_ratio=0.50)],
        RegimeScoringConfig(softmax_temperature=1.0),
    )
    score = result.regime_scores.iloc[0]
    contribution = result.contributions[
        result.contributions["dimension_id"] == "growth_momentum"
    ].iloc[0]

    assert bool(score["valid"]) is True
    assert contribution["normalized_weight"] == pytest.approx(1.0)
    assert score["raw_score"] == pytest.approx(1.0)


def test_probabilities_sum_and_dominant_confidence_are_computed():
    regimes = [
        _regime("growth_regime"),
        RegimeDefinition.model_validate(
            {
                "regime_id": "inflation_regime",
                "enabled": True,
                "min_valid_dimensions": 2,
                "min_coverage_ratio": 0.6,
                "dimensions": [
                    {
                        "dimension_id": "growth_momentum",
                        "weight": 0.5,
                        "polarity": "negative",
                    },
                    {
                        "dimension_id": "inflation_pressure",
                        "weight": 0.5,
                        "polarity": "positive",
                    },
                ],
            }
        ),
    ]
    result = build_regimes_from_dimensions(
        _dimension_scores(),
        regimes,
        RegimeScoringConfig(softmax_temperature=1.0),
    )
    valid_scores = result.regime_scores[result.regime_scores["valid"]]

    assert valid_scores["probability"].sum() == pytest.approx(1.0)
    assert result.regime_health.iloc[0]["dominant_regime"] == "inflation_regime"
    assert result.regime_health.iloc[0]["confidence"] > 0


def test_temperature_affects_probability_concentration():
    regimes = [
        _regime("growth_regime"),
        RegimeDefinition.model_validate(
            {
                "regime_id": "inflation_regime",
                "enabled": True,
                "min_valid_dimensions": 2,
                "min_coverage_ratio": 0.6,
                "dimensions": [
                    {
                        "dimension_id": "growth_momentum",
                        "weight": 0.5,
                        "polarity": "negative",
                    },
                    {
                        "dimension_id": "inflation_pressure",
                        "weight": 0.5,
                        "polarity": "positive",
                    },
                ],
            }
        ),
    ]
    low_temp = build_regimes_from_dimensions(
        _dimension_scores(),
        regimes,
        RegimeScoringConfig(softmax_temperature=0.5),
    ).regime_scores
    high_temp = build_regimes_from_dimensions(
        _dimension_scores(),
        regimes,
        RegimeScoringConfig(softmax_temperature=2.0),
    ).regime_scores

    low_top = low_temp["probability"].max()
    high_top = high_temp["probability"].max()
    assert low_top > high_top


def test_regime_rows_are_stored(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    result = build_regimes_from_dimensions(
        _dimension_scores(),
        [_regime()],
        RegimeScoringConfig(softmax_temperature=1.0),
    )

    store.replace_regime_outputs(
        result.contributions,
        result.regime_scores,
        result.regime_health,
    )

    assert len(store.read_table("regime_dimension_contributions")) == 2
    assert len(store.read_table("regime_scores")) == 1
    assert len(store.read_table("regime_health")) == 1


def test_regime_cli_commands_work(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    dimension_scores = _dimension_scores()
    extra_rows = []
    for dimension_id, score in [
        ("credit_liquidity", 0.5),
        ("policy_stance", 0.1),
        ("yield_curve", 0.2),
    ]:
        row = dimension_scores.iloc[0].copy()
        row["dimension_id"] = dimension_id
        row["score"] = score
        extra_rows.append(row)
    all_scores = pd.concat([dimension_scores, pd.DataFrame(extra_rows)], ignore_index=True)
    store.replace_dimension_outputs(pd.DataFrame(), all_scores, pd.DataFrame())
    runner = CliRunner()

    build_result = runner.invoke(
        app,
        [
            "build-regimes",
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
        ["inspect-regime", "goldilocks", "--db-path", str(db_path)],
    )
    health_result = runner.invoke(app, ["regime-health", "--db-path", str(db_path)])
    current_result = runner.invoke(app, ["current-regime", "--db-path", str(db_path)])

    assert build_result.exit_code == 0
    assert inspect_result.exit_code == 0
    assert health_result.exit_code == 0
    assert current_result.exit_code == 0
    assert "goldilocks" in inspect_result.output
    assert "dominant_regime" in current_result.output
