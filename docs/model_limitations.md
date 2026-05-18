# Model Limitations

This project is an experimental v0.4 macro, sector, and news diagnostic. It is designed
to be transparent and inspectable, not authoritative.

## Not Investment Advice

The engine does not provide investment advice, trading guidance, allocation
guidance, portfolio sizing, or security recommendations.

Macro regime output may inform future research workflows, but it should not be
used by itself to make financial decisions.

Sector macro scores are also diagnostics only. They describe how the configured
macro regime probabilities and macro dimensions map to sector tailwind/headwind
assumptions. They do not recommend buying, selling, avoiding, overweighting, or
underweighting any sector, ETF, stock, or portfolio.

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

## Sector Mapper

The v0.2 sector layer uses heuristic exposure and regime-prior assumptions for
the 11 GICS-style U.S. sectors. These values are transparent model assumptions,
not objective truths.

Sector proxy tickers such as XLE, XLF, XLK, and XLU are reporting and later
validation references only. They are not security recommendations.

The sector layer does not perform:

- security selection
- ETF recommendations
- sector allocation sizing
- trading rules
- portfolio construction
- return forecasting

Sector ETF proxy validation, when run, is a diagnostic sanity check only. It
compares stored sector scores with later sector ETF proxy returns relative to
SPY. It does not model transaction costs, slippage, execution constraints,
allocation sizing, or any implementable strategy.

The current v0.2 sector validation result is weak/mixed and should not be read
as empirical proof that the sector mapper is predictive. v0.2-M1 kept production
sector assumptions unchanged and treats the sector layer as experimental.

## AI News/Event Classification And Scoring

The v0.3 news layer uses AI-assisted classification for unstructured text. AI
outputs are probabilistic and interpretive. They may be wrong, incomplete,
overconfident, stale, or sensitive to prompt wording and source quality.

Provider behavior and model versions can change over time. A classification
from DeepSeek, OpenAI-compatible providers, or a future model may differ even
when the input text and prompt are similar.

The news layer is diagnostic only. It stores structured macro themes, sector
impacts, entities, severity, and confidence. News score aggregation is
deterministic after classification, but the inputs remain interpretive AI
outputs.

News classifications should be reviewed before relying on them. Source quality,
publication timing, duplicated headlines, missing context, and ambiguous wording
can materially affect classifications.

Known AI/news risks include:

- hallucinated or overly broad rationales
- malformed JSON or partially parsed responses
- prompt sensitivity
- model/provider variability
- source bias and incomplete news coverage
- duplicated or stale articles
- synthetic sample news that is useful for plumbing but not empirical validation

The AI layer must not provide investment advice, market action guidance,
execution guidance, portfolio instructions, or security instructions.

## Real-News Monitoring

The v0.4 monitoring layer checks input quality, classification quality, and
combined overlay stability. These checks help surface operational issues, but
they are not empirical validation that news scores have predictive value.

Known real-news pilot limitations include:

- RSS and search-query source bias
- uneven source coverage across macro and sector themes
- old RSS results contaminating current pilot files
- duplicated or near-duplicated stories
- short article snippets with limited context
- missing source URLs or incomplete metadata
- source groups that are manually assigned or absent

Classification repair and retry logic is intentionally conservative. It can
normalize obvious enum aliases and clamp small numeric drift, but it does not
invent missing required fields or hide severe schema failures. Repair and retry
rates should be monitored over time because high rates can indicate prompt,
provider, or source-quality deterioration.

Balanced, time-consistent real-news history is still needed before tuning news
score weights or judging whether the overlay adds durable diagnostic value.

## Combined Macro-Sector-News Diagnostic

The v0.3 combined diagnostic is an experimental overlay. It combines the v0.2
sector macro score with bounded sector news scores while preserving the original
macro-only sector score.

The combined layer does not:

- alter v0.1 macro regime scoring
- alter v0.2 sector macro scoring
- replace raw macro probabilities
- create portfolio weights
- create security selections
- model execution or implementation constraints

Combined diagnostic validation is limited until enough real classified news
history exists. Synthetic sample news can verify software behavior but cannot
validate empirical usefulness.

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

v0.4 is intended as a release-candidate real-news monitoring overlay for local
research and inspection. The macro v0.1 core remains the production macro
engine, the v0.2 sector layer remains an experimental deterministic sector
mapper, and the v0.3/v0.4 news layer remains an experimental interpretive
overlay.
