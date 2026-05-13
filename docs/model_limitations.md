# Model Limitations

This project is an experimental v0.1 macro regime diagnostic. It is designed to
be transparent and inspectable, not authoritative.

## Not Investment Advice

The engine does not provide investment advice, trading guidance, allocation
guidance, portfolio sizing, or security recommendations.

Macro regime output may inform future research workflows, but it should not be
used by itself to make financial decisions.

## Revised-Data Diagnostic

Historical diagnostics use revised FRED data.

That means historical observations may include revisions that were not available
on the historical evaluation date. The historical timeline is therefore a
revised-data diagnostic, not a point-in-time backtest.

A true point-in-time historical test would require vintage data, such as ALFRED,
and explicit release-availability logic.

## Source Coverage

The v0.1 source set is intentionally small and U.S.-focused. It covers growth,
inflation, policy, credit/liquidity, and yield-curve conditions, but it does not
cover the full global macro landscape.

Known omitted or deferred areas include:

- fiscal impulse
- global growth
- non-U.S. inflation
- dollar/liquidity channels
- commodity supply shocks
- detailed housing cycle data
- survey breadth beyond selected FRED-accessible series

## Data Freshness And Revisions

FRED series have different frequencies, release lags, revision policies, and
occasional availability issues. The engine has source-health checks and
calendar-as-of alignment, but it does not yet model real publication-time
availability.

Transient FRED API errors can occur. The local ingestion layer is idempotent, so
rerunning the pipeline can fill missing series when the API recovers.

## Formula Design

Regime scoring uses transparent formula weights and asymmetric polarity rules.
This is intentional for auditability, but it is still a simplified model.

The model is not trained, optimized, or validated as a predictive trading model.
Confidence should be read as probability separation inside the configured model,
not as a statistical guarantee.

## Raw Signal Vs Reported State

The engine preserves raw monthly regime probabilities and separately reports a
transition-filtered regime state.

This means the raw monthly leader can differ from the reported regime when the
raw probability gap is too small to justify a reported transition. That is
expected behavior near regime boundaries.

## Backtesting

The v0.1 engine does not implement:

- ALFRED/vintage backtests
- trading backtests
- portfolio simulations
- performance attribution
- transaction costs
- slippage
- execution constraints

Historical diagnostics should be used for model sanity review, not performance
claims.

## Current Release Intent

v0.1 is a release-candidate model core for local research and inspection. The
next appropriate work is release hardening, documentation review, and careful
operator testing before any further model expansion.
