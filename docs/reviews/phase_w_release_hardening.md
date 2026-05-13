# Phase W Release Hardening Review

Date reviewed: 2026-05-14

Objective: prepare the project as a v0.1 release candidate through
documentation, reproducibility notes, operational guardrails, and final
validation. No model logic, source universe, trading logic, allocation logic, or
ALFRED/vintage backtesting was added.

## Verdict

Phase W passes.

The project is ready for a v0.1 release-candidate review.

## Documentation Added Or Updated

Updated:

- `README.md`
- `.env.example`

Added:

- `docs/model_limitations.md`
- `docs/release_checklist_v0_1.md`

The README now explains:

- project purpose
- install and `.env` setup
- command examples
- production source list
- pipeline stages
- raw monthly signal vs reported regime state
- revised-data diagnostic disclaimer
- generated-output policy
- troubleshooting
- known limitations

## Generated Output Policy

Generated artifacts remain ignored:

- `outputs/*.json`
- `outputs/*.md`
- `outputs/**/*.json`
- `outputs/**/*.md`
- `outputs/**/*.yaml`
- local DuckDB files
- Parquet exports
- `.env`

`.env.example` is safe to commit and contains no secret value.

## Validation Results

Commands run:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
```

Results:

- Tests: 103 passed, 2 skipped
- Ruff: all checks passed
- Config validation: valid
- Live pipeline: `success_with_warnings`

## Live Pipeline Result

Latest live pipeline summary:

- Config: `config/phase_b_sources.yaml`
- Mode: `live`
- Series requested: 12
- Series succeeded in latest request: 6
- Series failed in latest request: 6
- Status: `success_with_warnings`
- Output directory: `outputs`

The latest FRED request returned transient HTTP 500 errors for several series.
Because ingestion is idempotent and previous observations were already stored
locally, the pipeline still produced valid current outputs. This is an
operational warning, not a model failure.

Latest source-health state:

| Series | Last observation | Usable | Reason |
|---|---:|---|---|
| `BAA10Y` | 2026-05-11 | true | fresh |
| `BAMLH0A0HYM2` | 2026-05-12 | true | fresh |
| `CPIAUCSL` | 2026-04-01 | true | fresh |
| `DGS10` | 2026-05-11 | true | fresh |
| `FEDFUNDS` | 2026-04-01 | true | fresh |
| `ICSA` | 2026-05-02 | true | fresh |
| `INDPRO` | 2026-03-01 | true | stale |
| `NFCI` | 2026-05-08 | true | fresh |
| `PAYEMS` | 2026-04-01 | true | fresh |
| `PCEPI` | 2026-03-01 | true | stale |
| `T10Y2Y` | 2026-05-12 | true | fresh |
| `UNRATE` | 2026-04-01 | true | fresh |
| `USSLIND` | n/a | false | discontinued_or_stale |

## Current Regime Output

Latest valid date: 2026-05-01

Raw monthly signal:

| Regime | Probability |
|---|---:|
| reflation | 29.79% |
| stagflation | 28.76% |
| tightening | 22.90% |
| goldilocks | 9.36% |
| recession | 9.19% |

Raw dominant regime: `reflation`

Raw confidence: 1.03%

Reported regime state:

- Reported regime: `stagflation`
- Reported probability: 28.76%
- Reported confidence: -1.03%
- Transition filter reason: `held_below_min_confidence`

Interpretation:

The current model state is near a regime boundary. Raw `reflation` leads, but
the probability gap is below the 2% reported-transition threshold, so the
reported regime remains `stagflation`. This is expected behavior and should be
described as low-confidence boundary behavior.

## Historical Diagnostic

Mode: `revised_data`

| Metric | Value |
|---|---:|
| Valid dates | 437 |
| Invalid dates | 0 |
| Reported transition count | 43 |
| Near-zero reported transitions | 0 |
| Low-confidence reported transitions | 8 |
| Average regime duration | 9.93 months |
| Average confidence | 19.17% |
| Low-confidence periods | 86 |

The historical diagnostic remains explicitly labeled as revised-data output,
not a point-in-time vintage backtest.

## Output Files Generated

```text
outputs/current_regime.json
outputs/current_regime.md
outputs/historical_diagnostic.json
outputs/historical_diagnostic.md
```

Reports continue to state that outputs are not investment advice and do not
provide trading, allocation, or portfolio guidance.

## Known Limitations

Documented in `docs/model_limitations.md`.

Key limitations:

- Uses revised FRED data.
- Not an ALFRED/vintage point-in-time backtest.
- No trading, allocation, portfolio sizing, or investment recommendations.
- Small U.S.-focused source universe.
- Transparent formula model, not ML.
- FRED API availability can affect latest ingestion runs.
- Current raw and reported regimes can diverge near boundaries.

## Release Candidate Decision

v0.1 release candidate is ready for final review.

Recommended next step:

Phase X: v0.1 release candidate review.

That phase should be review-only unless it finds a clear documentation or
operational bug.
