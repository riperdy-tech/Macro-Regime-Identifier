# Daily Dashboard Checklist

Use this checklist during routine local dashboard review.

## Run Backend Workflow

- [ ] Run daily diagnostic:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
```

- [ ] Export dashboard data:

```powershell
python -m macro_engine.cli export-dashboard-data
```

- [ ] Start dashboard:

```powershell
cd dashboard
npm run dev
```

## Confirm Dashboard State

- [ ] Overview page renders.
- [ ] Latest run date matches the expected daily run.
- [ ] Latest run id is visible.
- [ ] Archive path is visible.
- [ ] Macro date is visible.
- [ ] News score date is visible.
- [ ] Dashboard data status is `complete` or any missing files are understood.
- [ ] Readiness label is visible and still honest.
- [ ] Guardrail status is visible.
- [ ] Source coverage warnings are visible.
- [ ] History page shows recent archived runs or a short-history message.

## Record Issues

- [ ] Record any display, data freshness, missing-file, or usability issue using
  `docs/operations/dashboard_issue_log_template.md`.
- [ ] Do not change scoring formulas during dashboard triage.
- [ ] Do not place API keys, generated outputs, archives, or local data into git.
