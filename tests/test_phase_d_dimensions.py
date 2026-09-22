from __future__ import annotations

import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.dimensions.composition import (
    CompositionFeature,
    CompositionRegistry,
    CompositionSegment,
    DimensionComposition,
    load_composition_registry,
    validate_registry_against_dimensions,
)
from macro_engine.dimensions.config import DimensionDefinition, load_dimension_config
from macro_engine.dimensions.scoring import DuplicateFeatureRows, build_dimensions_from_features
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


def test_duplicate_feature_rows_fail_loudly_instead_of_picking_one():
    """A stored (feature_id, date) key with more than one row must stop scoring, not
    silently take whichever row `sort_values("date")` happened to put last."""
    duplicated = pd.concat(
        [_features(), _features().iloc[[0]].assign(raw_value=999.0, normalized_value=999.0)],
        ignore_index=True,
    )

    with pytest.raises(DuplicateFeatureRows) as excinfo:
        build_dimensions_from_features(duplicated, [_dimension()])

    assert excinfo.value.feature_id == "feature_a"
    assert excinfo.value.n == 2
    assert "feature_a" in str(excinfo.value)
    assert "2" in str(excinfo.value)


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


# ── S1.4 (P0_0 §2.6): the declarative composition registry ─────────────────────


def _registry(*, declare_feature_b: bool) -> CompositionRegistry:
    features = [CompositionFeature(feature_id="feature_a", weight=0.6)]
    if declare_feature_b:
        features.append(CompositionFeature(feature_id="feature_b", weight=0.4))
    return CompositionRegistry(
        dimensions={
            "growth_momentum": DimensionComposition(
                dimension_id="growth_momentum",
                segments=[
                    CompositionSegment(
                        composition_id="growth_momentum:v1:2026-01-01..",
                        valid_from="2026-01-01",
                        valid_to=None,
                        features=features,
                    )
                ],
            )
        }
    )


def test_composition_registry_is_a_noop_when_the_valid_set_matches_the_declared_set():
    result = build_dimensions_from_features(
        _features(valid_b=True), [_dimension()], _registry(declare_feature_b=True)
    )
    score = result.dimension_scores.iloc[0]

    assert bool(score["valid"]) is True
    assert score["reason"] == "ok"
    assert score["score"] == pytest.approx(-0.2)


def test_undeclared_valid_feature_invalidates_the_whole_dimension_for_that_date():
    """A feature that validates OUTSIDE its declared composition window is the general
    shape of the HY OAS defect (S0.2): a coverage-gated dimension silently re-specifies
    itself. The registry must catch it and say so, not renormalize around it."""
    result = build_dimensions_from_features(
        _features(valid_b=True), [_dimension()], _registry(declare_feature_b=False)
    )
    score = result.dimension_scores.iloc[0]
    contributions = result.contributions.set_index("feature_id")

    assert bool(score["valid"]) is False
    assert score["reason"] == "undeclared_composition:extra:feature_b"
    assert score["score"] is None
    # feature_a itself was never a problem -- its own row stays valid; only the DATE's
    # dimension score is invalidated, per §2.6 ("invalidates the dimension for that date").
    assert bool(contributions.loc["feature_a", "valid"]) is True
    assert bool(contributions.loc["feature_b", "valid"]) is False
    assert contributions.loc["feature_b", "reason"] == "undeclared_composition:extra:feature_b"


def test_composition_registry_is_a_noop_for_dimensions_it_does_not_register():
    """Registration is additive: a dimension absent from the registry entirely scores
    exactly as it did before S1.4 (composition is None-equivalent for it)."""
    registry = CompositionRegistry(dimensions={})
    without_registry = build_dimensions_from_features(_features(valid_b=True), [_dimension()])
    with_empty_registry = build_dimensions_from_features(
        _features(valid_b=True), [_dimension()], registry
    )

    pd.testing.assert_frame_equal(
        without_registry.dimension_scores, with_empty_registry.dimension_scores
    )


def test_validate_registry_rejects_an_unknown_dimension_id():
    registry = _registry(declare_feature_b=True)
    registry.dimensions["not_a_real_dimension"] = registry.dimensions.pop("growth_momentum")

    with pytest.raises(ValueError, match="unknown dimension_id"):
        validate_registry_against_dimensions(registry, [_dimension()])


def test_validate_registry_rejects_a_feature_not_configured_on_the_dimension():
    registry = CompositionRegistry(
        dimensions={
            "growth_momentum": DimensionComposition(
                dimension_id="growth_momentum",
                segments=[
                    CompositionSegment(
                        composition_id="growth_momentum:v1:2026-01-01..",
                        valid_from="2026-01-01",
                        features=[CompositionFeature(feature_id="feature_not_configured", weight=1.0)],
                    )
                ],
            )
        }
    )

    with pytest.raises(ValueError, match="not configured"):
        validate_registry_against_dimensions(registry, [_dimension()])


