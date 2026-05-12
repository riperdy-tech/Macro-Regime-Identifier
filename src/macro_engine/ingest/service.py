from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from macro_engine.ingest.fred import FredClient, FredError
from macro_engine.ingest.health import build_source_health
from macro_engine.ingest.registry import load_ingestion_sources, select_sources
from macro_engine.ingest.schemas import IngestionRunSummary, IngestionSource
from macro_engine.storage.duckdb_store import DuckDBStore


def run_fred_ingestion(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    requested_series: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
    api_key: str | None = None,
    client: FredClient | None = None,
) -> IngestionRunSummary:
    load_dotenv()
    run_id = datetime.now(timezone.utc).isoformat()
    started_at = pd.Timestamp.now(tz="UTC")
    all_sources = load_ingestion_sources(config_path)
    sources = select_sources(all_sources, requested_series)
    health_sources = sources if requested_series else all_sources
    store = DuckDBStore(db_path)
    store.initialize()

    fred = client or FredClient(api_key or os.getenv("FRED_API_KEY", ""))
    metadata_records: list[dict[str, Any]] = []
    observation_frames: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []

    for source in sources:
        if not source.enabled:
            continue
        try:
            metadata = fred.get_series_metadata(source.series_id)
            metadata_records.append(_metadata_record(metadata))
            observations = fred.get_series_observations(
                source.series_id,
                observation_start=start,
                observation_end=end,
            )
            observations = _observation_frame(observations, metadata, source)
            observation_frames.append(observations)
        except FredError as exc:
            errors.append({"series_id": source.series_id, "error": str(exc)})

    raw_observations = (
        pd.concat(observation_frames, ignore_index=True)
        if observation_frames
        else pd.DataFrame(
            columns=[
                "series_id",
                "date",
                "value",
                "realtime_start",
                "realtime_end",
                "source",
                "fetched_at",
                "frequency",
                "units",
            ]
        )
    )
    store.upsert_series_metadata(metadata_records)
    store.upsert_raw_observations(raw_observations)
    stored_raw = store.read_raw_observations()
    health = build_source_health(health_sources, stored_raw, as_of=end)
    store.upsert_source_health(health)

    completed_at = pd.Timestamp.now(tz="UTC")
    succeeded_ids = {frame["series_id"].iloc[0] for frame in observation_frames if not frame.empty}
    stale_series = health[health["stale_flag"] & health["usable"]]["series_id"].tolist()
    store.record_ingestion_run(
        {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "status": "failed" if errors and not succeeded_ids else "completed_with_errors" if errors else "completed",
            "series_requested": len([source for source in sources if source.enabled]),
            "series_succeeded": len(succeeded_ids),
            "series_failed": len(errors),
            "errors": errors,
        }
    )
    store.export_parquet(parquet_dir)
    return IngestionRunSummary(
        run_id=run_id,
        series_requested=len([source for source in sources if source.enabled]),
        series_succeeded=len(succeeded_ids),
        series_failed=len(errors),
        stale_series=stale_series,
        storage_path=str(parquet_dir),
    )


def _metadata_record(metadata: dict[str, Any]) -> dict[str, Any]:
    notes = metadata.get("notes") or ""
    return {
        "series_id": metadata["series_id"],
        "title": metadata.get("title"),
        "frequency": metadata.get("frequency"),
        "units": metadata.get("units"),
        "seasonal_adjustment": metadata.get("seasonal_adjustment"),
        "last_updated": pd.to_datetime(metadata.get("last_updated"), errors="coerce"),
        "notes_hash": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
        "notes": notes,
        "fetched_at": pd.Timestamp.now(tz="UTC"),
    }


def _observation_frame(
    observations: pd.DataFrame,
    metadata: dict[str, Any],
    source: IngestionSource,
) -> pd.DataFrame:
    frame = observations.copy()
    frame["source"] = source.provider
    frame["fetched_at"] = pd.Timestamp.now(tz="UTC")
    frame["frequency"] = source.frequency
    frame["units"] = metadata.get("units")
    return frame[
        [
            "series_id",
            "date",
            "value",
            "realtime_start",
            "realtime_end",
            "source",
            "fetched_at",
            "frequency",
            "units",
        ]
    ]
