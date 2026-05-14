from __future__ import annotations

from pathlib import Path

from macro_engine.sectors.config import load_sector_config
from macro_engine.sectors.scoring import SectorBuildResult, build_sector_scores
from macro_engine.storage.duckdb_store import DuckDBStore


def build_stored_sector_scores(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    sector_config_path: str | Path = "config/sectors.yaml",
    exposure_config_path: str | Path = "config/sector_exposures.yaml",
    prior_config_path: str | Path = "config/sector_regime_priors.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> SectorBuildResult:
    config = load_sector_config(
        macro_config_path=config_path,
        sector_config_path=sector_config_path,
        exposure_config_path=exposure_config_path,
        prior_config_path=prior_config_path,
    )
    store = DuckDBStore(db_path)
    store.initialize()
    result = build_sector_scores(
        regime_scores=store.read_table("regime_scores"),
        regime_health=store.read_table("regime_health"),
        dimension_scores=store.read_table("dimension_scores"),
        timeline=store.read_table("historical_regime_timeline"),
        config=config,
    )
    store.replace_sector_outputs(
        result.sector_scores,
        result.components,
        result.sector_health,
    )
    store.export_parquet(parquet_dir)
    return result
