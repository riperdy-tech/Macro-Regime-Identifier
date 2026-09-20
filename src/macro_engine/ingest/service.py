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
from macro_engine.ingest.schemas import (
    IngestionRunSummary,
    IngestionSource,
    VintageIngestionSummary,
)
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


def run_fred_vintage_ingestion(
    *,
    as_of_dates: list[str],
    config_path: str | Path = "config/phase_b_sources.yaml",
    requested_series: list[str] | None = None,
    observation_start: str | None = None,
    observation_end: str | None = None,
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/alfred",
    api_key: str | None = None,
    client: FredClient | None = None,
    resume: bool = True,
    progress: bool = False,
) -> VintageIngestionSummary:
    """Backfill ALFRED vintages for exactly the as-of dates the diagnostics use.

    Point-in-time integrity is bought by multiplying request volume by the number of vintages, so
    this never pulls a series' full vintage history: it pulls only the vintages the evaluation
    calendar actually asks about. Writes go to the separate `raw_observation_vintages` table (see
    DuckDBStore), leaving the daily revisioned `raw_observations` path completely untouched.

    FLUSHED PER SERIES, AND RESUMABLE. The first version accumulated every series in memory and
    wrote once at the end, so a full-history backfill -- ~6,500 requests -- held its only copy in
    memory for hours, and FRED rate-limits long runs, which makes being killed a real possibility
    rather than a hypothetical. Each series now lands in the database as soon as it is fetched,
    and `resume=True` skips (series, as-of) pairs already stored, so a restart costs only the work
    that had not completed.

    Idempotent either way: repeated writes replace the same
    (series_id, date, realtime_start, realtime_end) rows.
    """
    load_dotenv()
    run_id = datetime.now(timezone.utc).isoformat()
    all_sources = load_ingestion_sources(config_path)
    sources = [source for source in select_sources(all_sources, requested_series) if source.enabled]
    store = DuckDBStore(db_path)
    store.initialize()

    fred = client or FredClient(api_key or os.getenv("FRED_API_KEY", ""))
    unique_dates = sorted({str(value) for value in as_of_dates})
    already_stored = _stored_vintage_pairs(store) if resume else set()
    errors: list[dict[str, str]] = []
    empty_vintages: list[dict[str, str]] = []
    vintage_rows = 0
    skipped_pairs = 0
    stored_series: list[str] = []

    for source in sources:
        frames: list[pd.DataFrame] = []
        fetched = 0
        for as_of in unique_dates:
            if (source.series_id, as_of) in already_stored:
                skipped_pairs += 1
                continue
            try:
                observations = fred.get_series_observations_vintage(
                    source.series_id,
                    as_of=as_of,
                    observation_start=observation_start,
                    observation_end=observation_end,
                )
            except FredError as exc:
                errors.append({"series_id": source.series_id, "as_of": as_of, "error": str(exc)})
                continue
            if observations.empty:
                empty_vintages.append({"series_id": source.series_id, "as_of": as_of})
                continue
            frames.append(_vintage_frame(observations, source))
            fetched += 1
        if frames:
            # Flush THIS series now: a multi-hour run must not hold its only copy in memory.
            chunk = pd.concat(frames, ignore_index=True)
            store.upsert_raw_observation_vintages(chunk)
            vintage_rows += int(len(chunk))
            stored_series.append(source.series_id)
        if progress:
            print(
                f"vintages: {source.series_id} fetched={fetched} "
                f"skipped_or_empty={len(unique_dates) - fetched} "
                f"rows_stored_total={vintage_rows}",
                flush=True,
            )

    if parquet_dir and (vintage_rows or skipped_pairs):
        # Keep the Parquet mirror current even when this run only skipped over stored work.
        _export_vintage_parquet(store, parquet_dir)

    return VintageIngestionSummary(
        run_id=run_id,
        series_requested=len(sources),
        as_of_dates=unique_dates,
        vintage_rows=vintage_rows,
        vintage_series=len(stored_series),
        skipped_pairs=skipped_pairs,
        empty_vintage_count=len(empty_vintages),
        failed_count=len(errors),
        storage_path=str(parquet_dir),
        series_stored=stored_series,
    )


def _stored_vintage_pairs(store: DuckDBStore) -> set[tuple[str, str]]:
    """(series_id, as-of date) pairs already present, for resume.

    A pair counts as stored when the series has ANY vintage row stamped with that as-of date --
    which is exactly the unit of work, so a partially-completed series resumes mid-way instead of
    restarting.
    """
    existing = store.read_raw_observation_vintages()
    if existing.empty:
        return set()
    frame = existing[["series_id", "realtime_start"]].dropna().drop_duplicates()
    return {
        (str(row.series_id), pd.Timestamp(row.realtime_start).date().isoformat())
        for row in frame.itertuples(index=False)
    }


def _vintage_frame(observations: pd.DataFrame, source: IngestionSource) -> pd.DataFrame:
    frame = observations.copy()
    frame["source"] = "ALFRED"
    frame["fetched_at"] = pd.Timestamp.now(tz="UTC")
    frame["frequency"] = source.frequency
    # `units` is descriptive only and is not part of the vintage fetch; the series
    # metadata table carries the authoritative value.
    frame["units"] = None
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


def _empty_vintage_frame() -> pd.DataFrame:
    return pd.DataFrame(
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


def _export_vintage_parquet(store: DuckDBStore, parquet_dir: str | Path) -> None:
    output_path = Path(parquet_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    store.read_raw_observation_vintages().to_parquet(
        output_path / "raw_observation_vintages.parquet", index=False
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
