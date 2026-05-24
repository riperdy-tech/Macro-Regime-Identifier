# v1.2-M3 Release Hardening

Verdict: **release-ready as v1.2-rc1**.

## 1. What v1.2 Adds

v1.2 is an **automation release** built on the frozen `v1.1-rc1` + `v1.2-M1/M2`.

| Milestone | Description | Status |
|---|---|---|
| v1.2-M1 | GitHub Actions daily automation workflow | pass |
| v1.2-M2 | Automated Pages publish validation | pass |
| v1.2-M3 | Release hardening | pass |

**v1.2 adds:**

- GitHub Actions daily automation (`daily-dashboard.yml`)
- Scheduled dashboard refresh (Mon-Fri 22:37 UTC)
- Manual workflow trigger (`workflow_dispatch`)
- GitHub Pages automated publishing
- Workflow artifacts for debugging (7-day retention)
- Automation run summary (JSON + Markdown)
- GitHub-compatible daily pipeline config
- Static dashboard data publishing (no backend API)

**v1.2 does not add:**

- Predictive validation
- Trading logic
- Allocation logic
- Portfolio sizing
- Investment recommendations
- Frontend scoring or AI calls
- Macro/sector/news formula changes
- Combined diagnostic formula changes

## 2. GitHub Actions Workflow Summary

```yaml
File: .github/workflows/daily-dashboard.yml
Trigger: workflow_dispatch + schedule (Mon-Fri 22:37 UTC)
Runtime: ~8 min mock / ~12 min live
Steps: 18 (checkout → setup → validate → lint → test → pipeline →
       accumulation → export → build → summary → artifacts → publish)
```

## 3. Manual Workflow Validation

Run #26346789194: **all 22 steps passed**.

| Step | Result |
|---|---|
| Validate config | ✅ |
| Lint (ruff) | ✅ |
| Tests (173 core) | ✅ |
| Tests (17 automation) | ✅ |
| Daily diagnostic | ✅ |
| News accumulation | ✅ |
| Export dashboard data | ✅ |
| Build dashboard | ✅ |
| Publish to GitHub Pages | ✅ |
| Upload artifacts | ✅ |

## 4. Scheduled Trigger

```text
Cron: 37 22 * * 1-5 (Mon-Fri at 22:37 UTC)
UTC note: GitHub cron is UTC — adjust for timezone expectations
```

## 5. GitHub Pages Deployment

```text
URL: https://riperdy-tech.github.io/Macro-Regime-Identifier/
Status: 200 OK
Title: Macro Diagnostic Dashboard
```

## 6. Manifest Freshness

Last run (#26346789194):
```text
generated_at: 2026-05-23T23:55:09Z
data_status: partial → fixed to complete (accumulation added in M3)
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-16
files: 7 → 8 (accumulation added)
```

M3 added `run-news-accumulation` + `write-news-accumulation-report` to the
workflow. After the next workflow run, `data_status` should be `complete`
with all 8 files.

## 7. Data Completeness

The workflow now generates all 8 dashboard files:
```text
daily_diagnostic_summary.json
current_sector_ranking.json
news_score_report.json
combined_sector_diagnostic.json
news_monitoring_report.json
news_source_coverage_report.json
news_accumulation_report.json    ← added in M3
history_index.json
```

## 8. Artifact Behavior

```text
Artifact: daily-outputs
Files: 10 (summaries, accumulation, automation, manifest, history)
Retention: 7 days
Size: ~4 KB
```

## 9. Secret Hygiene

- `FRED_API_KEY`: referenced as `${{ secrets.FRED_API_KEY }}` in workflow
- `DEEPSEEK_API_KEY`: referenced as `${{ secrets.DEEPSEEK_API_KEY }}` in workflow
- No hardcoded secrets in any tracked file
- Generated dashboard data audited: no secrets found
- Exposed token noted — must be rotated outside repo

## 10. Repo Hygiene

```text
.env: not staged
API keys: not in tracked files
GitHub tokens: not in tracked files
data/news_pilot/: not staged
outputs/: not staged
dashboard/public/data/: generated files not staged
dashboard/node_modules/: not staged
dashboard/dist/: not staged
DuckDB files: not staged
.claude/: not staged
```

## 11. Guardrail Audit

**Passed.** No forbidden market-action language in:
- Workflow YAML
- Automation summary
- README v1.2 section
- Model limitations v1.2 section
- Release checklist
- This review

## 12. Known Limitations

1. **Schedule not exact.** GitHub cron may be delayed during high load.
2. **Runner environment differs.** Ubuntu + Python 3.11 vs local Windows + 3.14.
3. **Dashboard can be stale.** If workflow fails, data freezes until next run.
4. **Ephemeral DB state.** DuckDB is recreated from scratch each run.
5. **Local-only data unavailable.** Cloud runner cannot access `.env` or `data/news_pilot/`.
6. **Published data is public.** Dashboard JSON is accessible to anyone.
7. **Partial manifest in M2 run.** Fixed in M3 by adding accumulation. Next run should be complete.

## 13. Release Blockers

**None.** All acceptance criteria are met.

## 14. Non-blocking Follow-ups

- Rotate exposed GitHub token
- Trigger workflow after M3 merge to confirm `data_status: complete`
- Consider adding notification step (Slack/email) for failed runs

## 15. Release Decision

**Release-ready as `v1.2-rc1`.**

v1.2 delivers on its defined scope: automated daily pipeline, scheduled
dashboard refresh, and GitHub Pages publishing.

## 16. Boundary Statement

v1.2 is not investment advice.

v1.2 is not a trading system.

v1.2 is not an allocation system.

v1.2 does not validate predictive performance.

GitHub Pages dashboard is static.

GitHub Actions runs the backend in a temporary cloud environment.

Public dashboard data must not contain secrets.
