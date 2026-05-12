from macro_engine.config.loader import load_all_configs
from macro_engine.pipeline import classify_observations
from macro_engine.toy_data import build_toy_observations


def test_toy_pipeline_produces_required_payload_fields():
    config = load_all_configs("config")
    result = classify_observations(build_toy_observations(), config, "2026-05-08")
    payload = result["payload"]

    assert payload["as_of"] == "2026-05-08"
    assert payload["historical_mode"] == "historical_revised_data_diagnostic"
    assert payload["primary_regime"] in payload["regime_probabilities"]
    assert 0 <= payload["confidence"] <= 1
    assert abs(sum(payload["regime_probabilities"].values()) - 1) < 0.000001
    assert "source_health" in payload


def test_source_health_reports_disabled_and_used_sources():
    config = load_all_configs("config")
    result = classify_observations(build_toy_observations(), config, "2026-05-08")
    health = result["source_health"]
    items = {item.feature_id: item for item in health.items}

    assert items["us_leading_index_disabled"].status == "disabled"
    assert items["us_leading_index_disabled"].used_in_score is False
    assert items["headline_cpi_yoy_z"].used_in_score is True
    assert health.disabled_series == 1


def test_dimension_scores_are_bounded():
    config = load_all_configs("config")
    result = classify_observations(build_toy_observations(), config, "2026-05-08")
    dimension_scores = result["dimension_scores"]

    assert ((dimension_scores["score"] >= -1) & (dimension_scores["score"] <= 1)).all()
    assert ((dimension_scores["confidence"] >= 0) & (dimension_scores["confidence"] <= 1)).all()


def test_missing_required_source_lowers_dimension_confidence():
    config = load_all_configs("config")
    raw = build_toy_observations()
    raw = raw[raw["series_id"] != "INDPRO"]

    result = classify_observations(raw, config, "2026-05-08")
    growth = result["dimension_scores"].set_index("dimension").loc["growth_momentum"]

    assert growth["confidence"] == 0
    assert "industrial_production_yoy_z" in result["source_health"].required_series_missing
