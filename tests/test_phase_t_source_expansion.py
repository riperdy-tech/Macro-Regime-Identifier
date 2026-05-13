from __future__ import annotations

import pandas as pd

from macro_engine.dimensions.config import load_dimension_config
from macro_engine.features.config import load_feature_config
from macro_engine.ingest.registry import load_ingestion_sources
from macro_engine.ingest.schemas import IngestionRunSummary
from macro_engine.pipeline_runner import run_pipeline
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_c_features import _raw_monthly


PHASE_T_CONFIG = "config/experiments/phase_t_sources.yaml"


def test_phase_t_source_expansion_config_validates_and_keeps_production_separate():
    production_sources = {source.series_id for source in load_ingestion_sources("config/phase_b_sources.yaml")}
    expanded_sources = {source.series_id for source in load_ingestion_sources(PHASE_T_CONFIG)}
    expanded_features = {feature.feature_id for feature in load_feature_config(PHASE_T_CONFIG).features}

    assert {"ICSA", "RSAFS", "T5YIE", "BAMLH0A0HYM2"}.issubset(expanded_sources)
    assert {"ICSA", "RSAFS", "T5YIE", "BAMLH0A0HYM2"}.isdisjoint(production_sources)
    assert {
        "initial_claims_level_z",
        "retail_sales_yoy_z",
        "five_year_breakeven_level_z",
        "high_yield_oas_level_z",
    }.issubset(expanded_features)


def test_phase_t_dimensions_include_expanded_features_with_expected_polarity():
    dimensions = {
        dimension.dimension_id: dimension
        for dimension in load_dimension_config(PHASE_T_CONFIG).dimensions
    }

    growth = {feature.feature_id: feature.polarity for feature in dimensions["growth_momentum"].features}
    inflation = {
        feature.feature_id: feature.polarity
        for feature in dimensions["inflation_pressure"].features
    }
    credit = {
        feature.feature_id: feature.polarity
        for feature in dimensions["credit_liquidity"].features
    }

    assert growth["initial_claims_level_z"] == "negative"
    assert growth["retail_sales_yoy_z"] == "positive"
    assert inflation["five_year_breakeven_level_z"] == "positive"
    assert credit["high_yield_oas_level_z"] == "negative"


def test_phase_t_pipeline_works_against_temp_mock_data(tmp_path):
    db_path = tmp_path / "phase_t.duckdb"
    output_dir = tmp_path / "outputs" / "phase_t" / "reports"
    config_path = tmp_path / "phase_t_sources.yaml"
    source_config = open(PHASE_T_CONFIG, encoding="utf-8").read()
    source_config = source_config.replace(
        "output_dir: outputs/experiments/phase_t/reports",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(source_config, encoding="utf-8")

    summary = run_pipeline(
        config_path=config_path,
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_phase_t_mock_ingest,
    )

    assert summary.status in {"success", "success_with_warnings"}
    assert summary.series_requested == 14
    assert summary.series_succeeded == 14
    assert summary.latest_valid_regime_date is not None
    assert (output_dir / "current_regime.json").exists()
    feature_health = DuckDBStore(db_path).read_table("feature_health")
    assert "initial_claims_level_z" in set(feature_health["feature_id"])


def _phase_t_mock_ingest(config_path, start, end, db_path, parquet_dir):
    del config_path, start, end
    enabled_series = [
        "INDPRO",
        "PAYEMS",
        "UNRATE",
        "CPIAUCSL",
        "PCEPI",
        "FEDFUNDS",
        "DGS10",
        "BAA10Y",
        "NFCI",
        "T10Y2Y",
        "ICSA",
        "RSAFS",
        "T5YIE",
        "BAMLH0A0HYM2",
    ]
    store = DuckDBStore(db_path)
    store.initialize()
    raw = pd.concat(
        [_raw_monthly(series_id, 160) for series_id in enabled_series],
        ignore_index=True,
    )
    store.upsert_raw_observations(raw)
    store.export_parquet(parquet_dir)
    return IngestionRunSummary(
        run_id="phase-t-mock-run",
        series_requested=len(enabled_series),
        series_succeeded=len(enabled_series),
        series_failed=0,
        stale_series=[],
        storage_path=str(parquet_dir),
    )
