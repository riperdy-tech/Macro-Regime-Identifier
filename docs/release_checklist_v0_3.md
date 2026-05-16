# v0.3 Release Checklist

Use this checklist for the AI news/event classification, news score aggregation,
and experimental combined sector diagnostic release candidate.

## Repository Hygiene

- [ ] `.env` is not staged.
- [ ] API keys are not written into tracked files.
- [ ] `data/` is not staged except intentional example files.
- [ ] `outputs/` is not staged.
- [ ] Generated experiment outputs are ignored.
- [ ] Local DuckDB files are not staged.
- [ ] Cache files are not staged.

## Validation Commands

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli ingest-news --config config/news_sources.yaml`
- [ ] `python -m macro_engine.cli classify-news --config config/news_ai.yaml`
- [ ] `python -m macro_engine.cli build-news-scores --config config/news_scoring.yaml`
- [ ] `python -m macro_engine.cli write-news-score-report --config config/news_scoring.yaml`
- [ ] `python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml`
- [ ] `python -m macro_engine.cli write-combined-sector-report --config config/sector_news_integration.yaml`

## Configuration

- [ ] Production macro config remains `config/phase_b_sources.yaml`.
- [ ] Production sector configs remain unchanged unless a later release explicitly promotes changes.
- [ ] News source config exists: `config/news_sources.yaml`.
- [ ] News theme config exists: `config/news_themes.yaml`.
- [ ] News AI config exists: `config/news_ai.yaml`.
- [ ] News scoring config exists: `config/news_scoring.yaml`.
- [ ] Combined diagnostic config exists: `config/sector_news_integration.yaml`.
- [ ] Live AI is disabled by default.
- [ ] Tests use mock AI behavior.

## News Layer

- [ ] Local sample news ingestion works.
- [ ] Mock classification writes `news_classifications`.
- [ ] Theme rows are stored in `news_theme_scores`.
- [ ] Sector impact rows are stored in `news_sector_impacts`.
- [ ] News score aggregation writes daily and weekly theme scores.
- [ ] News score aggregation writes daily and weekly sector scores.
- [ ] `news_score_components` makes scores traceable.
- [ ] News scoring runs are recorded.

## Combined Diagnostic

- [ ] v0.2 `sector_scores` are not mutated.
- [ ] Combined diagnostics are stored separately.
- [ ] News overlay is bounded.
- [ ] Missing or thin news falls back to macro-only behavior.
- [ ] Component rows explain macro, news, and uncertainty pieces.
- [ ] Combined validation limitation is documented when real news history is insufficient.

## Reports

- [ ] `outputs/news_classification_report.json/.md` writes.
- [ ] `outputs/news_score_report.json/.md` writes.
- [ ] `outputs/combined_sector_diagnostic.json/.md` writes.
- [ ] Sector report still writes.
- [ ] Reports state diagnostic-only limitations.
- [ ] Reports do not imply market action.

## Language Guardrails

- [ ] Reports do not use buy/sell language.
- [ ] Reports do not use overweight/underweight language.
- [ ] Reports do not use avoid language.
- [ ] Reports do not use recommendation language.
- [ ] Reports do not provide position sizing.
- [ ] Reports do not provide portfolio allocation.
- [ ] Reports do not provide execution or trading instructions.

## Known Limitations

- [ ] AI classification limitations are documented.
- [ ] Model/provider variability is documented.
- [ ] Prompt sensitivity is documented.
- [ ] JSON parsing and malformed-response risk is documented.
- [ ] Source bias and incomplete coverage are documented.
- [ ] Synthetic news limitation is documented.
- [ ] Combined diagnostic is labeled experimental.
- [ ] Combined validation is blocked until real classified news history exists.

## Release Decision

- [ ] Release blockers are documented.
- [ ] Non-blocking follow-ups are documented.
- [ ] If no blockers, tag `v0.3-rc1`.
