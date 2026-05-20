# v0.8-M2 Dashboard History Views Review

Verdict: pass.

v0.8-M2 adds lightweight history visibility to the read-only dashboard. The
history view is derived from archived daily summary JSON files and does not
calculate new diagnostic scores.

## History Export Behavior

`export-dashboard-data` now writes:

```text
dashboard/public/data/history_index.json
```

The index is built from:

```text
outputs/archive/*/*/daily_diagnostic_summary.json
```

Each history row includes:

- run id
- run date
- status
- archive path
- macro regime
- macro confidence
- top combined sectors
- readiness label
- guardrail status
- classification success rate
- max overlay rank change
- warning count
- error count

If no archived daily summaries are available, the export writes an empty history
array with `history_status: empty`.

## History Page Behavior

The dashboard now includes a History tab.

It displays:

- history status
- recorded run count
- latest run
- average macro confidence across recent rows
- short-history message when fewer than five rows are available
- recent daily run table
- run status, macro regime, combined top sectors, readiness label, guardrail
  status, warnings, and errors

The view is read-only and uses exported backend summaries only.

## Missing-History Behavior

When history is unavailable, the dashboard renders a clear empty state:

```text
No archived daily runs found.
```

When history exists but is short, it renders:

```text
Not enough history yet. Continue daily runs before interpreting trends.
```

## Sample Fixture Behavior

Committed sample data now includes:

```text
dashboard/public/sample-data/history_index.json
```

The sample fixture is synthetic and is only for local UI development and missing
data fallback.

## Build And Test Results

Commands run:

```powershell
python -m pytest tests/test_phase_v07_dashboard.py
python -m ruff check src/macro_engine/dashboard_export.py tests/test_phase_v07_dashboard.py
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm run build
```

Results:

```text
focused tests: 4 passed
ruff: passed
export-dashboard-data: passed
npm run build: passed
```

Full validation is covered by the final bundled milestone validation.

## Guardrail Audit

Dashboard source text and sample fixtures avoid market-action language. The
history page uses operating labels such as status, confidence, readiness, warning
count, error count, and archive path.

## Known Limitations

- The history index depends on archived daily summaries.
- It is not a performance validation feature.
- Older archives may not contain every modern summary field.
- Short history windows should be treated as operating context only.

## M3 Readiness

v0.8 can proceed to release hardening once final full validation passes.
