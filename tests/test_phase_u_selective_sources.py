from __future__ import annotations

import pandas as pd

from macro_engine.dimensions.config import load_dimension_config
from macro_engine.features.config import load_feature_config
from macro_engine.ingest.registry import load_ingestion_sources
from macro_engine.ingest.schemas import IngestionRunSummary
from macro_engine.pipeline_runner import run_pipeline
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_c_features import _raw_monthly


PHASE_U_CONFIG = "config/experiments/phase_u_sources.yaml"


def test_phase_u_config_includes_only_selective_source_candidates():
    sources = {source.series_id for source in load_ingestion_sources(PHASE_U_CONFIG)}
    production_sources = {
        source.series_id for source in load_ingestion_sources("config/phase_b_sources.yaml")
    }
    features = {feature.feature_id for feature in load_feature_config(PHASE_U_CONFIG).features}

    assert {"ICSA", "BAMLH0A0HYM2"}.issubset(sources)
    assert {"RSAFS", "T5YIE"}.isdisjoint(sources)
    assert {"ICSA", "BAMLH0A0HYM2"}.isdisjoint(production_sources)
    assert {"initial_claims_level_z", "high_yield_oas_level_z"}.issubset(features)
    assert {"retail_sales_yoy_z", "five_year_breakeven_level_z"}.isdisjoint(features)


def test_phase_u_dimensions_map_candidates_with_expected_weights_and_polarity():
    dimensions = {
        dimension.dimension_id: dimension
        for dimension in load_dimension_config(PHASE_U_CONFIG).dimensions
    }

    growth = {
        feature.feature_id: (feature.weight, feature.polarity)
        for feature in dimensions["growth_momentum"].features
    }
    credit = {
        feature.feature_id: (feature.weight, feature.polarity)
        for feature in dimensions["credit_liquidity"].features
    }

    assert growth["initial_claims_level_z"] == (0.15, "negative")
    assert credit["high_yield_oas_level_z"] == (0.30, "negative")


def test_phase_u_pipeline_works_against_temp_mock_data(tmp_path):
    db_path = tmp_path / "phase_u.duckdb"
    output_dir = tmp_path / "outputs" / "phase_u" / "reports"
    config_path = tmp_path / "phase_u_sources.yaml"
    source_config = open(PHASE_U_CONFIG, encoding="utf-8").read()
    source_config = source_config.replace(
        "output_dir: outputs/experiments/phase_u/reports",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(source_config, encoding="utf-8")

    summary = run_pipeline(
        config_path=config_path,
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_phase_u_mock_ingest,
    )

    assert summary.status in {"success", "success_with_warnings"}
    assert summary.series_requested == 12
    assert summary.series_succeeded == 12
    assert summary.latest_valid_regime_date is not None
    assert (output_dir / "current_regime.json").exists()
    feature_health = DuckDBStore(db_path).read_table("feature_health")
    assert {"initial_claims_level_z", "high_yield_oas_level_z"}.issubset(
        set(feature_health["feature_id"])
    )


def _phase_u_mock_ingest(config_path, start, end, db_path, parquet_dir):
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
        run_id="phase-u-mock-run",
        series_requested=len(enabled_series),
        series_succeeded=len(enabled_series),
        series_failed=0,
        stale_series=[],
        storage_path=str(parquet_dir),
    )
