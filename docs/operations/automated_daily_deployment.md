# Automated Daily Dashboard Deployment

How the `v1.2` automated daily pipeline + GitHub Pages deployment works.

## How It Works

```text
GitHub Actions (scheduled or manual)
  → Run daily diagnostic pipeline
  → Export dashboard JSON data
  → Build React dashboard
  → Publish to GitHub Pages (gh-pages branch)
```

The workflow runs in `.github/workflows/daily-dashboard.yml`.

## Schedule

```text
Cron: 37 22 * * 1-5  (Mon-Fri at 22:37 UTC)
```

GitHub cron is UTC. Adjust for your timezone expectation.
Scheduled runs may be delayed during GitHub Actions load.

## Manual Trigger

1. Go to: `https://github.com/riperdy-tech/Macro-Regime-Identifier/actions/workflows/daily-dashboard.yml`
2. Click "Run workflow"
3. Select branch: `master`
4. Choose mode: `mock` or `live`
5. Click "Run workflow"

## Required GitHub Secrets

Set these in repository Settings → Secrets and variables → Actions:

| Secret | Required For | Notes |
|---|---|---|
| `FRED_API_KEY` | Always (macro pipeline) | 32-char lowercase from FRED |
| `DEEPSEEK_API_KEY` | Live AI mode only | DeepSeek API key |

Without `FRED_API_KEY`, the macro pipeline fails. The workflow will not
complete successfully.

## Mock vs Live Mode

### Mock Mode (default)

- Uses `config/daily_pipeline_github.yaml`
- News source: `synthetic_sample` (committed sample data)
- No live AI classification
- Only `FRED_API_KEY` needed (for macro data)
- Safe for scheduled runs

### Live Mode

- Manual trigger only (select `live` from dropdown)
- Uses `--live-ai` flag
- Requires `DEEPSEEK_API_KEY` secret
- Classifies up to 25 items per run (configurable)
- Uses `synthetic_sample` profile by default on GitHub runner
- For real-news profiles, configure a public RSS source in `news_sources.yaml`

## Expected Runtime

```text
~5-10 minutes for mock mode (FRED fetch + pipeline + dashboard build + deploy)
~8-15 minutes for live mode (+ AI classification)
```

GitHub Actions has a 30-minute timeout configured.

## Where Logs/Artifacts Live

- **GitHub Actions run page**: Full logs for each step
- **Artifacts**: `daily-outputs` (7-day retention) — includes:
  - `daily_diagnostic_summary.json`
  - `daily_diagnostic_summary.md`
  - `automation_run_summary.json`
  - `automation_run_summary.md`
  - `manifest.json`
  - `history_index.json`

## How to Debug Failed Runs

1. Go to the Actions tab
2. Click the failed run
3. Expand the failing step
4. Common issues:
   - `FRED_API_KEY` missing → set the secret
   - FRED API timeout → retry; network may be transient
   - Test failure → check `pytest` step output
   - Dashboard build failure → check `npm run build` output

## How to Confirm Pages Updated

1. Visit: `https://riperdy-tech.github.io/Macro-Regime-Identifier/`
2. Check the Overview page for the latest macro date
3. Check History tab for the most recent run
4. The `manifest.json` timestamp should update after each successful run

## What NOT to Commit

- `.env` or any file containing secrets
- `data/news_pilot/` real news files
- `data/*.duckdb` database files
- `outputs/` (generated locally)
- `outputs/archive/` (generated locally)
- `outputs/replay/` (generated locally)
- `dashboard/public/data/` generated files
- `dashboard/node_modules/`
- `dashboard/dist/` (generated during workflow)
- `logs/`
- `.claude/`

These are gitignored. If they appear in `git status`, do not stage them.

## Notes

- This is a diagnostic platform, not a trading system.
- Dashboard is read-only. No AI calls or scoring in the frontend.
- Generated data is not committed to the repository.
- The `gh-pages` branch contains only the built dashboard artifact.
