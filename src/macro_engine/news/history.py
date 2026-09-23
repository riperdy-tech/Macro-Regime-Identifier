"""Durable news history: export, hydrate, import (N1.4).

The Actions cache is an accelerator, not the system of record -- it is evicted
after 7 days unused and a single bad save can erase it (the 2026-07-27 mock
wipe). This module makes the classified history durable by writing daily
parquet partitions to `history_dir` (in the cloud, the `run-history` branch),
and re-populating a cold store from them.

Privacy (OA-5): only headline/link/date metadata and classification results
are exported. Article body text and the raw AI response never leave the
machine that produced them -- `_ITEM_EXPORT_COLUMNS` and
`_CLASSIFICATION_EXPORT_COLUMNS` are allow-lists, not exclude-lists.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from macro_engine.news.service import (
    _sector_impacts_from_classifications,
    _theme_scores_from_classifications,
)
from macro_engine.storage.duckdb_store import DuckDBStore

SCHEMA_VERSION = 1

# Items are exported only for these live-fetched providers. synthetic/local_csv/
# local_json/manual_text rows, and anything tagged raw_metadata.backfill, never
# leave the machine (part-2 backfill has its own reserved subtree, §5).
_EXPORTABLE_ITEM_PROVIDERS = {"rss", "gdelt", "finnhub"}

# raw_metadata keys allow-listed into the exported meta_json (everything else,
# including anything body-shaped, is dropped).
_META_JSON_ALLOWED_KEYS = {
    "source_group",
    "feed_url",
    "domain",
    "language",
    "sourcecountry",
    "gdelt_query",
    "finnhub_category",
    "source_group_mapping_method",
    "original_body_chars",
}

_ITEM_EXPORT_COLUMNS = [
    "news_id",
    "content_hash",
    "provider",
    "source",
    "source_group",
    "source_url",
    "title",
    "title_sha256",
    "published_at",
    "first_seen_at",
    "last_ingested_at",
    "body_chars",
    "body_sha256",
    "fulltext_enriched",
    "meta_json",
]

# Every news_classifications column except raw_ai_response_json (OA-5: no raw
# model output leaves the machine either).
_CLASSIFICATION_EXPORT_COLUMNS = [
    "classification_id",
    "news_id",
    "classified_at",
    "ai_provider",
    "ai_model",
    "macro_themes_json",
    "sector_impacts_json",
    "entities_json",
    "secular_theme",
    "time_horizon",
    "severity",
    "confidence",
    "summary",
    "classification_status",
    "error_message",
    "origin",
    "prompt_version",
]

_SOURCE_RUN_EXPORT_COLUMNS = [
    "run_id",
    "run_at",
    "source_id",
    "provider",
    "source_group",
    "status",
    "items_fetched",
    "items_new",
    "newest_published_at",
    "undated_count",
    "error",
    "elapsed_seconds",
]

_KEY_COLUMNS = {
    "items": ["news_id"],
    "classifications": ["classification_id"],
    "source_runs": ["run_id", "source_id"],
}


def export_news_history(
    db_path: str | Path = "data/macro_engine.duckdb",
    history_dir: str | Path = "outputs/news_history",
) -> dict[str, Any]:
    """Append changed day partitions and update the manifest. Never raises --
    every problem is returned in `errors` so a bad snapshot day never takes
    the daily diagnostic down (§2, N1.4)."""
    result: dict[str, Any] = {
        "items_written": 0,
        "classifications_written": 0,
        "source_runs_written": 0,
        "partitions_written": 0,
        "conflicts": 0,
        "errors": [],
        "manifest_totals": {},
    }
    try:
        history_path = Path(history_dir)
        history_path.mkdir(parents=True, exist_ok=True)
        store = DuckDBStore(db_path)
        store.initialize()

        manifest = _read_manifest(history_path)
        partitions: dict[str, dict[str, Any]] = dict(manifest.get("partitions", {}))
        errors: list[str] = []

        items_df = _exportable_items(store.read_news_items())
        classifications_df = _exportable_classifications(
            store.read_table("news_classifications"),
            set(items_df["news_id"].astype(str)) if not items_df.empty else set(),
        )
        try:
            source_runs_raw = store.read_table("news_source_runs")
        except Exception:
            source_runs_raw = pd.DataFrame()
        source_runs_df = _exportable_source_runs(source_runs_raw)

        for kind, frame, store_wins in (
            ("items", items_df, True),
            ("classifications", classifications_df, False),
            ("source_runs", source_runs_df, False),
        ):
            stats = _export_table(
                history_path,
                kind,
                frame,
                _KEY_COLUMNS[kind],
                partitions,
                store_wins=store_wins,
                errors=errors,
            )
            result[f"{kind}_written"] += stats["written"]
            result["partitions_written"] += stats["partitions"]
            result["conflicts"] += stats["conflicts"]

        totals = {
            "items": sum(v["rows"] for k, v in partitions.items() if k.startswith("items/")),
            "classifications": sum(
                v["rows"] for k, v in partitions.items() if k.startswith("classifications/")
            ),
            "source_runs": sum(
                v["rows"] for k, v in partitions.items() if k.startswith("source_runs/")
            ),
        }
        manifest_out = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "exporter_run_id": manifest.get("exporter_run_id") or _exporter_run_id(),
            "partitions": partitions,
            "totals": totals,
        }
        if result["partitions_written"] or not (history_path / "manifest.json").exists():
            manifest_out["exporter_run_id"] = _exporter_run_id()
            _write_manifest(history_path, manifest_out)
        result["manifest_totals"] = totals
        result["errors"] = errors
    except Exception as exc:  # noqa: BLE001 - export must never take the run down
        result["errors"].append(f"history_export_failed:{exc}")
    return result


def hydrate_news_history(
    db_path: str | Path = "data/macro_engine.duckdb",
    history_dir: str | Path = "outputs/news_history",
) -> dict[str, Any]:
    """Insert every snapshot row the store lacks. Never overwrites, verifies
    checksums, and never raises (§2, N1.4)."""
    result: dict[str, Any] = {
        "store_was_cold": False,
        "items_inserted": 0,
        "classifications_inserted": 0,
        "classifications_upgraded": 0,
        "classifications_skipped_protected": 0,
        "source_runs_inserted": 0,
        "errors": [],
        "manifest_totals": {},
    }
    try:
        history_path = Path(history_dir)
        manifest_path = history_path / "manifest.json"
        if not manifest_path.exists():
            return result

        store = DuckDBStore(db_path)
        store.initialize()
        existing_items = store.read_news_items()
        result["store_was_cold"] = bool(existing_items.empty)

        manifest = _read_manifest(history_path)
        result["manifest_totals"] = manifest.get("totals", {})
        partitions = manifest.get("partitions", {})

        for rel_path in sorted(partitions):
            meta = partitions[rel_path]
            abs_path = history_path / rel_path
            if not abs_path.exists():
                result["errors"].append(f"snapshot_partition_missing:{rel_path}")
                continue
            try:
                actual_sha = _sha256_file(abs_path)
            except OSError as exc:
                result["errors"].append(f"snapshot_partition_corrupt:{rel_path}:{exc}")
                continue
            if actual_sha != meta.get("sha256"):
                result["errors"].append(f"snapshot_partition_corrupt:{rel_path}")
                continue
            try:
                frame = pd.read_parquet(abs_path)
            except Exception as exc:  # noqa: BLE001 - one bad file must not abort hydrate
                result["errors"].append(f"snapshot_partition_corrupt:{rel_path}:{exc}")
                continue
            kind = rel_path.split("/")[0]
            if kind == "items":
                _hydrate_items(store, frame, result)
            elif kind == "classifications":
                _hydrate_classifications(store, frame, result)
            elif kind == "source_runs":
                _hydrate_source_runs(store, frame, result)
    except Exception as exc:  # noqa: BLE001 - hydrate must never take the run down
        result["errors"].append(f"history_hydrate_failed:{exc}")
    return result


def import_news_history(
    db_path: str | Path = "data/macro_engine.duckdb",
    history_dir: str | Path = "outputs/news_history",
) -> dict[str, Any]:
    """The operator-facing local pull: identical semantics to `hydrate_news_history`
    (insert-if-absent, precedence-protected), documented under its own name for
    `import-news-history` / `pull_news_history.ps1|.sh`."""
    return hydrate_news_history(db_path=db_path, history_dir=history_dir)


# --------------------------------------------------------------------------
# Item / classification / source-run filtering (export side)
# --------------------------------------------------------------------------


def _exportable_items(items_df: pd.DataFrame) -> pd.DataFrame:
    empty_cols = [*_ITEM_EXPORT_COLUMNS, "_partition_date"]
    if items_df.empty:
        return pd.DataFrame(columns=empty_cols)

    df = items_df.copy()
    df["provider"] = df["provider"].astype(str)
    meta = df.get("raw_metadata_json", pd.Series([None] * len(df))).map(_parse_json_dict)
    is_backfill = meta.map(lambda m: bool(m.get("backfill")))
    keep = df["provider"].isin(_EXPORTABLE_ITEM_PROVIDERS) & ~is_backfill
    df = df[keep].copy()
    meta = meta[keep]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    df["source_group"] = meta.map(lambda m: m.get("source_group") or m.get("query_group"))
    df["title_sha256"] = df["title"].fillna("").astype(str).map(_sha256_text)
    df["last_ingested_at"] = df["ingested_at"]
    body = df.get("body", pd.Series([""] * len(df))).fillna("").astype(str)
    df["body_chars"] = body.map(len).astype("int64")
    df["body_sha256"] = body.map(_sha256_text)
    df["fulltext_enriched"] = meta.map(lambda m: bool(m.get("fulltext_enriched")))
    df["meta_json"] = meta.map(
        lambda m: json.dumps(
            {k: v for k, v in m.items() if k in _META_JSON_ALLOWED_KEYS}, sort_keys=True
        )
    )
    partition_source = df["first_seen_at"].where(df["first_seen_at"].notna(), df["ingested_at"])
    df["_partition_date"] = pd.to_datetime(partition_source, utc=True, errors="coerce").dt.date
    df = df.dropna(subset=["_partition_date"])
    return df[[*_ITEM_EXPORT_COLUMNS, "_partition_date"]].reset_index(drop=True)


def _exportable_classifications(
    classifications_df: pd.DataFrame, exportable_item_ids: set[str]
) -> pd.DataFrame:
    empty_cols = [*_CLASSIFICATION_EXPORT_COLUMNS, "_partition_date"]
    if classifications_df.empty:
        return pd.DataFrame(columns=empty_cols)

    df = classifications_df.copy()
    if "origin" not in df.columns:
        df["origin"] = None
    if "prompt_version" not in df.columns:
        df["prompt_version"] = None
    effective_origin = df["origin"].where(
        df["origin"].notna(), df["ai_provider"].map(lambda p: "mock" if p == "mock" else "live")
    )
    keep = effective_origin.isin(["live", "imported"]) & df["news_id"].astype(str).isin(
        exportable_item_ids
    )
    df = df[keep].copy()
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    for col in _CLASSIFICATION_EXPORT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["_partition_date"] = pd.to_datetime(df["classified_at"], utc=True, errors="coerce").dt.date
    df = df.dropna(subset=["_partition_date"])
    return df[[*_CLASSIFICATION_EXPORT_COLUMNS, "_partition_date"]].reset_index(drop=True)


def _exportable_source_runs(runs_df: pd.DataFrame) -> pd.DataFrame:
    empty_cols = [*_SOURCE_RUN_EXPORT_COLUMNS, "_partition_date"]
    if runs_df.empty:
        return pd.DataFrame(columns=empty_cols)
    df = runs_df.copy()
    for col in _SOURCE_RUN_EXPORT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["_partition_date"] = pd.to_datetime(df["run_at"], utc=True, errors="coerce").dt.date
    df = df.dropna(subset=["_partition_date"])
    return df[[*_SOURCE_RUN_EXPORT_COLUMNS, "_partition_date"]].reset_index(drop=True)


# --------------------------------------------------------------------------
# Partition read/write
# --------------------------------------------------------------------------


def _export_table(
    history_path: Path,
    kind: str,
    frame: pd.DataFrame,
    key_cols: list[str],
    partitions: dict[str, dict[str, Any]],
    *,
    store_wins: bool,
    errors: list[str],
) -> dict[str, int]:
    stats = {"written": 0, "conflicts": 0, "partitions": 0}
    if frame.empty or "_partition_date" not in frame.columns:
        return stats
    export_cols = [c for c in frame.columns if c != "_partition_date"]

    for day, group in frame.groupby("_partition_date"):
        rel_path = _relative_partition_path(kind, day)
        manifest_entry = partitions.get(rel_path)
        manifest_rows = int(manifest_entry["rows"]) if manifest_entry else 0
        if manifest_rows == len(group) and manifest_entry is not None:
            # Idempotence: this day's store count matches what was already
            # exported for it -- nothing new to write.
            continue

        abs_path = history_path / rel_path
        existing_frame = pd.DataFrame(columns=export_cols)
        if abs_path.exists():
            try:
                existing_frame = pd.read_parquet(abs_path)
            except Exception as exc:  # noqa: BLE001 - one bad file must not abort export
                errors.append(f"export_read_failed:{rel_path}:{exc}")
                continue

        new_frame = group[export_cols].reset_index(drop=True)
        merged, conflicts = _merge_partition(
            existing_frame, new_frame, key_cols, incoming_wins=store_wins
        )
        stats["conflicts"] += conflicts
        merged = merged.sort_values(key_cols, kind="stable").reset_index(drop=True)

        if len(merged) < max(manifest_rows, len(existing_frame)):
            errors.append(f"export_refused_shrink:{rel_path}")
            continue

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = abs_path.with_name(abs_path.name + ".tmp")
        merged.to_parquet(tmp_path, engine="pyarrow", compression="zstd", index=False)
        os.replace(tmp_path, abs_path)
        partitions[rel_path] = {"rows": int(len(merged)), "sha256": _sha256_file(abs_path)}
        stats["written"] += len(merged)
        stats["partitions"] += 1

    return stats


def _merge_partition(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    key_cols: list[str],
    *,
    incoming_wins: bool,
) -> tuple[pd.DataFrame, int]:
    """Union two partition frames by key. On a shared key, `incoming_wins`
    decides which row survives; `conflicts` counts shared keys whose rows
    differ (expected to be 0 for immutable tables)."""
    if existing.empty:
        return incoming.copy(), 0
    if incoming.empty:
        return existing.copy(), 0

    existing_key = existing[key_cols].astype(str).agg("|".join, axis=1)
    incoming_key = incoming[key_cols].astype(str).agg("|".join, axis=1)
    shared = set(existing_key) & set(incoming_key)

    conflicts = 0
    if shared:
        common_cols = [c for c in existing.columns if c in incoming.columns]
        existing_shared = (
            existing[existing_key.isin(shared)].sort_values(key_cols).reset_index(drop=True)
        )
        incoming_shared = (
            incoming[incoming_key.isin(shared)].sort_values(key_cols).reset_index(drop=True)
        )
        left = existing_shared[common_cols].fillna("__NA__").astype(str)
        right = incoming_shared[common_cols].fillna("__NA__").astype(str)
        conflicts = int((left.values != right.values).any(axis=1).sum())

    only_existing = existing[~existing_key.isin(incoming_key)]
    only_incoming = incoming[~incoming_key.isin(existing_key)]
    shared_rows = (
        incoming[incoming_key.isin(shared)] if incoming_wins else existing[existing_key.isin(shared)]
    )
    merged = pd.concat([only_existing, only_incoming, shared_rows], ignore_index=True)
    return merged, conflicts


def _relative_partition_path(kind: str, day: Any) -> str:
    ts = pd.Timestamp(day)
    return f"{kind}/{ts:%Y}/{ts:%m}/{kind}_{ts:%Y-%m-%d}.parquet"


# --------------------------------------------------------------------------
# Hydrate helpers
# --------------------------------------------------------------------------


def _hydrate_items(store: DuckDBStore, frame: pd.DataFrame, result: dict[str, Any]) -> None:
    if frame.empty:
        return
    rows = []
    for row in frame.to_dict(orient="records"):
        meta = _parse_json_dict(row.get("meta_json"))
        meta = dict(meta)
        meta["body_unavailable"] = True
        meta["hydrated"] = True
        title = row.get("title")
        body_text = title if title not in (None, "") else "(hydrated headline unavailable)"
        rows.append(
            {
                "news_id": row.get("news_id"),
                "source": row.get("source"),
                "source_url": row.get("source_url"),
                "title": title if title not in (None, "") else body_text,
                "body": body_text,
                "published_at": _naive_utc(row.get("published_at")),
                "ingested_at": _naive_utc(row.get("last_ingested_at")),
                "provider": row.get("provider"),
                "raw_metadata": meta,
                "content_hash": row.get("content_hash"),
                "first_seen_at": _naive_utc(row.get("first_seen_at")),
            }
        )
    hydrate_frame = pd.DataFrame(rows)
    write_res = store.merge_news_items(hydrate_frame)
    result["items_inserted"] += write_res.get("inserted", 0)


def _hydrate_classifications(store: DuckDBStore, frame: pd.DataFrame, result: dict[str, Any]) -> None:
    if frame.empty:
        return
    frame = frame.copy()
    # Parquet round-trips classified_at as a tz-aware UTC pandas Timestamp;
    # DuckDB's arrow-backed insert path applies the session's local timezone
    # when writing a tz-aware value into a naive TIMESTAMP column, silently
    # shifting the wall-clock value. Strip to naive UTC before it reaches SQL.
    frame["classified_at"] = frame["classified_at"].map(_naive_utc)
    for col in ("macro_themes_json", "sector_impacts_json", "entities_json"):
        if col not in frame.columns:
            frame[col] = "[]"
    records = [
        SimpleNamespace(
            news_id=row["news_id"],
            macro_themes=_parse_json_list(row.get("macro_themes_json")),
            sector_impacts=_parse_json_list(row.get("sector_impacts_json")),
        )
        for row in frame.to_dict(orient="records")
    ]
    theme_scores = _theme_scores_from_classifications(records)
    sector_impacts = _sector_impacts_from_classifications(records)
    write_res = store.write_news_classifications(frame, theme_scores, sector_impacts, origin=None)
    result["classifications_inserted"] += write_res.get("inserted", 0)
    result["classifications_upgraded"] += write_res.get("upgraded", 0)
    result["classifications_skipped_protected"] += write_res.get("skipped_protected", 0)


def _hydrate_source_runs(store: DuckDBStore, frame: pd.DataFrame, result: dict[str, Any]) -> None:
    if frame.empty:
        return
    frame = frame.copy()
    for col in ("run_at", "newest_published_at"):
        if col in frame.columns:
            frame[col] = frame[col].map(_naive_utc)
    write_res = store.insert_news_source_runs(frame)
    result["source_runs_inserted"] += write_res.get("inserted", 0)


# --------------------------------------------------------------------------
# Manifest + hashing
# --------------------------------------------------------------------------


def _read_manifest(history_path: Path) -> dict[str, Any]:
    manifest_path = history_path / "manifest.json"
    if not manifest_path.exists():
        return {"schema_version": SCHEMA_VERSION, "partitions": {}, "totals": {}}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "partitions": {}, "totals": {}}


def _write_manifest(history_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path = history_path / "manifest.json"
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, manifest_path)


def _naive_utc(value: Any) -> Any:
    """Strip tz info after normalizing to UTC. DuckDB's arrow-backed insert
    path applies the session's local timezone when a tz-aware pandas value is
    written into a naive TIMESTAMP column, silently shifting the wall-clock
    value -- every timestamp read back from a parquet snapshot must be
    de-tz'd before it is handed to a store write."""
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts.tz_localize(None)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _exporter_run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-export"
