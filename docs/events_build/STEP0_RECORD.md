# Step 0 record — ownership and baseline (MRI_EVENTS_PLAN v2 §9 step 0)

Date: 2026-10-18. Branch `feat/mri-events`.

## Who owns local news hydration

**Unowned.** What is known:

- `scripts/pull_news_history.ps1` / `.sh` (N1.4, NEWS_PART1_SPEC §N1.4) were written as an operator-run
  pull; the plan states `import-news-history` "has never been run locally".
- No scheduled task runs it. Scheduled tasks present on 2026-10-18: `MRI-Local-Daily` (runs
  `scripts/run_local_daily.ps1`, whose header says "No news: news is the cloud job's"), plus RS2-* tasks and
  `GT-News Digest`; none calls hydration.
- rev23 AK.11 (`stocks-workspace/docs/review_2026-09-22/rev23/SPEC_rebuild.md`) records D2 (the MRI fixes) as
  "handled by another session" and lists local outputs, the anchor, theme discovery and the screener export.
  It does not mention news hydration; it says "the other session owns the MRI rows in STATUS". No rev23 or
  rs2-local document mentions `pull_news_history` or `import-news-history`.
- Consumers of local news: the shock narrative channel (`shocks/narrative.py`) and the planned events job.
  The cloud job owns ingestion/classification and the `run-history` snapshot (writer); nothing owns the
  cloud -> local pull.

Proposed owner (for the operator to ratify, D3): the MRI events job, as its first step (E0.1 makes the pull
daily-runnable, idempotent, and non-zero on failure).

## Baseline hashes (firewall, plan §9.1)

`scripts/events_firewall_hash.py --write docs/events_build/firewall_baseline.json`; the baseline is
committed beside this file. Canonicalisation drops run metadata (`built_at`, `source_run_id`,
`validated_run_id`, `fetched_at`, any `*_at` / `*_run_id` key). `shock_register.json` is hashed over numeric
leaves only. `raw_observations` rows for DGS10, DFII10, T10YIE are hashed over
(date, value, realtime_start, realtime_end) with row count and newest date.

Check: `python scripts/events_firewall_hash.py --check docs/events_build/firewall_baseline.json`.
The baseline is a same-day instrument: a daily FRED ingest legitimately moves the raw_observations hashes.
Compare immediately before and after the events job.

## E0.1 hydration record (2026-10-18, real data)

Backup first: `data/backups/macro_engine_before_E0.1_20261018.duckdb` (untracked; `data/backups/` is not committed).
Source: `origin/run-history` at `111f7db` (fetched; the first attempt used `cb69d7a`).

| Table | Before | After | Inserted |
|---|---|---|---|
| news_items | 17,216 | 27,594 | 10,378 |
| news_classifications | 567 | 3,170 | 2,603 (manifest total 2,603) |
| news_source_runs | 0 | 783 | 783 |

- Newest `first_seen_at` after: 2026-10-18T00:26:31Z (age 0.33 h at check). Before: null for every row
  (legacy rows carry no `first_seen_at`).
- Items by `first_seen_at`: 10-14 117, 10-15 120, 10-16 120 (published 10-14 127, 10-15 121, 10-16 108).
  Classified by publish date: 10-14 56, 10-15 53, 10-16 55. Title scan since 10-12: CoreWeave 7,
  Houthi 12, "rate hike" 1.
- Shock narratives (computed in memory with `attach_narrative` on the new store against the on-disk register,
  nothing written): 6 of 7 shocks non-empty (volatility 366 items, credit 60, rates 104, oil 218, labour 51,
  inflation 190; dollar none). On disk `shock_register.json` still has empty narratives until the next
  daily shock run, by design (firewall).
- Second run: 0 items, 0 classifications inserted, 2,603 skipped_protected (idempotent).
- Firewall `--check` after hydration: OK.

Defects found and fixed while running it for the first time:

1. `pull_news_history.ps1` piped `git archive | tar`; Windows PowerShell 5.1 re-encodes the pipe and `tar`
   failed ("Unrecognized archive format"). Now archives to a file, then extracts.
2. `hydrate_news_history` processed partitions in alphabetical order, so classifications (c) ran before
   items (i). A failure part-way (the first run hit a DuckDB file lock held by an unrelated pytest process)
   stranded 513 classifications with no items. Items now go first; the re-run completed cleanly.
3. The script ran bare `python`; it now uses the repository venv.

## Not done / report-only (Phase 1 fixes, round 2)

- **7c, shock narrative on-disk refresh is not wired.** Hydration fills the store, and `attach_narrative` gives
  non-empty narratives in memory, but nothing in the hydration or events job rewrites `shock_register.json` on
  disk. That file is a protected artifact (plan 9.1); refreshing it belongs to the daily shock run, so wiring it
  from here would make the events job write a firewall file. Left for the shock run or a coordinator ruling.
- **7d, no U+FFFD in the store.** Checked 2026-10-18: 0 `news_items` rows with U+FFFD in title or body. The three
  `decode(..., errors="replace")` sites (`news/fulltext.py:50`, `news/gdelt_backfill.py:195`,
  `news/ingest.py:613`) can in principle introduce it from a badly encoded response; they are unchanged because
  nothing has been observed and replacing them would change ingest behaviour outside this phase.
