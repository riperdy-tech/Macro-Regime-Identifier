# v0.1 Release Checklist

Use this checklist before treating the engine as a v0.1 release candidate.

## Repository

- [ ] Production config is `config/phase_b_sources.yaml`.
- [ ] `.env` is present locally and not committed.
- [ ] `.env.example` documents required environment variables.
- [ ] Generated `outputs/` artifacts are ignored.
- [ ] Local `data/` DuckDB/Parquet artifacts are ignored.
- [ ] No generated cache files are staged.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml`

## Live Pipeline Review

- [ ] Pipeline succeeds or succeeds with explainable warnings.
- [ ] Latest valid regime date is current enough for the configured calendar.
- [ ] Invalid diagnostic dates are zero or explicitly explained.
- [ ] Source-health warnings are reviewed.
- [ ] Feature-health warnings are reviewed.
- [ ] Dimension-health warnings are reviewed.
- [ ] Regime-health warnings are reviewed.

## Report Review

- [ ] `outputs/current_regime.json` is valid JSON.
- [ ] `outputs/current_regime.md` is readable.
- [ ] `outputs/historical_diagnostic.json` is valid JSON.
- [ ] `outputs/historical_diagnostic.md` is readable.
- [ ] Current report shows raw monthly signal.
- [ ] Current report shows reported regime state.
- [ ] Current report shows transition filter reason.
- [ ] Current report includes source/data warnings.
- [ ] Historical report labels output as revised-data diagnostic.

## Model Guardrails

- [ ] No trading logic was added.
- [ ] No allocation logic was added.
- [ ] No portfolio sizing logic was added.
- [ ] No ALFRED/vintage backtesting is implied.
- [ ] Reports state that outputs are not investment advice.
- [ ] Reports state that outputs are not trading/allocation guidance.

## Production Source Set

- [ ] `ICSA` is enabled and usable.
- [ ] `BAMLH0A0HYM2` is enabled and usable.
- [ ] `RSAFS` is not in production config.
- [ ] `T5YIE` is not in production config.
- [ ] `USSLIND` remains disabled as a source-health test.

## Release Decision

- [ ] Known limitations are documented in `docs/model_limitations.md`.
- [ ] Phase review is written under `docs/reviews/`.
- [ ] Release tag is created.
- [ ] Any remaining issues are documented as follow-up work.
