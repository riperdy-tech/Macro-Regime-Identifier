# v1.2-M2 Automated Pages Publish Validation

Verdict: **pass — workflow ready, pending live GitHub Actions trigger**.

## 1. What M2 Delivers

Validation documentation for the automated GitHub Pages publishing workflow
built in M1. Since the workflow cannot be triggered from a local development
environment, this review documents the expected behavior, verification steps,
and guardrails.

## 2. Workflow Status

| Item | Status |
|---|---|
| Workflow file exists | ✅ `.github/workflows/daily-dashboard.yml` |
| Manual trigger (`workflow_dispatch`) | ✅ Configured with mode/profile inputs |
| Scheduled trigger | ✅ Mon-Fri 22:37 UTC |
| Mock mode default | ✅ Uses `daily_pipeline_github.yaml` |
| Live mode supported | ✅ Via workflow_dispatch input |
| Secrets referenced safely | ✅ `${{ secrets.FRED_API_KEY }}` format |
| Artifact upload | ✅ 7-day retention |
| GitHub Pages publish | ✅ `peaceiris/actions-gh-pages@v4` |

## 3. Local Validation

```text
pytest: 17/17 new automation tests pass
ruff: All checks passed
validate-config: 13 sources, 11 dimensions, 6 regimes
write-automation-summary: Works (outputs/automation_run_summary.json + .md)
dashboard build: pending (run npm run build to confirm)
```

## 4. GitHub Pages Publishing Approach

The workflow uses the same publish mechanism as the existing `pages.yml`:

- Publisher: `peaceiris/actions-gh-pages@v4`
- Token: `${{ secrets.GITHUB_TOKEN }}`
- Branch: `gh-pages`
- Source: `dashboard/dist` (built during workflow)

Expected URL after deployment:
`https://riperdy-tech.github.io/Macro-Regime-Identifier/`

## 5. Website Verification Checklist

After the first successful workflow_dispatch run:

- [ ] Site returns HTTP 200
- [ ] Title shows "Macro Diagnostic Dashboard"
- [ ] Overview page renders
- [ ] History page renders
- [ ] Latest generated timestamp is updated
- [ ] `manifest.json` shows `data_status: complete`
- [ ] No console errors (browser check)

## 6. Secrets Required

| Secret | Set? | Notes |
|---|---|---|
| `FRED_API_KEY` | Must be set in repo Secrets | Required for macro data fetch |
| `DEEPSEEK_API_KEY` | Optional | Only needed for live AI mode |

Without `FRED_API_KEY`, the workflow fails at the macro step.

## 7. Source Profile

Default: `synthetic_sample` (committed, works on GitHub runner).
Live-news runs need public RSS sources configured in `news_sources.yaml`.

## 8. Dashboard Freshness

After a successful run, the dashboard should reflect:
- Latest macro date (from FRED, typically ~1 month behind)
- Latest news score date
- Updated History tab entries
- Fresh `manifest.json` timestamp

## 9. Artifact Behavior

Artifacts uploaded: `daily-outputs` (7-day retention):
- `daily_diagnostic_summary.json`
- `daily_diagnostic_summary.md`
- `automation_run_summary.json`
- `automation_run_summary.md`
- `manifest.json`
- `history_index.json`

## 10. Known Limitations

1. **Not yet live-tested.** The workflow has not been triggered on GitHub.
   Manual workflow_dispatch is the first recommended step after merge.

2. **FRED_API_KEY must be set.** The macro pipeline requires this secret.
   Mock mode still needs live FRED data.

3. **No local data access.** `data/news_pilot/` files are gitignored and
   unavailable on the GitHub runner. Use committed sample data or public
   RSS sources.

4. **Schedule is approximate.** GitHub Actions cron may be delayed during
   high load.

5. **No failure notification.** The workflow logs failures in GitHub Actions
   but does not send email/Slack notifications. Add notification steps
   in a future milestone if needed.

## 11. Whether v1.2 Can Proceed to Release Hardening

**Not yet.** The workflow should be live-tested first:
- Merge to master
- Run manual `workflow_dispatch` (mock mode)
- Verify GitHub Pages updates
- Fix any issues
- Then proceed to release hardening

## 12. Guardrail Audit

Scanned: new docs, workflow YAML, automation module, tests.
No forbidden market-action language found.
All disclaimers present where required.

## 13. Repo Hygiene

```text
.env: unstaged
API keys: not in any tracked file
data/news_pilot/: unstaged
outputs/: unstaged
dashboard/public/data/: unstaged
dashboard/node_modules/: unstaged
DuckDB files: unstaged
.claude/: unstaged
```

Only tracked additions: workflow, config, source module, tests, docs.

## 14. Boundary Statement

This review is not investment advice.

This system is not a trading system.

No scoring formulas were changed.

No trading, allocation, or recommendation logic was introduced.

The automated workflow publishes diagnostic data only. The dashboard
remains read-only with no AI calls or scoring logic.
