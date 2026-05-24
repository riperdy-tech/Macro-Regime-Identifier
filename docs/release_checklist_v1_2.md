# v1.2 Release Checklist

Release target: `v1.2-rc1`

Release position: v1.2 is an automation release. It adds GitHub Actions daily
automation, scheduled dashboard refresh, GitHub Pages automated publishing,
and workflow artifacts for debugging.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli export-dashboard-data`
- [ ] `cd dashboard && npm run build`
- [ ] Workflow YAML review
- [ ] Manual GitHub Actions workflow run
- [ ] Dashboard smoke check
- [ ] GitHub Pages availability check

## Expected Automation Outputs

- [ ] Workflow completes all steps
- [ ] Dashboard data manifest generated
- [ ] GitHub Pages updated
- [ ] Manifest timestamp fresh
- [ ] Manifest data_status complete (or partial with documented reason)
- [ ] Workflow artifacts uploaded
- [ ] Accumulation report generated

## Documentation

- [ ] README.md updated with v1.2 positioning
- [ ] docs/model_limitations.md updated with v1.2 automation limitations
- [ ] docs/operations/automated_daily_deployment.md updated
- [ ] docs/release_checklist_v1_2.md created
- [ ] docs/reviews/phase_v12_m3_release_hardening.md created

## Guardrails

- [ ] Docs and dashboard text audited for market-action wording
- [ ] Generated data audited
- [ ] Dashboard remains display-only
- [ ] No frontend AI calls
- [ ] No frontend scoring logic

## Secret Hygiene

- [ ] No API keys in tracked files
- [ ] No GitHub tokens in tracked files
- [ ] No secrets in generated dashboard data
- [ ] No secrets in workflow YAML (only `${{ secrets.X }}` references)
- [ ] Exposed tokens rotated

## Repo Hygiene

- [ ] `.env` not staged
- [ ] API keys not staged
- [ ] GitHub tokens not staged
- [ ] `data/news_pilot/` not staged
- [ ] `outputs/` not staged
- [ ] `dashboard/public/data/` generated files not staged
- [ ] `dashboard/node_modules/` not staged
- [ ] `dashboard/dist/` not staged
- [ ] DuckDB files not staged
- [ ] `.claude/` not staged

## Release Decision

- [ ] Release-ready as `v1.2-rc1`
- [ ] Not release-ready
- [ ] Blocked pending issue

## Required Positioning

- [ ] v1.2 is not investment advice
- [ ] v1.2 is not a trading system
- [ ] v1.2 is not an allocation system
- [ ] v1.2 does not validate predictive performance
- [ ] v1.2 does not add scoring formula changes
- [ ] v1.2 does not add trading/allocation/recommendation logic
- [ ] GitHub Pages dashboard is static
- [ ] Public dashboard data must not contain secrets
