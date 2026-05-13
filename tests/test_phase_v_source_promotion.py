from __future__ import annotations

from macro_engine.dimensions.config import load_dimension_config
from macro_engine.features.config import load_feature_config
from macro_engine.ingest.registry import load_ingestion_sources


def test_phase_v_selected_sources_are_promoted_to_production():
    sources = {source.series_id for source in load_ingestion_sources("config/phase_b_sources.yaml")}
    features = {feature.feature_id for feature in load_feature_config("config/phase_b_sources.yaml").features}

    assert {"ICSA", "BAMLH0A0HYM2"}.issubset(sources)
    assert {"RSAFS", "T5YIE"}.isdisjoint(sources)
    assert {"initial_claims_level_z", "high_yield_oas_level_z"}.issubset(features)
    assert {"retail_sales_yoy_z", "five_year_breakeven_level_z"}.isdisjoint(features)


def test_phase_v_production_dimension_weights_match_approved_source_promotion():
    dimensions = {
        dimension.dimension_id: dimension
        for dimension in load_dimension_config("config/phase_b_sources.yaml").dimensions
    }

    growth = {feature.feature_id: feature for feature in dimensions["growth_momentum"].features}
    credit = {
        feature.feature_id: feature for feature in dimensions["credit_liquidity"].features
    }

    assert growth["industrial_production_yoy_z"].weight == 0.30
    assert growth["payrolls_yoy_z"].weight == 0.30
    assert growth["unemployment_6m_change_z"].weight == 0.25
    assert growth["initial_claims_level_z"].weight == 0.15
    assert growth["initial_claims_level_z"].polarity == "negative"

    assert credit["baa_spread_level_z"].weight == 0.35
    assert credit["nfci_level_z"].weight == 0.35
    assert credit["high_yield_oas_level_z"].weight == 0.30
    assert credit["high_yield_oas_level_z"].polarity == "negative"
