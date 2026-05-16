# v0.3-M1 AI News/Event Ingestion Foundation

Date: 2026-05-16

## Verdict

v0.3-M1 passes.

The project now has an additive AI-assisted news/event ingestion and classification foundation. It does not change v0.1 macro scoring, v0.2 sector scoring, FRED sources, regime formulas, sector assumptions, or report logic.

## Implemented

Added configs:

- `config/news_sources.yaml`
- `config/news_themes.yaml`
- `config/news_ai.yaml`

Added sample data:

- `data/examples/sample_news_items.csv`

Added news modules:

- `src/macro_engine/news/config.py`
- `src/macro_engine/news/ingest.py`
- `src/macro_engine/news/classify.py`
- `src/macro_engine/news/schema.py`
- `src/macro_engine/news/service.py`
- `src/macro_engine/news/report.py`
- `src/macro_engine/news/providers/base.py`
- `src/macro_engine/news/providers/local_csv.py`
- `src/macro_engine/news/providers/local_json.py`
- `src/macro_engine/news/providers/manual_text.py`
- `src/macro_engine/news/providers/openai_classifier.py`

Added storage tables:

- `news_items`
- `news_classifications`
- `news_theme_scores`
- `news_sector_impacts`

Added CLI commands:

- `python -m macro_engine.cli ingest-news --config config/news_sources.yaml`
- `python -m macro_engine.cli classify-news --config config/news_ai.yaml`
- `python -m macro_engine.cli inspect-news-item NEWS_ID`
- `python -m macro_engine.cli news-classification-summary`
- `python -m macro_engine.cli write-news-report --config config/news_ai.yaml`

Generated outputs:

- `outputs/news_classification_report.json`
- `outputs/news_classification_report.md`

## AI Provider

The configured live provider is DeepSeek using the OpenAI-compatible chat completions endpoint.

Default config:

- provider: `deepseek`
- model: `deepseek-v4-flash`
- base URL: `https://api.deepseek.com`
- JSON response mode: enabled in the provider request
- `enable_live_ai: false`
- `mock_mode: true`

Normal tests and default local runs do not require a live API key.

## Sample Mock Run

Command results:

- News rows ingested: 6
- Successful mock classifications: 6
- Theme score rows: 6
- Sector impact rows: 3

Mock classification summary:

- Top themes:
  - growth_slowdown: 3
  - monetary_tightening: 2
  - commodity_pressure: 1
- Top sectors:
  - real_estate: 2
  - energy: 1

The sample file is synthetic and exists for demos/tests only.

## Live DeepSeek Smoke Test

A single live DeepSeek smoke test was run in an isolated temporary DuckDB using a synthetic article. The live call succeeded.

Result:

- News rows: 1
- Successful classifications: 1
- Theme score rows: 2
- Sector impact rows: 3
- Themes identified:
  - inflation_pressure
  - monetary_tightening
- Sectors identified:
  - financials
  - real_estate
  - consumer_discretionary

The API key was not written to tracked files.

## Guardrails

The AI system prompt instructs the classifier to:

- classify the article/event only
- return valid JSON only
- use only allowed theme IDs
- use only allowed sector IDs
- include uncertainty when ambiguous
- avoid investment or market-action language

The generated news report was checked for forbidden recommendation language. No buy, sell, overweight, underweight, avoid, recommendation, trade, position sizing, or portfolio allocation wording appeared.

## Validation

- Tests: 134 passed, 2 skipped
- Ruff: passed
- Config validation: passed
- Local news ingestion: passed
- Mock AI classification: passed
- News report generation: passed
- Isolated live DeepSeek smoke test: passed

## Limitations

- AI classifications can be wrong, incomplete, or overconfident.
- Source quality and article context matter.
- v0.3-M1 does not aggregate news into time-series news scores.
- v0.3-M1 does not merge news into macro regime scoring.
- v0.3-M1 does not merge news into sector ranking.
- The sample data is synthetic.
- Live DeepSeek calls are disabled by default.

## Next Step

v0.3-M2 can proceed: news scoring and aggregation.

Recommended v0.3-M2 scope:

- aggregate classified themes by day/week
- aggregate sector impacts by day/week
- add freshness decay
- add source/relevance weighting
- produce `sector_news_scores`
- keep the output diagnostic-only

