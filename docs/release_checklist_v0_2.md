# v0.2 Release Checklist

Use this checklist for the sector macro mapper release candidate.

## Repository Hygiene

- [ ] `.env` is not staged.
- [ ] `data/` is not staged.
- [ ] `outputs/` is not staged.
- [ ] Generated experiment outputs are ignored.
- [ ] Production macro config remains `config/phase_b_sources.yaml`.
- [ ] Production sector configs remain:
  - `config/sectors.yaml`
  - `config/sector_exposures.yaml`
  - `config/sector_regime_priors.yaml`
  - `config/sector_validation.yaml`

## Validation Commands

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml`
- [ ] `python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml`

## Sector Mapper

- [ ] Sector taxonomy contains the 11 GICS-style U.S. sectors.
- [ ] Proxy tickers are documented as validation/reporting references only.
- [ ] Sector scores read stored macro outputs and do not call FRED.
- [ ] Sector scoring does not alter macro regime outputs.
- [ ] Sector score components are stored and inspectable.
- [ ] Current sector ranking report writes JSON and Markdown.
- [ ] Sector validation report writes JSON and Markdown when local price data is available.

## Validation Data

- [ ] If local ETF proxy validation is run, `data/sector_proxy_prices.csv` exists.
- [ ] Local CSV schema is `ticker,date,close`.
- [ ] Required tickers are present:
  - SPY
  - XLC
  - XLY
  - XLP
  - XLE
  - XLF
  - XLV
  - XLI
  - XLK
  - XLB
  - XLRE
  - XLU
- [ ] Validation report clearly states it is diagnostic only.

## Language Guardrails

- [ ] Reports do not use buy/sell language.
- [ ] Reports do not use overweight/underweight language.
- [ ] Reports do not use avoid language.
- [ ] Reports do not provide allocation sizing.
- [ ] Reports do not provide trading guidance.
- [ ] Reports state sector scores are diagnostics only.
- [ ] Reports state ETF proxy validation is not a trading backtest.

## Known Limitations

- [ ] Sector assumptions are documented as heuristic.
- [ ] v0.2-M1 weak/mixed validation result is documented.
- [ ] Sector mapper is labeled experimental unless future validation improves.
- [ ] No transaction costs, slippage, execution constraints, or portfolio construction are modeled.
- [ ] No ALFRED/vintage backtesting is implemented.
- [ ] No news ingestion is implemented.

## Release Decision

- [ ] Release blockers are documented.
- [ ] Non-blocking follow-ups are documented.
- [ ] If no blockers, tag `v0.2-rc1`.

