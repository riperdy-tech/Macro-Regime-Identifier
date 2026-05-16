# v0.3-M2 News Scoring Aggregation Review

## Verdict

v0.3-M2 passes.

The news layer now converts stored item-level AI classifications into deterministic daily and weekly diagnostic scores. The implementation remains additive: it reads `news_items`, `news_classifications`, `news_theme_scores`, and `news_sector_impacts`, then writes derived score tables without changing macro regime scoring or sector macro scoring.

## Implemented

- Added `config/news_scoring.yaml`.
- Added daily and weekly macro theme score tables.
- Added daily and weekly sector news score tables.
- Added `news_score_components` for item-level traceability.
- Added `news_scoring_runs`.
- Added CLI commands:
  - `build-news-scores`
  - `current-news-summary`
  - `inspect-news-score`
  - `write-news-score-report`
- Added JSON and Markdown report output:
  - `outputs/news_score_report.json`
  - `outputs/news_score_report.md`

## Scoring Behavior

Theme components use:

```text
direction_sign * severity * confidence * source_weight * freshness_weight
```

Sector components use:

```text
impact_score * confidence * source_weight * freshness_weight
```

The scoring config applies conservative defaults:

- confidence weighting enabled
- severity weighting enabled
- half-life freshness decay enabled
- maximum single-item contribution cap
- maximum same-source daily contribution cap
- low-confidence and very-low-severity filtering

## Sample Local Output

Using the synthetic local sample news items:

```text
news rows: 6
classifications: 6
daily theme rows: 14
daily sector rows: 10
weekly theme rows: 6
weekly sector rows: 4
component rows: 33
latest score date: 2026-05-05
```

Latest score summary:

```text
Top positive themes:
- monetary_tightening: 0.523
- commodity_pressure: 0.323

Sector news tailwind:
- energy: 0.252

Sector news headwind:
- real_estate: -0.418
```

## Explainability

Every daily score can be traced through `news_score_components`, including:

- news item id
- theme or sector id
- direction
- raw component
- adjusted component
- severity
- confidence
- source weight
- freshness weight
- rationale for sector impacts

This preserves auditability from aggregate news score back to item-level classification.

## Guardrails

Generated reports use diagnostic language only. The report writer checks for prohibited market-action language before writing Markdown.

The generated report states that the output is a diagnostic news score overlay and is not investment advice or market action guidance.

## Validation

Commands run successfully during the milestone:

```text
python -m macro_engine.cli ingest-news --config config/news_sources.yaml
python -m macro_engine.cli classify-news --config config/news_ai.yaml
python -m macro_engine.cli build-news-scores --config config/news_scoring.yaml
python -m macro_engine.cli current-news-summary
python -m macro_engine.cli write-news-score-report --config config/news_scoring.yaml
```

Unit tests cover:

- config loading
- freshness decay
- confidence and severity weighting
- contribution caps
- daily aggregation
- weekly aggregation
- component storage
- CLI behavior
- report generation
- report language guardrails

## Limitations

- Current sample news is synthetic and small.
- News history is not yet deep enough for empirical validation.
- Scores are diagnostic overlays, not model truth.
- AI classification quality remains dependent on the quality and relevance of input text.

## M3 Readiness

M3 can proceed. The score tables provide the necessary sector-level news inputs for a bounded combined macro-sector plus news diagnostic.
