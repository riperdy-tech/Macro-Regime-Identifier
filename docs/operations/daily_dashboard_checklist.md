# Daily Dashboard Checklist

Use this checklist during routine local dashboard review.

## Run Backend Workflow

- [ ] Record operating mode before running:

```text
operating_mode:
source_profile:
live_ai_used:
config_file:
```

- [ ] Run daily diagnostic. The default project config is mock-safe and does
  not call a live AI provider:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --archive
```

- [ ] For a live AI or real-news run, use an explicit local/live config or
  command flag and record the source profile. Confirm the run is bounded,
  observable, and safe to resume before relying on it.

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
- [ ] If the run used mock/synthetic data, do not treat it as live operating
  evidence.
- [ ] If the run used live AI, confirm classification counts, retry/repair
  rates, and failures are visible in reports.

## Record Issues

- [ ] Record any display, data freshness, missing-file, or usability issue using
  `docs/operations/dashboard_issue_log_template.md`.
- [ ] Do not change scoring formulas during dashboard triage.
- [ ] Do not place API keys, generated outputs, archives, or local data into git.
