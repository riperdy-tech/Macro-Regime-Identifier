from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from macro_engine.ingest.fred import FredClient, FredError, VintageNotYetPublished
from macro_engine.ingest.health import build_source_health
from macro_engine.ingest.registry import load_ingestion_sources, select_sources
from macro_engine.ingest.schemas import (
    IngestionRunSummary,
    IngestionSource,
    VintageIngestionSummary,
)
from macro_engine.storage.duckdb_store import DuckDBStore

# FRED documents 120 requests/minute per key. The fetcher paces itself below that so a wide
# worker pool buys latency hiding without tripping the limit; 429s are still retried by the
# client when a burst slips through.
_FRED_REQUESTS_PER_MINUTE = 100.0

# Matches the daily runner's own vintage refresh (`VINTAGE_REFRESH_WORKERS` in daily.py) and
# the standalone backfill script's default: enough to hide ALFRED's uneven per-request latency
# (0.3-19 s, measured) without a dedicated flag for what is normally a handful of new dates.
_DEFAULT_VINTAGE_BACKFILL_WORKERS = 8


class _RequestPace:
    """Global minimum interval between request STARTS, shared across worker threads.

    Concurrency hides ALFRED's server-side latency (measured: 0.3 s for one vintage and 19 s
    for another in the same series), but it must not turn into a burst. This keeps the offered
    rate under the documented per-key limit no matter how many workers are running.
    """

    def __init__(self, per_minute: float) -> None:
        self._interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
        if sleep_for > 0:
            time.sleep(sleep_for)


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


def _worker_clients(client: FredClient, count: int) -> list[FredClient]:
    """One client per worker: a `requests.Session` is not safe to share across threads.

    Cloning keeps each worker on its own connection pool. Test doubles need no cloning and are
    handed back unchanged, so the concurrent path stays exercisable without a real network.
    """
    if count <= 1 or not isinstance(client, FredClient):
        return [client] * max(count, 1)
    return [client] + [replace(client, session=None) for _ in range(count - 1)]


