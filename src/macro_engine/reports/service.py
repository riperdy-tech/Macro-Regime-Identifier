from __future__ import annotations

from pathlib import Path

from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.evaluation.nber import load_nber_benchmark_config
from macro_engine.reports.config import load_report_config
from macro_engine.reports.writer import (
    build_current_regime_report,
    build_historical_diagnostic_report,
    current_report_markdown,
    diagnostic_report_markdown,
    write_report_outputs,
)
from macro_engine.storage.duckdb_store import DuckDBStore

DEFAULT_NBER_CONFIG_PATH = "config/nber_recessions.yaml"


def write_current_regime_report(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    nber_config_path: str | Path = DEFAULT_NBER_CONFIG_PATH,
) -> tuple[Path, Path]:
    config = load_report_config(config_path)
    scoring_mode = load_evaluation_config(config_path).scoring_mode
    # C3 (P0_0 §1.3.1): `threshold_configured` is the one number a consumer may treat as a
    # probability threshold -- reuse the NBER benchmark's own detection_threshold (0.25)
    # rather than inventing a second constant for the same concept.
    recession_threshold = 0.25
    if Path(nber_config_path).exists():
        recession_threshold = load_nber_benchmark_config(nber_config_path).detection_threshold
    store = DuckDBStore(db_path)
    payload = build_current_regime_report(
        regime_scores=store.read_table("regime_scores"),
        regime_health=store.read_table("regime_health"),
        timeline=store.read_table("historical_regime_timeline"),
        regime_contributions=store.read_table("regime_dimension_contributions"),
        dimension_scores=store.read_table("dimension_scores"),
        dimension_contributions=store.read_table("dimension_feature_contributions"),
        feature_health=store.read_table("feature_health"),
        source_health=store.read_table("source_health"),
        config=config,
        scoring_mode=scoring_mode,
        recession_threshold=recession_threshold,
    )
    markdown = current_report_markdown(payload)
    return write_report_outputs(
        output_dir=config.output_dir,
        json_name="current_regime.json",
        markdown_name="current_regime.md",
        payload=payload,
        markdown=markdown,
    )


def write_historical_diagnostic_report(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_report_config(config_path)
    store = DuckDBStore(db_path)
    payload = build_historical_diagnostic_report(
        timeline=store.read_table("historical_regime_timeline"),
        transitions=store.read_table("regime_transitions"),
        summary=store.read_table("diagnostic_summary"),
        config=config,
    )
    markdown = diagnostic_report_markdown(payload)
    return write_report_outputs(
        output_dir=config.output_dir,
        json_name="historical_diagnostic.json",
        markdown_name="historical_diagnostic.md",
        payload=payload,
        markdown=markdown,
    )
