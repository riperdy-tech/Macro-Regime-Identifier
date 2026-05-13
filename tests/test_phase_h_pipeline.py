from __future__ import annotations

import os

import pandas as pd
import pytest

from macro_engine.ingest.fred import FredError
from macro_engine.ingest.schemas import IngestionRunSummary
from macro_engine.pipeline_runner import run_pipeline
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_c_features import _raw_monthly


def _mock_ingest(config_path, start, end, db_path, parquet_dir):
    store = DuckDBStore(db_path)
    store.initialize()
    raw = pd.concat(
        [
            _raw_monthly("INDPRO", 140),
            _raw_monthly("PAYEMS", 140),
            _raw_monthly("UNRATE", 140),
            _raw_monthly("CPIAUCSL", 140),
            _raw_monthly("PCEPI", 140),
            _raw_monthly("FEDFUNDS", 140),
            _raw_monthly("DGS10", 140),
            _raw_monthly("BAA10Y", 140),
            _raw_monthly("NFCI", 140),
            _raw_monthly("T10Y2Y", 140),
        ],
        ignore_index=True,
    )
    store.upsert_raw_observations(raw)
    store.export_parquet(parquet_dir)
    return IngestionRunSummary(
        run_id="mock-run",
        series_requested=10,
        series_succeeded=10,
        series_failed=0,
        stale_series=[],
        storage_path=str(parquet_dir),
    )


def _failing_ingest(config_path, start, end, db_path, parquet_dir):
    raise RuntimeError("mock hard failure")


def test_run_pipeline_works_against_temp_mock_data(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "outputs"
    config_path = tmp_path / "pipeline_config.yaml"
    source_config = open("config/phase_b_sources.yaml", encoding="utf-8").read()
    source_config = source_config.replace("output_dir: outputs", f"output_dir: {output_dir.as_posix()}")
    config_path.write_text(source_config, encoding="utf-8")

    summary = run_pipeline(
        config_path=config_path,
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
    )

    assert summary.status in {"success", "success_with_warnings"}
    assert summary.series_requested == 10
    assert summary.series_succeeded == 10
    assert summary.latest_valid_regime_date is not None
    assert summary.dominant_regime is not None
    assert len(summary.outputs or []) == 4
    assert (output_dir / "current_regime.json").exists()
    assert (output_dir / "historical_diagnostic.md").exists()
    pipeline_runs = DuckDBStore(db_path).read_table("pipeline_runs")
    assert pipeline_runs.iloc[-1]["status"] == summary.status


def test_run_pipeline_records_failed_step_on_hard_failure(tmp_path):
    db_path = tmp_path / "macro.duckdb"

    with pytest.raises(RuntimeError, match="mock hard failure"):
        run_pipeline(
            config_path="config/phase_b_sources.yaml",
            db_path=db_path,
            parquet_dir=tmp_path / "fred",
            mode="mock",
            ingest_runner=_failing_ingest,
        )

    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "failed"
    assert run["failed_step"] == "ingest"


def test_live_pipeline_requires_fred_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(FredError, match="FRED_API_KEY is required"):
        run_pipeline(
            config_path="config/phase_b_sources.yaml",
            db_path=tmp_path / "macro.duckdb",
            parquet_dir=tmp_path / "fred",
            mode="live",
            ingest_runner=_mock_ingest,
            load_env=False,
        )


def test_live_pipeline_can_be_invoked_when_key_is_present_with_mock_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    summary = run_pipeline(
        config_path="config/phase_b_sources.yaml",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        mode="live",
        ingest_runner=_mock_ingest,
    )

    assert summary.series_succeeded == 10


def test_pipeline_summary_is_deterministic_shape(tmp_path):
    summary = run_pipeline(
        config_path="config/phase_b_sources.yaml",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
    ).to_dict()

    assert set(summary) == {
        "run_id",
        "status",
        "failed_step",
        "warning_count",
        "config_path",
        "mode",
        "output_dir",
        "series_requested",
        "series_succeeded",
        "stale_series",
        "latest_valid_regime_date",
        "dominant_regime",
        "confidence",
        "outputs",
    }
    assert summary["mode"] == "mock"


@pytest.mark.skipif(not os.getenv("FRED_API_KEY"), reason="FRED_API_KEY not set")
def test_optional_live_pipeline_smoke(tmp_path):
    summary = run_pipeline(
        config_path="config/phase_b_sources.yaml",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        mode="live",
    )

    assert summary.series_succeeded is not None
