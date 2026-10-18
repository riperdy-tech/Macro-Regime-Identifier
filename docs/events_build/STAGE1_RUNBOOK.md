# Stage 1 daily events job: runbook

Phase 5b. Operator decisions: D3 (events job about 08:30 KST, triggered by successful hydration, freshness checks, a failure
report; a morning update, not intraday) and D5 (additive, versioned `event_register.json`). Consumer contract:
`STAGE1_HANDOFF.md`. Register meaning: `CANDIDATE_REGISTER_SPEC.md`.

## What runs

`scripts/run_events_daily.ps1` (`.sh` twin beside it), in order, stopping at the first failure:

1. Import check, `scripts/check_macro_engine_import.py`, with the repository venv's interpreter.
2. Hydration, `scripts/pull_news_history.ps1`: `git fetch origin run-history`, then `import-news-history` (insert-if-absent,
   idempotent) with `--max-age-hours` (env `NEWS_HYDRATION_MAX_AGE_HOURS`, default 48). A hydration error, an empty manifest or
   a stale store exits non-zero and the events job does not start. The events job does not trust that: step 3 checks again.
3. `python -m macro_engine.cli events-daily` (`src/macro_engine/events/daily.py`):
   1. Freshness: the store's newest `first_seen_at` must be at most `--max-age-hours` old. Stale: failure record, exit 1,
      **no register written**, yesterday's file untouched.
   2. Firewall snapshot (before): the protected artifacts and the `raw_observations` rows for DGS10, DFII10, T10YIE, hashed with
      the logic of `scripts/events_firewall_hash.py`.
   3. Build for `as_of` (UTC day, default today): collapse the 7-day pool by syndication, assign each new report to a candidate
      through the persistent event state, close stale candidates, build the document.
   4. Firewall snapshot (after) compared with before. Any difference: failure record, exit 1, no register written.
   5. Atomic write (temp file + rename) of `outputs/event_register.json`, a dated copy at
      `outputs/archive/<as_of>/events_daily/event_register.json`, and `outputs/event_register_run.json`.

Grouping is `register.grouping` in `config/events_register.yaml`, default `syndication`: every syndication-collapsed report is
its own candidate, nothing is linked across reports, `related_candidates` is empty, `grouping_mode` says so. `arm_a` and
`arm_b` stay selectable (`--grouping`) and are labelled `experimental: true` with an experimental notice in the file. One store
holds one grouping mode: the job refuses to switch mode on a store that already holds candidates written under another.

Idempotent: the register is a pure function of (store, as-of day); it carries no wall-clock field. The same day twice writes
the same bytes (`register_changed: false` in the run record). Verified on the real store, see below.

Failure behaviour: on any failure the previous `event_register.json` stays exactly as it was; `event_register_run.json` says
`status: failed` with the errors (and `previous_register_kept`). The script writes `logs/events/last_failure.txt` (removed on
success) and exits non-zero. A consumer decides staleness from the register's `as_of_date` and the run record (see handoff).

Logs: `logs/events/events_daily_<timestamp>.log`. No window or prompt is needed; no network call apart from the `git fetch`
of hydration (and nothing is pushed).

## Register it (operator action; NOT done by the build)

```
powershell -ExecutionPolicy Bypass -File scripts\register_events_daily_task.ps1            register "MRI-Events-Daily"
powershell -ExecutionPolicy Bypass -File scripts\register_events_daily_task.ps1 -Remove    roll back
```

The registrar computes 08:30 Korea Standard Time as a local clock time with `[TimeZoneInfo]` (on this machine, whose zone is
Korea Standard Time, that is 08:30). The trigger is a local time of day, so on a machine in a daylight-saving zone it shifts
against KST at a clock change; re-run the registrar then. The task runs hidden (`-WindowStyle Hidden`), start-when-available,
one instance at a time, one hour limit. The 06:30 `MRI-Local-Daily` task is unaffected; this task is separate, so rolling it
back removes the events job and nothing else. Without the task, run the script by hand.

Rollback of data: delete nothing. A bad register is superseded by the next successful run; the archive keeps each day's copy.
The additive tables (`event_state_*`, `event_register_*`) are not read by any existing output.

## Open decision (blocks registering): who owns local news hydration

Step 2 depends on local hydration running daily. `STEP0_RECORD.md` records that no scheduled owner is ratified. Until the
operator names one, nothing should be scheduled that depends on it silently. Two clean options: (a) this job owns it, as built
(the script runs `pull_news_history.ps1` itself and fails loudly if it cannot); (b) another owner runs hydration earlier and this
job runs only `events-daily` (drop step 2 from the script). Either way the freshness check in `events-daily` stands. Also open
for the screener side: the files the screener reads and the 06:30 export step, see `STAGE1_HANDOFF.md`.

## Dry run, as_of 2026-10-18

Run on a COPY of `data/macro_engine.duckdb` with outputs in the scratchpad and the protected artifacts read from the real
`outputs/` (nothing in the repository's `outputs/` or store was written). Store newest `first_seen_at` 2026-10-18T00:26Z, age
6.6 h; 27,594 items, 3,170 classifications.

| measure | value |
| --- | --- |
| pool reports (7 days, syndication-collapsed) / items | 667 / 729 (62 copies collapsed into earlier reports) |
| candidates | 667 (391 open, 276 closed) |
| single-source candidates | 657 (98.5%); officially sourced 10; independently corroborated 0 |
| classified reports | 320 of 667; unclassified share 52.0% |
| candidates with at least one resolved entity | 82 |
| firewall | OK (in-process, before == after); `--check` against `firewall_baseline.json` on the real tree also OK |
| second run, same day | byte-identical (`register_changed: false`), sha256 d85f79cd... |
| build time | about 5 s (job about 6 s; the CLI call about 20 s including imports) |

Reading the numbers: the unclassified share is high because classification is capped per day (the E0.2a cap change is
separate and still needs 14 days of data); every unclassified candidate is shown with `unclassified_reports` and an empty
entity list, never hidden. The 98.5% single-source figure is publisher independence, not truth: no candidate is called
confirmed. Candidates from reports first seen 10-11 and 10-12 were closed on the first run (older than the 3-day open window).

## Not done / caveats

- No scheduled task registered; no `.github/`, CI, screener, `mri_sync` or `.env*` file touched.
- `arm_b` in the daily job reads stored vectors only; no embedding job runs daily, so new reports have no vector (arm B then
  scores as arm A with the dense term zero). It is experimental either way.
- `RegisterStore.record` keeps the first-seen detail of a report; the daily job refreshes the stored detail of a report that
  was unclassified when first seen and has since been classified (otherwise it would show "unclassified" for good). It
  does not rewrite the state's `entity_keys` for that report, which matters only to the linker arms.
