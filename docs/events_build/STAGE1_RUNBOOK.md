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

## Decided 2026-10-22: the events job owns local news hydration (option a below; STEP0_RECORD.md)

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

## Schedule change, 2026-10-22: 13:00 KST, not 08:30

The 08:30 time assumed the cloud news commit lands about 07:40 KST. Measured over 2026-10-08..10-21, GitHub starts the
`37 22 * * *` cron 1.5-4.5 h late, so `run-history` is committed at about 09:45-12:40 KST. At 08:30 the job would always
build from the previous day's news. The registrar default is now 13:00 KST. A cloud run later than that still leaves the
store within the 48 h freshness limit, so the job builds from the previous day's items and says so through `as_of_date`.

The job now also exports `event_register.json` and `event_register_run.json` to `C:\ProgramData\stocks-mri\outputs` (step 4),
and `run_local_daily.ps1` exports `shock_register.json` there as an optional file (D5).

## E2 and E4 added, 2026-10-22

- New step 2b, `refresh-confirmation-prices`: base instruments, sector ETFs, and the tickers named by yesterday's open
  candidates. Never fatal. A ticker named for the first time is fetched the next day, so its company check reads
  `no_stock_series` on day one and is filled on the next run (the register recomputes every candidate daily).
- `events-daily` fills `market_response` (`events/market_check.py`) and `sector_exposure` (`events/exposure.py`) and adds
  `market_checks`. Rulings on the seven open E2 details: `config/events.yaml` `unratified.*.ruling`.
- E3 (plan §7's active / decaying / closed lifecycle) is not built. It is defined over linked events, and the register runs
  in `syndication` mode (one report per candidate) until a linker passes the blind holdout. Each candidate keeps its 3-day
  `status_window`. E3 is built with the first approved linker.
- Dry run 2026-10-22 on a copy of the store (as of 10-21; nothing in the repo's `outputs/` or store was written):
  648 candidates; theme results aligned 37, no_signal 136, pending 122, display_only 198, not_scored 80; 49 shared check
  keys; firewall OK; 17 s.

## Risk: the store lock during a GPU run

DuckDB allows one writer and no concurrent readers from other processes. rs2 (`rs2_data._mri_query`) reads the MRI store
for the risk-free legs and returns an empty result when the file is locked, so the run continues **without** the input.
This job holds the store for about 20 s a day; the 06:30 MRI daily holds it longer. A GPU run that reads the risk-free rate
during either window loses that input silently. Until rs2 retries on a locked file, avoid scheduling GPU runs across
13:00 KST, or have rs2 read a copy.

Update 2026-10-22: rs2 `mri/query-retry-on-lock` (dd7f9ff) retries a locked store 6 x 10 s, then raises `MriStoreLocked`.
The Phase 4 coordinator approved it; it merges into `review-2026-10/r26-int` after the full rs2 suite. Exposure is mainly
in point-in-time builds (time_travel_trial, the acceptance tools). Live-mode packs read the anchor JSONs, and
`bill_rate_mean` reads a parquet, so neither touches the store.
