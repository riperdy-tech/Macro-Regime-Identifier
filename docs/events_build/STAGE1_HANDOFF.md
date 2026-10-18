# Stage 1 handoff to the screener side

For whoever builds the screener consumer. MRI side: `STAGE1_RUNBOOK.md`. Meaning of the fields: `CANDIDATE_REGISTER_SPEC.md`.
Nothing here asks the screener to change a number: D1 stands (news never alters a number), and a candidate is a record only.

## Artifacts

| file | where (MRI repo) | role |
| --- | --- | --- |
| `event_register.json` | `outputs/event_register.json` (resolve through `MRI_OUTPUTS_DIR`, as the other MRI outputs) | the register |
| `event_register_run.json` | `outputs/event_register_run.json` | status of the latest run: read it for freshness and failure |
| dated copies | `outputs/archive/<as_of_date>/events_daily/event_register.json` | history; not needed by the UI |

`event_register.json`: `artifact: "event_register"`, `schema_version: 1`. Additive fields may appear without a version change;
a removed or re-typed field bumps the version, and the consumer must refuse a `schema_version` it does not know.
`event_register_run.json`: `artifact: "event_register_run"`, `schema_version: 1`.

## Fields the consumer should read

Top level: `schema_version`, `as_of_date` (UTC day), `as_of` (instant), `grouping_mode` (`syndication` in production),
`experimental` (bool), `notice` (plain-language text to show), `warnings` (list), `candidate_count`, `candidates`.

Per candidate (id `candidate_id`, stable `evt_...`):

- `title` (display title of the earliest report), `report_ids`, `item_ids`, `deduped_report_count`, `syndicated_copy_count`
- `onset` (`published_at` of the earliest report, null with flag `published_at_missing`), `first_available_at`
  (`first_seen_at`, when the system could first know), `last_report_at`
- `status_window.status` (`open` or `closed`), `status_window.closed_on`
- `publisher_count`, `distinct_publishers[]` (publisher, `report_ids`, `syndication_provenance` with wire-copy items)
- `official_source` (bool), `corroboration.category` (`officially_sourced`, `independently_corroborated`, `single_source`;
  `conflicting` is never produced in Stage 1) and `corroboration.evidence`
- `classification_status` (`classified_reports`, `total_reports`, `coverage`, per-report `status`, `classified_at`)
- `entities_resolved[]` (`key`, `tickers`, `names`, `flags`), `entities_unresolved[]` (`normalized`, `names`, `status`: `unresolved`,
  `generic`, `ambiguous`), `resolver_flags`
- `themes[]` (`theme_id`, `direction`, `role: "annotation_only"`): annotations, never a grouping or dedup key
- `market_response`: `{"checks": []}` until E2
- `related_candidates`: always empty when `grouping_mode` is `syndication`
- `flags[]`: `single_source`, `unclassified_reports`, `syndication_only`, `published_at_missing`, `resolver_flags`, others

Ignore unknown fields. Treat any other field as not part of the contract.

## What `mri_sync` must copy (describe only; no screener file was edited)

`stock-screener-v3/scripts/mri_sync.py` and its copy in `screener-publish/scripts/mri_sync.py` (the clone the workspace rule
names for publishing) define `SNAPSHOT_FILES`, a tuple of five names copied from `peer_paths.mri_outputs_dir()` into
`public/data/mri/`, with a `manifest.json` of `mri_date` and sha256. To carry the register:

1. Add `"event_register.json"` and `"event_register_run.json"` to `SNAPSHOT_FILES`.
2. The manifest reads `mri_date` from a top-level `date` key (`json.loads(...).get("date")`). The register has no `date`: it has
   `as_of_date`. Either read `as_of_date` for these two files or accept `mri_date: null`; do not add `date` to the register
   to satisfy the sync without a ruling, because it is the screener's reader, not MRI's contract.
3. These two files are optional for the sync as written (`if not src.exists(): continue`), which is the wanted behaviour: a missing
   register must not fail the screener run.
4. MRI side, not yet done: `scripts/run_local_daily.ps1` step 7 copies only the five existing files to
   `C:\ProgramData\stocks-mri\outputs`, the folder the screener runner (NETWORK SERVICE) can read. The two new files must be
   added to that list as optional (its loop exits 4 on a missing file, which must not apply to them), or the runner will never
   see the register. That is a separate, small MRI change awaiting the operator's go; the job writes to `outputs/` only.

## Freshness semantics for the UI

- Show "as of" with `as_of_date` (UTC day) and the run's `finished_at`. The register for day D is built from items first seen
  through the end of D, so a 08:30 KST run on D+1 shows D.
- Stale warning rule: show a stale banner when the register's `as_of_date` is more than 1 day behind today's UTC date, or when
  `event_register_run.json` has `status: "failed"` (the register shown is then yesterday's or older: say that it is). A missing
  register shows nothing, not an error.
- Never infer freshness from file modification time; use the two fields above.
- Do not show a candidate older than its `status_window`: `closed` candidates are kept and shown as closed, not hidden.

## Display rules

- Never call a candidate a confirmed or verified event, in text, labels, colours or icons. Use "candidate" and show `notice`
  somewhere visible on the page that lists them.
- Show single-source candidates; label them "single source". Do not hide, dim away or rank them out by that flag.
- In `syndication` mode a candidate is one report: do not render "related" or "same story" groupings. With `experimental: true`
  (arm modes), label any grouping "experimental".
- Show `classification_status` honestly: an unclassified candidate has no entities or themes yet, say so, do not fill gaps.
- Leave the market-response block empty (no chart, no "no impact" text) until E2 fills `market_response.checks`.
- `entities_unresolved` of status `generic` (e.g. "United States") are not companies; do not link them to tickers.
- Themes are annotations. Never use them to group or merge candidates.
- Nothing on this page may change a screener score, rank or verdict.
