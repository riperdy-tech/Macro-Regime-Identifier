# v0.3-M3 Combined Sector News Diagnostic Review

## Verdict

v0.3-M3 passes as an experimental combined diagnostic layer.

The implementation keeps the v0.2 sector macro score intact and creates a separate combined output that blends normalized sector macro scores with bounded sector news scores. It does not replace the macro sector mapper and does not mutate stored `sector_scores`.

## Implemented

- Added `config/sector_news_integration.yaml`.
- Added `combined_sector_diagnostics`.
- Added `combined_sector_diagnostic_components`.
- Added CLI commands:
  - `build-combined-sector-diagnostics`
  - `current-combined-sector-ranking`
  - `inspect-combined-sector`
  - `write-combined-sector-report`
- Added JSON and Markdown report output:
  - `outputs/combined_sector_diagnostic.json`
  - `outputs/combined_sector_diagnostic.md`

## Combination Logic

The combined layer uses:

```text
combined_score =
  macro_weight * normalized_sector_macro_score
+ news_weight * bounded_sector_news_score
- uncertainty_penalty
```

Defaults:

```text
macro sector weight: 0.75
news sector weight: 0.25
max news adjustment: 0.50
```

If a sector has no recent news or insufficient item coverage, the combined diagnostic falls back to macro-only behavior for that sector. Missing news therefore does not create a false signal.

## Sample Local Output

Using existing sector macro scores plus synthetic news scores:

```text
diagnostic date: 2026-05-05
combined rows: 55
component rows: 165
```

Latest combined ranking:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

News overlay effects:

```text
energy: positive news overlay from synthetic energy supply event
real_estate: negative news overlay from synthetic restrictive-rate event
other sectors: macro-only because recent sector news coverage is thin
```

## Raw Components Preserved

Each combined row stores:

- normalized sector macro score
- bounded sector news score
- effective macro weight
- effective news weight
- news item count
- news confidence
- diagnostic confidence

Component rows explain the contribution of:

- normalized sector macro score
- bounded sector news score
- news uncertainty penalty

## Validation Status

Combined historical validation is blocked by limited synthetic news history. The current sample is useful for verifying plumbing and report behavior, but it is not enough to judge whether the combined overlay improves sector proxy validation.

The report states this limitation clearly.

## Guardrails

The combined diagnostic report uses diagnostic language only. It does not include market-action language or allocation guidance. The generated report is checked for prohibited terms before it is written.

## Validation

Commands run successfully during the milestone:

```text
python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml
python -m macro_engine.cli current-combined-sector-ranking
python -m macro_engine.cli write-combined-sector-report --config config/sector_news_integration.yaml
```

Unit tests cover:

- integration config loading
- macro score normalization
- news score bounding
- missing-news behavior
- low-coverage news fallback
- combined score calculation
- component storage
- CLI behavior
- report generation
- language guardrails
- no mutation of v0.2 `sector_scores`

## Release-Hardening Readiness

v0.3 can proceed to release hardening after full validation passes. The main known limitation is empirical: the combined layer needs more real classified news history before validation can be meaningful.
