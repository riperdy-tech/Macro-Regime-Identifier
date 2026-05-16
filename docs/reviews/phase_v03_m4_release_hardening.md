# v0.3-M4 Release Hardening Review

## Verdict

v0.3 is release-ready as an experimental AI-assisted news diagnostic overlay.

Release blockers: none.

Release decision: tag `v0.3-rc1`.

## What v0.3 Adds

v0.3 adds three additive layers on top of the deterministic v0.1/v0.2 core:

1. AI-assisted news/event ingestion and classification.
2. Deterministic aggregation of stored classifications into news theme and sector scores.
3. Experimental combined sector diagnostics that blend v0.2 sector macro scores with bounded sector news scores.

The AI layer interprets unstructured text into structured signals. Scoring and aggregation remain deterministic, table-backed, and component-auditable.

## What v0.3 Does Not Do

v0.3 does not:

- change v0.1 macro regime scoring
- change v0.2 sector macro scoring
- add security selection
- add execution logic
- add portfolio construction
- add allocation sizing
- implement ALFRED/vintage backtesting
- claim empirical validation of the combined news overlay

v0.3 is not a trading system, not an allocation system, and not a security recommendation system.

## Validation Results

Validation commands run:

```text
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml
python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml
python -m macro_engine.cli ingest-news --config config/news_sources.yaml
python -m macro_engine.cli classify-news --config config/news_ai.yaml
python -m macro_engine.cli build-news-scores --config config/news_scoring.yaml
python -m macro_engine.cli write-news-score-report --config config/news_scoring.yaml
python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml
python -m macro_engine.cli write-combined-sector-report --config config/sector_news_integration.yaml
```

Results:

```text
pytest: 139 passed, 2 skipped
ruff: clean
validate-config: passed; 13 sources, 11 dimensions, 6 regimes
macro pipeline: success_with_warnings
sector report: generated
news classification/report workflow: generated with mock mode
news score report: generated
combined sector report: generated
```

Macro pipeline warnings:

```text
warning_count: 2
stale_series: PCEPI
series_requested: 12
series_succeeded: 12
```

## Current Macro Output

Latest valid regime date:

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

Confidence:

```text
0.1547
```

Raw probabilities:

```text
reflation: 0.3949
tightening: 0.2403
stagflation: 0.1842
goldilocks: 0.1241
recession: 0.0565
```

## Current v0.2 Sector Ranking

Latest sector date:

```text
2026-05-01
```

Top sectors:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

Bottom sectors:

```text
9. information_technology
10. real_estate
11. utilities
```

## Current News Score Summary

Latest news score date:

```text
2026-05-05
```

Top positive macro themes:

```text
monetary_tightening: 0.5226
commodity_pressure: 0.3230
```

Sector news tailwind:

```text
energy: 0.2524
```

Sector news headwind:

```text
real_estate: -0.4181
```

The release validation used mock/synthetic news mode. Live AI was not required for release validation.

## Current Combined Sector Diagnostic

Latest combined diagnostic date:

```text
2026-05-05
```

Top combined sectors:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

News overlay behavior:

```text
energy: positive bounded news overlay from synthetic energy-related event
real_estate: negative bounded news overlay from synthetic rate-sensitive event
other sectors: macro-only fallback because recent sector news coverage is thin
```

Combined validation remains blocked by limited/synthetic news history. The software path works, but empirical usefulness cannot be judged until real classified news history exists.

## Report Guardrail Audit

Generated Markdown reports were audited for market-action language:

```text
outputs/*.md
```

The only match was in `outputs/sector_validation.md`, inside the diagnostic disclaimer stating that sector ETF proxy validation is not a trading backtest and not a security recommendation. This is allowed limitation language, not report-body market-action guidance.

The new v0.3 reports generated during this milestone passed the guardrail audit.

## Repository Hygiene

`git status --short --ignored` showed only tracked documentation edits and ignored local artifacts before staging:

```text
M README.md
M docs/model_limitations.md
?? docs/release_checklist_v0_3.md
!! .env
!! data/macro_engine.duckdb
!! data/raw/
!! data/sector_proxy_prices.csv
!! outputs/
!! cache and __pycache__ paths
```

`.env`, `data/`, `outputs/`, local DuckDB files, caches, and generated artifacts remain ignored and were not staged.

## Known Limitations

- v0.3 uses synthetic example news for release validation.
- Live AI was not required for release validation.
- AI classifications can be wrong, incomplete, or prompt-sensitive.
- Provider/model behavior can vary over time.
- Combined diagnostic validation is limited until there is enough real classified news history.
- The combined layer is an experimental overlay, not an empirically validated decision model.
- Revised-data macro diagnostics remain distinct from point-in-time vintage testing.

## Non-Blocking Follow-Ups

- v0.4-M1: real news data pilot.
- Collect real news over time.
- Compare news-only and combined diagnostics against later sector-relative moves.
- Evaluate whether the news overlay improves diagnostics after real history accumulates.
- Consider live AI smoke tests only with explicit local key handling.

## Release Decision

v0.3 is ready for release candidate tagging as:

```text
v0.3-rc1
```

This release is explicitly experimental and diagnostic-only.
