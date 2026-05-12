from macro_engine.config.loader import load_all_configs


def test_config_loads_and_validates():
    config = load_all_configs("config")

    assert config.model.version == "0.1.0"
    assert "annual" in config.model.freshness_defaults
    assert "fiscal_impulse" in config.dimensions
    assert config.dimensions["fiscal_impulse"].dimension_type == "context"
    assert config.dimensions["fiscal_impulse"].required_for_regime is False


def test_sources_have_feature_ids_and_split_transform_normalization():
    config = load_all_configs("config")

    feature_ids = [source.feature_id for source in config.sources]
    assert len(feature_ids) == len(set(feature_ids))
    assert all(source.transform for source in config.sources)
    assert all(source.normalization for source in config.sources)


def test_usslind_disabled_and_context_sources_zero_weight():
    config = load_all_configs("config")
    sources = {source.feature_id: source for source in config.sources}

    assert sources["us_leading_index_disabled"].enabled is False
    assert sources["us_leading_index_disabled"].reason_disabled == "discontinued_or_stale"
    assert sources["wti_oil_3m_return_context"].weight == 0
    assert sources["gold_3m_return_context"].weight == 0
    assert sources["federal_deficit_context"].frequency == "annual"


def test_regimes_only_score_core_dimensions():
    config = load_all_configs("config")

    for regime in config.regimes.values():
        for dimension_name in regime.scoring:
            assert config.dimensions[dimension_name].required_for_regime
