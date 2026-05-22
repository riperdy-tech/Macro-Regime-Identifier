# Model Limitations

This project is a local-first macro, sector, news, operations, replay, and
dashboard diagnostic platform. It is designed to be transparent and inspectable,
not authoritative.

## Not Investment Advice

The engine does not provide investment advice, trading guidance, allocation
guidance, portfolio sizing, or security advice.

Macro regime output may inform future research workflows, but it should not be
used by itself to make financial decisions.

Sector macro scores are also diagnostics only. They describe how the configured
macro regime probabilities and macro dimensions map to sector tailwind/headwind
assumptions. They do not provide instructions for market action or sector sizing, and they do not
tell users which sector, ETF, stock, or portfolio to prefer.

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
validation references only. They are not security advice.

The sector layer does not perform:

- security selection
- ETF advice
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

## Daily Operations And Accumulation

The v0.5 daily pipeline is an operating workflow, not a model upgrade. It runs
existing macro, sector, news, combined, and monitoring steps, archives generated
diagnostic artifacts, and records run status.

Daily archives are local generated artifacts. They are intended for audit,
inspection, and continuity between runs, but they are not immutable publication
records and should not be treated as model validation evidence by themselves.

The accumulation tracker helps determine whether enough classified news history
exists for later validation. Readiness labels such as `insufficient_history`,
`early_history`, `monitor_ready`, and `validation_candidate` are operational
coverage labels. They are not claims that the news overlay is predictive.

Daily archives and accumulation summaries can still reflect source imbalance,
thin news, mock-mode examples, old RSS items, duplicated headlines, or provider
classification drift. These operational records should be reviewed before using
them to make calibration decisions.

Repeated real-news collection is required before the accumulation layer can
support validation or calibration decisions. A successful mock daily run only
shows that the workflow, guardrail audit, archiving, and summaries are working.

The v0.6 source coverage layer reports configured and observed news breadth by
source group. It can identify missing, stale, thin, or concentrated groups, but
it does not solve source bias by itself. A balanced watchlist still depends on
the user's local files, RSS feeds, and operating discipline.

Source group mapping is operational metadata, not a model signal. Explicit
`source_group` fields are preferred. `query_group` and configured mapping rules
can reduce unmapped local/RSS pilot items, but those mappings are assumptions
that require review. A low unmapped percentage does not mean the news set is
balanced, current, or suitable for validation.

Old RSS items can still enter local pilot files when a search/feed returns stale
results. The coverage and monitoring reports flag old-item share, stale groups,
and missing groups, but users must curate or replace the input data to improve
coverage quality.

Scheduled-run scripts make repeated operation easier, but they do not make the
system autonomous. Local environment setup, API key handling, Windows Task
Scheduler or cron configuration, and log review remain user-operated.

Live AI classification is now intentionally bounded for daily operation. The
daily pipeline classifies a configured maximum number of unclassified items per
run and writes completed item classifications incrementally. This improves
observability and resumability, but it also means large backlogs require
multiple runs.

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

## Dashboard Limitations

The v0.7 dashboard is a read-only display layer. It reads generated JSON files
from the backend and renders them for local review. It does not run FRED
ingestion, classify news, call AI providers, calculate scores, or write model
state.

Dashboard data can be stale. Users must run the backend workflow and
`export-dashboard-data` before expecting the UI to reflect the latest generated
outputs.

The dashboard does not validate model performance. It can make operational
state easier to inspect, but it does not change the evidence requirements for
real validation, source balance, or accumulated-history review.

Dashboard sample data may be synthetic. It is useful for interface development
and missing-data checks, but it is not evidence that any diagnostic signal is
useful.

Dashboard quality is bounded by backend output quality. If backend reports are
missing, stale, incomplete, or based on thin source coverage, the dashboard will
surface that state but cannot correct it.

The v0.8 history view is a convenience layer over archived daily summaries. It
does not calculate new signals, does not prove predictive usefulness, and should
not be interpreted as validation. Short history windows should be treated as
operating context only.

Archived daily summaries may be incomplete across versions because fields have
been added over time. The dashboard should surface missing fields plainly rather
than backfilling or inventing them.

## Historical Replay Limitations

The v0.9 historical news replay is an operating replay. It groups local news
items by `published_at`, runs the daily workflow for replay dates, archives each
replay date separately, and lets the dashboard History tab display replay runs.
This checks workflow behavior, date filtering, archive handling, dashboard
history, and guardrails.

Replay is not predictive validation. It is not a trading backtest, and it does
not show whether any score would have forecast later market behavior.

The replay workflow does not make macro data vintage. Unless a separate
vintage-data backend is added, macro inputs may still reflect currently
available or revised data. Replay dates therefore should not be interpreted as
true point-in-time macro states.

Mock replay mode does not test live AI provider behavior. It is useful for
software reliability and release checks, but live provider outputs can differ in
classification content, schema drift, latency, retry behavior, and cost.

RSS-derived replay files can be query-selected and biased. A mapped CSV can
cover all configured source groups while still overrepresenting certain themes,
publishers, regions, or article types. Balanced source coverage requires ongoing
curation and monitoring.

The replay command intentionally supports per-day item caps. These caps keep
runs bounded and auditable, but they also mean a replay may sample only part of
a larger local news file for each replay date.

## v1.0 Diagnostic Software Boundary

The `v1.0-rc1` release is a software and operating-workflow milestone. It
packages the macro engine, sector diagnostics, AI-assisted news classification,
news aggregation, combined diagnostics, monitoring, daily operation, historical
operating replay, and read-only dashboard into one local-first diagnostic
platform.

That release status does not mean the system has proven predictive value. It
means the commands, reports, archive flow, replay flow, dashboard export, and
dashboard build have passed release validation as software.

The dashboard remains display-only. It can make generated backend outputs easier
to inspect, but it does not calculate scores, call AI providers, classify news,
or validate model performance.

The project still requires repeated balanced real-news collection before
credible validation or calibration work can begin. Current readiness labels such
as `insufficient_history` should be treated literally.

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

v1.0 is intended as a local-first diagnostic platform for research and
inspection. The macro v0.1 core is the stable deterministic macro engine, the
v0.2 sector layer remains an experimental deterministic sector mapper, the
v0.3/v0.4 news layer remains an experimental interpretive overlay, and the
v0.5-v0.9 operating, source coverage, dashboard, and replay layers are release
ready as local software workflows.
