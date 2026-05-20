# v0.9-M2 Operational Fixes and Dashboard Polish

## Decision

v0.9-M2 passes as a narrow operational-polish milestone.

The M1 operating trial did not reveal a dashboard rendering blocker. It did
surface a daily-use clarity issue: operating notes need to distinguish
mock-safe/synthetic runs from live AI and real-news runs. The fixes in this
milestone are documentation and checklist improvements only.

## Fixes Made

Updated `README.md`:

- replaced stale v0.5 release-position wording with the current v0.8/v0.9
  operating-trial position
- clarified that the v0.7/v0.8 dashboard is read-only and displays
  backend-generated JSON
- preserved the diagnostic-only project framing

Updated `docs/operations/daily_dashboard_checklist.md`:

- added an operating-mode block for `operating_mode`, `source_profile`,
  `live_ai_used`, and `config_file`
- clarified that the default daily command is mock-safe
- added a reminder to use explicit local/live config or flags for real-news
  live-AI runs
- added checks for classification counts, retry/repair rates, and failures
  when live AI is used

Updated `docs/operations/dashboard_issue_log_template.md`:

- added fields for `operating_mode`, `source_profile`, and `live_ai_used`
- added issue types for live-run and source-profile problems

Updated `docs/model_limitations.md`:

- removed stale v0.5-only framing from the opening description

## No Model Changes

No macro, sector, news, combined, monitoring, or dashboard scoring behavior was
changed. The frontend remains a display layer only.

## Validation

Final validation was run after the M2 edits:

| Check | Result |
| --- | --- |
| `python -m pytest` | passed |
| `python -m ruff check .` | passed |
| `python -m macro_engine.cli validate-config` | passed |
| `python -m macro_engine.cli export-dashboard-data` | passed |
| `npm run build` in `dashboard/` | passed |
| Browser smoke check at `http://127.0.0.1:5173/` | passed |

The browser smoke check confirmed the dashboard title and all primary tabs:
Overview, Macro, Sectors, News, Combined, Monitoring, and History. Console
error count was zero.

## Guardrails

The changes do not add market-action workflow language or frontend logic. The
dashboard remains read-only, and the operating docs explicitly keep scoring
formula changes outside dashboard triage.

## Known Limitations

- This milestone does not create additional real daily run history.
- Source coverage is still thin until mapped real-news profiles are used
  repeatedly.
- Dashboard history is still operating context, not validation evidence.

## Next Step

Continue running the daily operating loop with real mapped source profiles when
available. A future release-hardening step should wait until enough actual
operating history exists to review run reliability and source coverage over
time.