def _fetch_vintages(
    series_id: str,
    as_of_dates: list[str],
    client: FredClient,
    observation_start: str | None,
    observation_end: str | None,
    max_workers: int,
    pace: _RequestPace,
) -> list[tuple[str, pd.DataFrame | None, str | None, bool]]:
    """Fetch one series' vintages and return `(as_of, frame, error, not_yet_published)` in DATE
    ORDER.

    Why concurrent: ALFRED's per-request latency is wildly uneven for the SAME series -- a
    2015 vintage returns in 0.3 s while a 2018 vintage of the same series takes 17-19 s
    (measured, HTTP 200, no rate-limit headers). A serial loop therefore runs at the speed of
    its worst request: ~4 requests/minute, which turned a ~2 hour backfill into a day-long one.
    Hiding that latency behind a small worker pool is the whole fix.

    Writes stay serial: the caller stores each series on its own thread once every pair for
    that series has landed, so concurrency changes how fast bytes arrive, never what is stored.

    `not_yet_published` is true when ALFRED rejected `as_of` as later than its own current date
    (`VintageNotYetPublished`) -- the evaluation calendar always asks about today, so this recurs
    every day until ALFRED catches up. It is kept apart from `error` so the caller never counts
    it as a fetch failure.
    """
    if not as_of_dates:
        return []

    def fetch(
        as_of: str, worker_client: FredClient
    ) -> tuple[str, pd.DataFrame | None, str | None, bool]:
        pace.wait()
        try:
            frame = worker_client.get_series_observations_vintage(
                series_id,
                as_of=as_of,
                observation_start=observation_start,
                observation_end=observation_end,
            )
        except VintageNotYetPublished:
            return as_of, None, None, True
        except FredError as exc:
            return as_of, None, str(exc), False
        return as_of, frame, None, False

    workers = min(max(max_workers, 1), len(as_of_dates))
    if workers == 1:
        return [fetch(as_of, client) for as_of in as_of_dates]

    pool_clients = _worker_clients(client, workers)
    results: dict[str, tuple[str, pd.DataFrame | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(fetch, as_of, pool_clients[index % workers])
            for index, as_of in enumerate(as_of_dates)
        ]
        for future in futures:
            outcome = future.result()
            results[outcome[0]] = outcome
    return [results[as_of] for as_of in as_of_dates]


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
    max_workers: int = 1,
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

    CONCURRENT FETCH, SERIAL WRITE. `max_workers > 1` fetches a series' pending vintages in
    parallel (paced below FRED's per-key request limit) and still stores them on the calling
    thread, so an operator can kill the run at any point without leaving a partial series behind.

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
    pace = _RequestPace(_FRED_REQUESTS_PER_MINUTE)
    unique_dates = sorted({str(value) for value in as_of_dates})
    already_stored = _stored_vintage_pairs(store) if resume else set()
    absent_known = _absent_vintage_pairs(store) if resume else set()
    answered = already_stored | absent_known
    errors: list[dict[str, str]] = []
    empty_vintages: list[dict[str, str]] = []
    not_yet_published: list[dict[str, str]] = []
    vintage_rows = 0
    skipped_pairs = 0
    skipped_absent_pairs = 0
    stored_series: list[str] = []

    for source in sources:
        frames: list[pd.DataFrame] = []
        fetched = 0
        started = time.monotonic()
        pending = [as_of for as_of in unique_dates if (source.series_id, as_of) not in answered]
        skipped_pairs += len(unique_dates) - len(pending)
        skipped_absent_pairs += sum(
            1 for as_of in unique_dates if (source.series_id, as_of) in absent_known
        )
        for as_of, observations, error, skip_not_yet_published in _fetch_vintages(
            source.series_id, pending, fred, observation_start, observation_end, max_workers, pace
        ):
            if skip_not_yet_published:
                # Not a failure and not an ordinary empty vintage: ALFRED just has not
                # published this as-of date yet (today, or later). It is not memoised as an
                # absence either -- unlike a genuinely nonexistent vintage, this one becomes
                # available later, often later the same day.
                not_yet_published.append({"series_id": source.series_id, "as_of": as_of})
                continue
            if error is not None:
                errors.append({"series_id": source.series_id, "as_of": as_of, "error": error})
                continue
            if observations is None or observations.empty:
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
        emptied = [
            item for item in empty_vintages if item["series_id"] == source.series_id
        ]
        if emptied:
            # Remember the negatives so the next run does not re-ask thousands of dead dates.
            store.upsert_vintage_absence(_absence_frame(emptied))
        if progress:
            elapsed = time.monotonic() - started
            rate = f"{len(pending) / elapsed * 60:.0f}/min" if pending and elapsed > 0 else "n/a"
            print(
                f"vintages: {source.series_id} fetched={fetched} "
                f"skipped_or_empty={len(unique_dates) - fetched} "
                f"rows_stored_total={vintage_rows} "
                f"elapsed={elapsed:.0f}s rate={rate}",
                flush=True,
            )

    if parquet_dir and (vintage_rows or skipped_pairs):
        # Keep the Parquet mirror current even when this run only skipped over stored work.
        _export_vintage_parquet(store, parquet_dir)

    for as_of in sorted({item["as_of"] for item in not_yet_published}):
        # Recorded, not warned: this is expected to recur every day until ALFRED publishes,
        # so it must not inflate warning_count or fail the run (see VintageNotYetPublished).
        print(f"vintages: vintage_skipped:not_yet_published:{as_of}", flush=True)

    return VintageIngestionSummary(
        run_id=run_id,
        series_requested=len(sources),
        as_of_dates=unique_dates,
        vintage_rows=vintage_rows,
        vintage_series=len(stored_series),
        skipped_pairs=skipped_pairs,
        empty_vintage_count=len(empty_vintages),
        skipped_absent_pairs=skipped_absent_pairs,
        failed_count=len(errors),
        storage_path=str(parquet_dir),
        series_stored=stored_series,
        failed_series=sorted({error["series_id"] for error in errors}),
        not_yet_published_count=len(not_yet_published),
        not_yet_published_dates=sorted({item["as_of"] for item in not_yet_published}),
    )


def run_vintage_backfill(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/alfred",
    start: str | None = None,
    end: str | None = None,
    api_key: str | None = None,
    client: FredClient | None = None,
    max_workers: int = _DEFAULT_VINTAGE_BACKFILL_WORKERS,
) -> VintageIngestionSummary:
    """Refresh ALFRED vintages for every enabled series, over the as-of dates the stored
    evaluation calendar actually asks about (`vintage_asof_dates`).

    This is the same "which series, which as-of dates, fetched how" `scripts/backfill_vintages.py
    --apply` runs by hand; `run-pipeline`'s vintages step calls it too, so there is one definition
    of the default backfill rather than two that could drift apart.
    """
    # Imported locally, not at module scope, only to keep ingest/ from acquiring a
    # module-load-time dependency on anchors/ for what is otherwise a one-line lookup.
    from macro_engine.anchors.pit_calendar import vintage_asof_dates

    as_of_dates = vintage_asof_dates(db_path=db_path, start=start, end=end)
    series = [
        source.series_id
        for source in select_sources(load_ingestion_sources(config_path))
    ]
    observation_start = start or (
        (pd.Timestamp(as_of_dates[0]) - pd.DateOffset(years=1)).date().isoformat()
        if as_of_dates
        else None
    )
    return run_fred_vintage_ingestion(
        as_of_dates=as_of_dates,
        config_path=config_path,
        requested_series=series,
        observation_start=observation_start,
        observation_end=end,
        db_path=db_path,
        parquet_dir=parquet_dir,
        api_key=api_key,
        client=client,
        resume=True,
        progress=False,
        max_workers=max_workers,
    )


def _stored_vintage_pairs(store: DuckDBStore) -> set[tuple[str, str]]:
    """(series_id, as-of date) pairs that already hold DATA.

    A pair counts as stored when the series has any vintage row stamped with that as-of date --
    which is exactly the unit of work, so a partially-completed series resumes mid-way instead of
    restarting. Read as a DISTINCT projection, not as the table: the archive is ~8.6M rows and
    growing, while the answer is a few thousand keys.
    """
    frame = store.read_vintage_keys()
    if frame is None or frame.empty:
        return set()
    return {
        (str(row.series_id), pd.Timestamp(row.realtime_start).date().isoformat())
        for row in frame.itertuples(index=False)
    }


def _absent_vintage_pairs(store: DuckDBStore) -> set[tuple[str, str]]:
    """(series_id, as-of date) pairs a previous run established ALFRED has no vintage for.

    Skippable for the same reason as stored pairs, and kept in a separate set so the summary can
    tell the operator which kind of "nothing to do" they are looking at.
    """
    frame = store.read_vintage_absence()
    if frame is None or frame.empty:
        return set()
    return {
        (str(row.series_id), pd.Timestamp(row.realtime_start).date().isoformat())
        for row in frame[["series_id", "realtime_start"]].itertuples(index=False)
    }


def _absence_frame(emptied: list[dict[str, str]]) -> pd.DataFrame:
    """`vintage_absence` rows for the (series, as-of) pairs that came back empty."""
    return pd.DataFrame(
        {
            "series_id": [item["series_id"] for item in emptied],
            "realtime_start": pd.to_datetime([item["as_of"] for item in emptied]),
            "reason": ["no_vintage_in_alfred"] * len(emptied),
            "checked_at": [pd.Timestamp.now(tz="UTC")] * len(emptied),
        }
    )


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
