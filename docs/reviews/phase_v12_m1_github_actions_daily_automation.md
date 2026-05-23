# v1.2-M1 GitHub Actions Daily Automation

Verdict: **pass**.

## 1. What Was Built

A GitHub Actions workflow that automates the full daily diagnostic pipeline:
backend → dashboard export → dashboard build → GitHub Pages publish.

### Files Created

| File | Purpose |
|---|---|
| `.github/workflows/daily-dashboard.yml` | Scheduled + manual workflow |
| `config/daily_pipeline_github.yaml` | GitHub-compatible config (mock-safe) |
| `src/macro_engine/automation.py` | Automation run summary generator |
| `tests/test_phase_v12_m1_github_automation.py` | 17 tests |
| `src/macro_engine/cli.py` | Added `write-automation-summary` command |

## 2. Workflow Design

### Triggers

- **Manual**: `workflow_dispatch` with inputs (run_mode, max_live_items, source_profile)
- **Scheduled**: Mon-Fri at 22:37 UTC (cron: `37 22 * * 1-5`)

### Job Steps

```text
1. Checkout
2. Setup Python 3.11
3. Install Python deps (pip install -e ".[dev]")
4. Setup Node 22
5. Install dashboard deps (npm ci)
6. Validate config
7. Lint (ruff)
8. Run tests (pytest)
9. Run daily diagnostic (mock or live)
10. Export dashboard data
11. Build dashboard (npm run build)
12. Write automation summary
13. Upload artifacts (7-day retention)
14. Publish to GitHub Pages
```

### Mock vs Live Modes

- **Mock (default)**: Uses `synthetic_sample` profile, no AI calls, no secrets needed for news
- **Live**: Uses `--live-ai` flag, requires `DEEPSEEK_API_KEY` secret
- **Secrets**: `FRED_API_KEY` (for macro), `DEEPSEEK_API_KEY` (for live AI)

## 3. GitHub-Compatible Config

`config/daily_pipeline_github.yaml`:
- Uses `synthetic_sample` source profile (committed, works on GitHub runner)
- Mock mode by default (`allow_live_ai: false`)
- Does not reference local-only files (`data/news_pilot/`)
- No secrets in config text

## 4. Automation Summary

The `write-automation-summary` command produces:
- `outputs/automation_run_summary.json`
- `outputs/automation_run_summary.md`

Includes: macro regime, top sectors, dashboard status, accumulation readiness,
GitHub run metadata (when available), and the required disclaimer.

## 5. Test Results

```text
tests/test_phase_v12_m1_github_automation.py: 17 passed
- AutomationSummary: 5 tests (build/write/env vars)
- WorkflowConfig: 5 tests (existence, triggers, secret safety)
- DailyPipelineConfig: 5 tests (mock default, no local files, no secrets)
- SecretSafety: 2 tests (no keys in exports)
```

## 6. Known Limitations

1. **FRED_API_KEY required for macro.** Even in mock mode, the macro pipeline
   fetches live FRED data. Without the secret, the workflow will fail at the
   macro step.

2. **Mock news only on GitHub runner.** Live AI requires `DEEPSEEK_API_KEY`
   and the workflow must be set to live mode. Default is mock.

3. **No local data files on runner.** `data/news_pilot/` and `.env` are
   gitignored. The workflow cannot access them. The GitHub config uses
   committed synthetic data.

4. **Schedule is approximate.** GitHub Actions cron may be delayed during
   high load. Do not treat the time as exact.

5. **Not yet live-tested.** The workflow file exists and is valid, but has
   not been triggered on GitHub. M2 will validate Pages publishing.

## 7. Whether M2 Can Proceed

**Yes.** The workflow exists, tests pass, config is valid, and automation
summary works. M2 should validate that the workflow publishes to GitHub Pages.

## 8. Boundary Statement

This review is not investment advice.

This system is not a trading system.

No scoring formulas were changed.

No trading, allocation, or recommendation logic was introduced.

Secrets are referenced only via `${{ secrets.X }}` — no actual keys in code
or config files.