def test_validate_registry_rejects_a_weight_that_drifted_from_phase_b_sources():
    registry = CompositionRegistry(
        dimensions={
            "growth_momentum": DimensionComposition(
                dimension_id="growth_momentum",
                segments=[
                    CompositionSegment(
                        composition_id="growth_momentum:v1:2026-01-01..",
                        valid_from="2026-01-01",
                        # _dimension() configures feature_a at weight 0.6, not 0.99.
                        features=[CompositionFeature(feature_id="feature_a", weight=0.99)],
                    )
                ],
            )
        }
    )

    with pytest.raises(ValueError, match="declares weight"):
        validate_registry_against_dimensions(registry, [_dimension()])


def test_composition_segments_may_not_overlap():
    with pytest.raises(ValueError, match="overlap or are unordered"):
        DimensionComposition(
            dimension_id="growth_momentum",
            segments=[
                CompositionSegment(
                    composition_id="growth_momentum:v1:1990-01-01..2023-12-31",
                    valid_from="1990-01-01",
                    valid_to="2023-12-31",
                    features=[CompositionFeature(feature_id="feature_a", weight=1.0)],
                ),
                CompositionSegment(
                    composition_id="growth_momentum:v2:2023-01-01..",
                    valid_from="2023-01-01",
                    valid_to=None,
                    features=[CompositionFeature(feature_id="feature_a", weight=1.0)],
                ),
            ],
        )


def test_restricted_to_drops_entries_for_dimensions_the_caller_never_configured():
    """A caller loading a NARROWER phase_b_sources.yaml variant (a test fixture, e.g.
    test_phase_t_source_expansion.py / test_phase_u_selective_sources.py, both of which hit
    this for real) legitimately configures only some dimensions. The full production
    dimension_composition.yaml still names all seven; those extra entries are not a drift
    to catch and must not turn into `unknown dimension_id` on every partial config."""
    registry = load_composition_registry("config/dimension_composition.yaml")
    narrowed = registry.restricted_to({"growth_momentum"})

    assert set(narrowed.dimensions) == {"growth_momentum"}
    # Does not raise: the dropped dimensions (policy_stance, credit_liquidity, ...) are
    # simply absent from `narrowed`, not present-but-invalid.
    reduced_config = [
        DimensionDefinition.model_validate(
            {
                "dimension_id": "growth_momentum",
                "min_valid_features": 1,
                "min_coverage_ratio": 0.5,
                # Must match the shipped registry's declared growth_momentum features/weights
                # exactly, or this raises for a different reason (a genuine drift) -- not
                # what this test is checking (that policy_stance etc. are silently dropped).
                "features": [
                    {"feature_id": "industrial_production_yoy_z", "weight": 0.30, "polarity": "positive"},
                    {"feature_id": "payrolls_yoy_z", "weight": 0.30, "polarity": "positive"},
                    {"feature_id": "unemployment_6m_change_z", "weight": 0.25, "polarity": "negative"},
                    {"feature_id": "initial_claims_level_z", "weight": 0.15, "polarity": "negative"},
                ],
            }
        )
    ]
    validate_registry_against_dimensions(narrowed, reduced_config)


def test_build_stored_dimensions_does_not_auto_load_composition_for_a_non_production_config(tmp_path):
    """Regression: `config/experiments/phase_t_sources.yaml` reuses the real dimension_id
    `growth_momentum` with its own feature/weight set (an experiment, not production). It
    must not be checked against the real `config/dimension_composition.yaml` -- that is a
    test double, not a production drift. (This is exactly what broke
    test_phase_t_source_expansion.py the first time this shipped: `growth_momentum`'s
    `industrial_production_yoy_z` is weighted differently there than in production.)"""
    from macro_engine.dimensions.service import build_stored_dimensions
    from macro_engine.storage.duckdb_store import DuckDBStore

    features = _features().copy()
    features["feature_id"] = features["feature_id"].replace(
        {"feature_a": "industrial_production_yoy_z", "feature_b": "payrolls_yoy_z"}
    )
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_features(features)

    # Must not raise: config/experiments/phase_t_sources.yaml's own growth_momentum
    # weights differ from the real registry's, which would be a false "drift" if the
    # registry were auto-loaded against this non-production config.
    build_stored_dimensions(
        config_path="config/experiments/phase_t_sources.yaml",
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
    )


def test_shipped_composition_registry_loads_and_validates_against_the_production_config():
    registry = load_composition_registry("config/dimension_composition.yaml")
    production = load_dimension_config("config/phase_b_sources.yaml")

    validate_registry_against_dimensions(registry, production.dimensions)

    assert registry.composition_id("credit_liquidity", pd.Timestamp("2020-01-01").date()) == (
        "credit_liquidity:v1:1990-01-01..2023-09-30"
    )
    assert registry.composition_id("credit_liquidity", pd.Timestamp("2024-01-01").date()) == (
        "credit_liquidity:v2:2023-10-01.."
    )
    assert registry.declared_feature_ids(
        "credit_liquidity", pd.Timestamp("2020-01-01").date()
    ) == {"baa_spread_level_z", "nfci_level_z"}
    assert registry.declared_feature_ids(
        "credit_liquidity", pd.Timestamp("2024-01-01").date()
    ) == {"baa_spread_level_z", "nfci_level_z", "high_yield_oas_level_z"}
