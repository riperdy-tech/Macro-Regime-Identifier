"""N1.6: news health -- gates that bite.

Pure: every input is a value the caller already has (this run's rows from
`news_source_runs`, the per-source `stale_after_hours` map, the active
profile's group membership, the classification/hydrate/export results). No
I/O here; `daily.py` reads the store and calls `compute_news_health`.

Status mapping (spec §3):
- `failed`  -- F1/F4 fired. Classification is skipped (`skipped_news_failed`)
  if it has not started. Macro/sector/anchors still run (non-negotiable 3).
- `degraded` -- any D-code fired and no F-code fired.
- `ok` -- otherwise; N-codes are notes only, never change the status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

# Bad per-run source statuses for the consecutive-bad-runs count (D1). `ok`,
# `rate_limited` and `circuit_open` are not "bad" on their own -- a rate-limited
# or breaker-skipped GDELT source is covered by N1/D2, not D1.
_BAD_SOURCE_STATUSES = {"error", "empty", "missing_key"}
_STALE_ELIGIBLE_PROVIDERS = {"rss", "finnhub"}
_CONSECUTIVE_BAD_RUNS_THRESHOLD = 3
_GDELT_DEAD_STREAK_RUNS = 7
DEFAULT_MAX_SINGLE_GROUP_PCT = 0.50
DEFAULT_CLASSIFICATION_ABORT_RATE = 0.20
DEFAULT_CLASSIFICATION_DEGRADE_RATE = 0.10
_MIN_ATTEMPTS_BEFORE_RATE_GATES = 10


def compute_news_health(
    *,
    run_id: str,
    run_at: datetime,
    source_runs_history: pd.DataFrame,
    stale_after_hours: dict[str, int],
    profile_groups: dict[str, list[str]],
    classification_result: dict[str, Any] | None,
    hydrate_result: dict[str, Any] | None,
    export_result: dict[str, Any] | None,
    store_was_cold: bool,
    max_single_group_pct: float = DEFAULT_MAX_SINGLE_GROUP_PCT,
) -> dict[str, Any]:
    history = source_runs_history.copy() if source_runs_history is not None else pd.DataFrame()
    if not history.empty:
        history["run_at"] = pd.to_datetime(history["run_at"], errors="coerce", utc=True)
        history["source_id"] = history["source_id"].astype(str)
        history["status"] = history["status"].astype(str)
    this_run = history[history["run_id"] == run_id] if not history.empty else pd.DataFrame()

    reasons: list[str] = []
    notes: list[str] = []

    sources_out, dead_by_source, source_provider = _per_source_rollup(
        history, this_run, run_at, stale_after_hours, reasons
    )

    groups_out: list[dict[str, Any]] = []
    for group, source_ids in sorted(profile_groups.items()):
        group_sources = [sid for sid in source_ids if sid in dead_by_source]
        uncovered = bool(group_sources) and all(dead_by_source[sid] for sid in group_sources)
        groups_out.append({"group": group, "sources": sorted(source_ids), "uncovered": uncovered})
        if uncovered:
            reasons.append(f"group_uncovered:{group}")

    # F1 news_blackout: zero new items across every source this run.
    total_new_this_run = (
        int(pd.to_numeric(this_run["items_new"], errors="coerce").fillna(0).sum())
        if not this_run.empty
        else 0
    )
    failed_gates: list[str] = []
    if not this_run.empty and total_new_this_run == 0:
        failed_gates.append("news_blackout")

    # F4 cold_hydrate_failed.
    hydrate_result = hydrate_result or {}
    manifest_totals = hydrate_result.get("manifest_totals") or {}
    manifest_expects_rows = sum(int(v or 0) for v in manifest_totals.values())
    hydrate_loaded_rows = int(hydrate_result.get("items_inserted", 0)) + int(
        hydrate_result.get("classifications_inserted", 0)
    )
    cold_hydrate_failed = bool(
        store_was_cold
        and manifest_expects_rows > 0
        and (bool(hydrate_result.get("errors")) or hydrate_loaded_rows == 0)
    )
    if cold_hydrate_failed:
        failed_gates.append("cold_hydrate_failed")

    # D1 already appended per source above (reasons list mutated in place).
    # D2 gdelt_dead.
    gdelt_streak = _gdelt_dead_streak(history)
    if gdelt_streak >= _GDELT_DEAD_STREAK_RUNS:
        reasons.append("gdelt_dead")

    # D4 history export/hydrate errors.
    export_result = export_result or {}
    for code in hydrate_result.get("errors", []) or []:
        reasons.append(str(code))
    for code in export_result.get("errors", []) or []:
        reasons.append(str(code))

    # D5 / F2 classification failure rate (F2 is the EXISTING abort inside
    # classify_stored_news, kept; D5 is the new, softer degrade threshold).
    classification_result = classification_result or {}
    attempted = int(classification_result.get("completed_count", 0) or 0)
    failed_count = int(classification_result.get("failed_count", 0) or 0)
    failure_rate = (failed_count / attempted) if attempted else None
    if attempted >= _MIN_ATTEMPTS_BEFORE_RATE_GATES and failure_rate is not None:
        if failure_rate > DEFAULT_CLASSIFICATION_DEGRADE_RATE:
            reasons.append("classification_failure_rate_high")

    # N2 classification_backlog. OA-3 (2026-09-23) approved the longer
    # deadline this note was waiting on, so it is promoted to DEGRADE here
    # rather than left as a note (the spec's own condition for the promotion).
    selected_count = int(classification_result.get("selected_count", 0) or 0)
    completed_count = int(classification_result.get("completed_count", 0) or 0)
    deadline_hit = bool(classification_result.get("deadline_hit", False))
    if deadline_hit or (selected_count > 0 and completed_count < selected_count):
        reasons.append("classification_backlog")

    # N1 gdelt_rate_limited:k/6 (note only).
    gdelt_this_run = this_run[this_run["provider"] == "gdelt"] if not this_run.empty else this_run
    n_gdelt = len(gdelt_this_run)
    n_gdelt_limited = int(
        gdelt_this_run["status"].isin(["rate_limited", "circuit_open"]).sum()
    ) if n_gdelt else 0
    if n_gdelt_limited:
        notes.append(f"gdelt_rate_limited:{n_gdelt_limited}/{n_gdelt}")

    # N3 group_concentration (note only).
    if total_new_this_run > 0 and not this_run.empty:
        by_group = this_run.groupby(this_run["source_group"].fillna("unmapped"))[
            "items_new"
        ].apply(lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum())
        top_group = by_group.idxmax() if not by_group.empty else None
        if top_group is not None and (by_group.max() / total_new_this_run) > max_single_group_pct:
            notes.append(f"group_concentration:{top_group}")

    # N4 undated_items:<id> (note only).
    if not this_run.empty:
        undated = this_run[pd.to_numeric(this_run["undated_count"], errors="coerce").fillna(0) > 0]
        for row in undated.to_dict(orient="records"):
            notes.append(f"undated_items:{row['source_id']}")

    reasons = list(dict.fromkeys(reasons))  # stable de-dupe
    notes = list(dict.fromkeys(notes))

    if failed_gates:
        status = "failed"
        reasons = list(dict.fromkeys(failed_gates + reasons))
    elif reasons:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "reasons": reasons,
        "notes": notes,
        "sources": sources_out,
        "groups": groups_out,
        "classification": {
            "selected": selected_count,
            "completed": completed_count,
            "failed": failed_count,
            "attempted": attempted,
            "failure_rate": failure_rate,
            "deadline_hit": deadline_hit,
        },
        "history": {
            "gdelt_dead_streak_runs": gdelt_streak,
            "distinct_runs_considered": (
                int(history["run_id"].nunique()) if not history.empty else 0
            ),
        },
    }


def _per_source_rollup(
    history: pd.DataFrame,
    this_run: pd.DataFrame,
    run_at: datetime,
    stale_after_hours: dict[str, int],
    reasons: list[str],
) -> tuple[list[dict[str, Any]], dict[str, bool], dict[str, str]]:
    sources_out: list[dict[str, Any]] = []
    dead_by_source: dict[str, bool] = {}
    provider_by_source: dict[str, str] = {}
    if history.empty:
        return sources_out, dead_by_source, provider_by_source

    run_at_ts = pd.Timestamp(run_at)
    if run_at_ts.tzinfo is None:
        run_at_ts = run_at_ts.tz_localize("UTC")
    else:
        run_at_ts = run_at_ts.tz_convert("UTC")

    for source_id in sorted(history["source_id"].unique()):
        src_hist = history[history["source_id"] == source_id].sort_values("run_at")
        this_row = src_hist[src_hist["run_id"] == this_run["run_id"].iloc[0]] if (
            not this_run.empty and "run_id" in this_run.columns
        ) else src_hist.iloc[0:0]
        latest_row = this_row.iloc[-1] if not this_row.empty else src_hist.iloc[-1]
        provider = str(latest_row.get("provider") or "")
        provider_by_source[source_id] = provider
        threshold = int(stale_after_hours.get(source_id, 72))

        consecutive_bad = _consecutive_bad_runs(src_hist)
        new_rows = src_hist[pd.to_numeric(src_hist["items_new"], errors="coerce").fillna(0) > 0]
        last_new_at = new_rows["run_at"].max() if not new_rows.empty else None
        if last_new_at is not None and pd.notna(last_new_at):
            hours_since_new = (run_at_ts - last_new_at).total_seconds() / 3600.0
            exceeds_stale = hours_since_new > threshold
        else:
            hours_since_new = None
            exceeds_stale = True

        is_stale_eligible = provider in _STALE_ELIGIBLE_PROVIDERS
        dead = is_stale_eligible and (
            consecutive_bad >= _CONSECUTIVE_BAD_RUNS_THRESHOLD or exceeds_stale
        )
        dead_by_source[source_id] = dead
        if dead:
            reasons.append(f"source_dead:{source_id}")

        items_new_val = (
            int(pd.to_numeric(this_row.iloc[-1]["items_new"], errors="coerce") or 0)
            if not this_row.empty
            else 0
        )
        sources_out.append(
            {
                "source_id": source_id,
                "status": str(latest_row.get("status") or "unknown"),
                "items_new": items_new_val,
                "last_new_at": last_new_at.isoformat() if last_new_at is not None and pd.notna(last_new_at) else None,
                "consecutive_bad_runs": consecutive_bad,
                "stale_after_hours": threshold,
                "dead": bool(dead),
            }
        )
    return sources_out, dead_by_source, provider_by_source


def _consecutive_bad_runs(src_hist_sorted_asc: pd.DataFrame) -> int:
    count = 0
    for status in reversed(src_hist_sorted_asc["status"].tolist()):
        if status in _BAD_SOURCE_STATUSES:
            count += 1
        else:
            break
    return count


def _gdelt_dead_streak(history: pd.DataFrame) -> int:
    if history.empty:
        return 0
    gdelt = history[history["provider"] == "gdelt"]
    if gdelt.empty:
        return 0
    per_run = (
        gdelt.groupby("run_id")
        .agg(run_at=("run_at", "max"), any_ok=("status", lambda s: bool((s == "ok").any())))
        .sort_values("run_at")
    )
    streak = 0
    for any_ok in reversed(per_run["any_ok"].tolist()):
        if not any_ok:
            streak += 1
        else:
            break
    return streak
