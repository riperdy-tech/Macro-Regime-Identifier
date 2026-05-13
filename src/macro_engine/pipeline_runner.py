from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
from dotenv import load_dotenv

from macro_engine.diagnostics.service import run_stored_historical_diagnostic
from macro_engine.dimensions.service import build_stored_dimensions
from macro_engine.evaluation.service import build_stored_asof_features
from macro_engine.features.service import build_stored_features
from macro_engine.ingest.fred import FredError
from macro_engine.ingest.service import run_fred_ingestion
from macro_engine.regimes.service import build_stored_regimes
from macro_engine.reports.config import load_report_config
from macro_engine.reports.service import (
    write_current_regime_report,
    write_historical_diagnostic_report,
)
from macro_engine.storage.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class PipelineSummary:
    run_id: str
    status: str
    failed_step: str | None
    warning_count: int
    config_path: str
    mode: str
    output_dir: str
    series_requested: int | None = None
    series_succeeded: int | None = None
    stale_series: list[str] | None = None
    latest_valid_regime_date: str | None = None
    dominant_regime: str | None = None
    confidence: float | None = None
    outputs: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "failed_step": self.failed_step,
            "warning_count": self.warning_count,
            "config_path": self.config_path,
            "mode": self.mode,
            "output_dir": self.output_dir,
            "series_requested": self.series_requested,
            "series_succeeded": self.series_succeeded,
            "stale_series": self.stale_series or [],
            "latest_valid_regime_date": self.latest_valid_regime_date,
            "dominant_regime": self.dominant_regime,
            "confidence": self.confidence,
            "outputs": self.outputs or [],
        }


def run_pipeline(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
    mode: str = "live",
    start: str | None = None,
    end: str | None = None,
    ingest_runner: Callable | None = None,
    load_env: bool = True,
) -> PipelineSummary:
    if load_env:
        load_dotenv()
    config_path = str(config_path)
    db_path = str(db_path)
    parquet_dir = str(parquet_dir)
    run_id = datetime.now(timezone.utc).isoformat()
    started_at = pd.Timestamp.now(tz="UTC")
    store = DuckDBStore(db_path)
    store.initialize()
    report_config = load_report_config(config_path)
    output_dir = report_config.output_dir
    warnings: list[str] = []
    outputs: list[str] = []
    ingestion_summary = None
    failed_step: str | None = None
    status = "success"

    try:
        _require_live_key_if_needed(mode)
        failed_step = "ingest"
        runner = ingest_runner or run_fred_ingestion
        ingestion_summary = runner(
            config_path=config_path,
            start=start,
            end=end,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )
        warnings.extend([f"stale_source:{series}" for series in ingestion_summary.stale_series])

        failed_step = "build-features"
        feature_result = build_stored_features(
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )
        _collect_invalid_feature_warnings(feature_result.feature_health, warnings)

        failed_step = "build-asof-features"
        build_stored_asof_features(
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )

        failed_step = "build-dimensions"
        dimension_result = build_stored_dimensions(
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )
        _collect_invalid_dimension_warnings(dimension_result.dimension_scores, warnings)

        failed_step = "build-regimes"
        regime_result = build_stored_regimes(
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )
        _collect_invalid_regime_warnings(regime_result.regime_scores, warnings)

        failed_step = "run-historical-diagnostic"
        run_stored_historical_diagnostic(
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )

        failed_step = "write-current-report"
        current_json, current_md = write_current_regime_report(
            config_path=config_path,
            db_path=db_path,
        )
        outputs.extend([str(current_json), str(current_md)])

        failed_step = "write-diagnostic-report"
        diagnostic_json, diagnostic_md = write_historical_diagnostic_report(
            config_path=config_path,
            db_path=db_path,
        )
        outputs.extend([str(diagnostic_json), str(diagnostic_md)])

        latest = _latest_current_regime(store)
        failed_step = None
        if warnings:
            status = "success_with_warnings"
        summary = PipelineSummary(
            run_id=run_id,
            status=status,
            failed_step=None,
            warning_count=len(warnings),
            config_path=config_path,
            mode=mode,
            output_dir=output_dir,
            series_requested=ingestion_summary.series_requested if ingestion_summary else None,
            series_succeeded=ingestion_summary.series_succeeded if ingestion_summary else None,
            stale_series=ingestion_summary.stale_series if ingestion_summary else [],
            latest_valid_regime_date=latest.get("date"),
            dominant_regime=latest.get("dominant_regime"),
            confidence=latest.get("confidence"),
            outputs=outputs,
        )
    except Exception:
        status = "failed"
        summary = PipelineSummary(
            run_id=run_id,
            status=status,
            failed_step=failed_step,
            warning_count=len(warnings),
            config_path=config_path,
            mode=mode,
            output_dir=output_dir,
            outputs=outputs,
        )
        _record_pipeline_summary(store, summary, started_at)
        raise

    _record_pipeline_summary(store, summary, started_at)
    return summary


def _require_live_key_if_needed(mode: str) -> None:
    if mode == "live" and not os.getenv("FRED_API_KEY"):
        raise FredError("FRED_API_KEY is required for live pipeline ingestion")


def _collect_invalid_feature_warnings(feature_health: pd.DataFrame, warnings: list[str]) -> None:
    if feature_health.empty:
        return
    for row in feature_health[~feature_health["usable"]].to_dict(orient="records"):
        warnings.append(f"invalid_feature:{row['feature_id']}:{row['reason']}")


def _collect_invalid_dimension_warnings(dimension_scores: pd.DataFrame, warnings: list[str]) -> None:
    if dimension_scores.empty:
        return
    invalid = dimension_scores[~dimension_scores["valid"]]
    for row in invalid.tail(25).to_dict(orient="records"):
        warnings.append(f"invalid_dimension:{row['dimension_id']}:{row['reason']}")


def _collect_invalid_regime_warnings(regime_scores: pd.DataFrame, warnings: list[str]) -> None:
    if regime_scores.empty:
        return
    invalid = regime_scores[~regime_scores["valid"]]
    for row in invalid.tail(25).to_dict(orient="records"):
        warnings.append(f"invalid_regime:{row['regime_id']}:{row['reason']}")


def _latest_current_regime(store: DuckDBStore) -> dict:
    timeline = store.read_table("historical_regime_timeline")
    valid_timeline = timeline[timeline["valid"]].sort_values("date")
    if not valid_timeline.empty:
        latest = valid_timeline.iloc[-1]
        return {
            "date": str(latest["date"]),
            "dominant_regime": latest.get("reported_regime") or latest["dominant_regime"],
            "confidence": None
            if pd.isna(latest.get("reported_confidence"))
            else float(latest.get("reported_confidence")),
        }
    health = store.read_table("regime_health")
    valid_health = health[health["valid"]].sort_values("date")
    if valid_health.empty:
        return {}
    latest = valid_health.iloc[-1]
    return {
        "date": str(latest["date"]),
        "dominant_regime": latest["dominant_regime"],
        "confidence": None if pd.isna(latest["confidence"]) else float(latest["confidence"]),
    }


def _record_pipeline_summary(
    store: DuckDBStore,
    summary: PipelineSummary,
    started_at: pd.Timestamp,
) -> None:
    store.record_pipeline_run(
        {
            "run_id": summary.run_id,
            "started_at": started_at,
            "completed_at": pd.Timestamp.now(tz="UTC"),
            "config_path": summary.config_path,
            "mode": summary.mode,
            "status": summary.status,
            "failed_step": summary.failed_step,
            "warning_count": summary.warning_count,
            "output_dir": summary.output_dir,
        }
    )
