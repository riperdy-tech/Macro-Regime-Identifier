# Phase 3c — classification overlay for run (b). Branch feat/mri-events. Rules as BRIEF_P1.md. No push/merge. Commit by pathspec, trailer "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>".

Goal: let acceptance run (b) ("as-if-all-classified") execute, WITHOUT mutating `data/macro_engine.duckdb` and WITHOUT disturbing run (a).
Authority: D2 (operator-approved classification expansion) covers this one-off DeepSeek spend (~158 items, ~216k tokens). Do not exceed the 158
items in `tests/fixtures/events_reference/classification_state.csv` (state unclassified/failed). Never print, copy, log or commit any API key;
use the existing env/config mechanism the classify CLI already uses. Do not edit `.env*`. If no key is available, STOP and report — do not look for keys elsewhere.

1. Make a copy of the store under `data/backups/` (e.g. `macro_engine_before_P3C_20261018.duckdb`), then a second working copy for classification.
   Classify the 158 items in the WORKING COPY using the repo's existing classification code path (same prompt/model/config as production:
   `config/news_ai_live.yaml` etc.; reuse, do not fork). Measure actual tokens used and record them (this also gives the first real cost
   measurement for E0.2a — put it in STEP0_RECORD.md, labelled with the model and prompt version). Respect the existing per-run timeouts;
   run in batches if needed. Record failures; retry once.
2. Export ONLY the new classifications for those items to `tests/fixtures/events_reference/classification_overlay.parquet` (or csv.gz) using the
   same schema as the store's classification table + `overlay_source: P3C`, `classified_at` = actual time. Privacy: headline/metadata/classification
   fields only (same allow-list as `news/history.py`).
3. Wire `scripts/events_replay.py` run (b): `--classification as_if_all` merges the overlay, ignoring `classified_at <= as_of` ONLY for
   overlay rows (the as-stored rows keep the no-look-ahead rule). Unit test: run (a) results are byte-identical before and after the overlay file exists.
4. Run acceptance (b) ONCE per linking mode (v2, v2_2), whole-store pool, scored on the 300 reference items. Record in
   `docs/events_build/E1_ACCEPTANCE.md` as INFORMATION (reference set was seen). No parameter changes afterwards. Report per-gate outcomes incl. where
   Aden lands, hub sizes, remaining false-split/merge causes.
5. Firewall check, ruff, new tests; full suite not needed (no src/ change except replay script) — run the events tests only.

Report ≤20 lines: commits, tokens used, failures, run (b) numbers both modes, per-gate result, anything not done.
