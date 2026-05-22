# v1.0-M2 Final Release Hardening Review

Verdict: pass.

Release decision: release-ready as `v1.0-rc1`.

## v1.0 Release Definition

`v1.0-rc1` is a local-first macro, sector, news, operations, replay, and
dashboard diagnostic platform.

It includes:

- deterministic macro regime diagnostics;
- deterministic sector macro diagnostics;
- AI-assisted news/event classification;
- deterministic news score aggregation;
- bounded combined macro-sector-news diagnostics;
- daily operating workflow;
- source coverage and monitoring reports;
- historical operating replay;
- read-only dashboard;
- GitHub Pages deployment support.

## What v1.0 Does Not Include

v1.0 does not:

- execute orders;
- size portfolios;
- choose securities;
- call AI from the frontend;
- calculate scores in the frontend;
- use vintage macro data;
- validate predictive performance;
- replace human review.

This is not investment advice.

This is not a trading system.

This is not an allocation system.

This is not a performance-validated model.

Historical replay is not a predictive backtest.

Macro data is not vintage unless separately supported.

The dashboard is display-only.

## Full Validation Results

Commands run:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm install
npm run build
```

Results:

```text
pytest: 171 passed, 2 skipped
ruff: passed
validate-config: passed
daily diagnostic: success
dashboard export: passed
npm install: passed
dashboard build: passed
```

Config validation:

```text
13 sources, 11 dimensions, 6 regimes
```

Local dashboard smoke check:

```text
title: Macro Diagnostic Dashboard
Overview: visible
Macro: visible
Sectors: visible
News: visible
Combined: visible
Monitoring: visible
History: visible
browser console errors: none observed
```

GitHub Pages check:

```text
url: https://riperdy-tech.github.io/Macro-Regime-Identifier/
status: 200
title: Macro Diagnostic Dashboard
```

## Current Macro Output

Latest macro date:

```text
2026-05-01
```

Reported regime:

```text
reflation
```

Raw dominant regime:

```text
reflation
```

Reported confidence:

```text
6.60%
```

Regime probabilities:

| Regime | Probability |
|---|---:|
| reflation | 35.21% |
| tightening | 28.61% |
| stagflation | 19.35% |
| goldilocks | 11.06% |
| recession | 5.77% |

Interpretation: the current regime output is valid but low-confidence.

## Current Sector Output

Latest sector macro date:

```text
2026-05-01
```

Top sector macro diagnostics:

| Rank | Sector | Confidence-adjusted score |
|---:|---|---:|
| 1 | energy | 0.519 |
| 2 | materials | 0.325 |
| 3 | industrials | 0.158 |
| 4 | consumer_staples | 0.009 |
| 5 | financials | -0.010 |

Sector diagnostics remain experimental because the sector exposures and priors
are heuristic and prior ETF proxy validation was weak/mixed.

## Current News And Combined Output

Latest news score date:

```text
2026-05-16
```

Top positive macro news themes:

| Theme | Score | Item count |
|---|---:|---:|
| monetary_tightening | 6.517 | 26 |
| commodity_pressure | 2.288 | 9 |

Top sector news tailwind:

| Sector | Score | Item count |
|---|---:|---:|
| energy | 1.965 | 9 |

Top sector news headwind:

| Sector | Score | Item count |
|---|---:|---:|
| real_estate | -5.214 | 26 |

Latest combined sector diagnostics:

| Rank | Sector | Combined score | News item count |
|---:|---|---:|---:|
| 1 | energy | 1.767 | 9 |
| 2 | materials | 1.383 | 0 |
| 3 | industrials | 0.669 | 0 |
| 4 | consumer_staples | 0.038 | 0 |
| 5 | financials | -0.043 | 0 |

Latest max overlay rank change:

```text
0
```

Interpretation: the news overlay remained bounded in the latest exported state.

## Dashboard Status

Dashboard export:

```text
data_status: complete
missing_files: none
latest_run_date: 2026-05-22
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-16
```

History index:

```text
history rows: 83
latest run_id: 20260522T105548Z-6cf6029f
latest run mode: daily
latest run status: success
latest readiness label: insufficient_history
```

The dashboard builds locally and remains a read-only display layer over exported
backend JSON.

## Replay Status

v0.9 30-day operating replay:

```text
status: success
date range: 2026-04-22 to 2026-05-21
replay days: 30
source file rows: 144
selected replay items: 295
successful classifications: 295
failed classifications: 0
failed daily replay runs: 0
classification mode: mock
```

Days with no raw same-day news:

```text
2026-04-25
2026-05-03
2026-05-10
```

Replay is an operating workflow check. It is not predictive validation.

## Accumulation Readiness

Latest accumulation summary:

```text
quality_status: ok
classified_items: 141
failed_items: 0
success_rate: 100%
source_count: 84
source_group_count: 10
readiness_label: insufficient_history
```

Latest source coverage after the release mock run:

```text
valid: true
source_group_count: 1
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 0
old_item_pct: 0.0%
```

Warnings:

```text
some enabled source groups have no stored items
latest stored items are concentrated in a small number of source groups
stored items cover fewer source groups than configured minimum
```

Interpretation: the software is release-ready as a diagnostic platform, but
real-news history and source coverage are still insufficient for predictive
validation.

## Guardrail Audit

Scanned:

- touched docs;
- dashboard source/static text;
- dashboard sample fixtures;
- generated Markdown reports.

Result:

```text
passed
```

Market-action wording appears only in limitation/disclaimer context and is not
used as system output or instruction.

## Repo Hygiene

Confirmed not staged:

- `.env`
- API keys
- `data/news_pilot/`
- `outputs/`
- `outputs/archive/`
- `outputs/replay/`
- generated dashboard data under `dashboard/public/data/`
- `dashboard/node_modules/`
- `dashboard/dist/`
- logs
- DuckDB files
- caches
- `.claude/`

Known local-only untracked paths:

```text
.claude/
outputs/
```

They remain unstaged.

## Version Marker

Updated package metadata:

```text
pyproject.toml: 1.0rc1
src/macro_engine/__init__.py: v1.0-rc1
```

`pyproject.toml` uses PEP 440-compatible version syntax. The project tag remains
`v1.0-rc1`.

## Known Limitations

- Macro history uses revised FRED data, not vintage release-time data.
- Sector diagnostics are heuristic and experimental.
- AI news classifications can be wrong or provider-sensitive.
- Source coverage remains incomplete and can be concentrated.
- Mock daily and replay runs validate workflow behavior, not live AI quality.
- Historical replay is not predictive validation.
- Accumulated real-news history remains insufficient.
- Dashboard output can be stale if backend export is stale.

## Release Blockers

None for `v1.0-rc1` as diagnostic software.

## Non-Blocking Follow-Ups

- Continue real daily operation.
- Improve balanced real-news source coverage.
- Accumulate enough real daily history before validation planning.
- Run additional bounded live AI operating trials.
- Keep dashboard usability improvements separate from scoring changes.

## Final Decision

Proceed with `v1.0-rc1`.

The project is release-ready as a local-first diagnostic platform. It is not
release-ready as a performance-validated forecasting, trading, allocation, or
security-selection system.
