# Version 0.1 Technical Spec: Structured Macro Regime Engine

## 1. Objective

Build a standalone, local-first Python engine that identifies U.S. macroeconomic regimes from credible structured data and market-implied signals.

Version 0.1 should not attempt to be a full real-time news intelligence platform. Its purpose is to establish the economic foundation: ingest structured macro and market data, transform it into interpretable macro dimensions, classify the current regime, explain the drivers, and support historical evaluation.

The v0.1 engine should answer:

1. What macro regime are we currently in?
2. Which dimensions are driving that regime?
3. How confident is the classification?
4. What changed recently?
5. How would the same logic have behaved historically?

---

## 2. Product Positioning

### Working name

**Macro Regime Intelligence Engine**

### v0.1 positioning

A local-first U.S. macro regime classifier based on structured economic and market-implied data.

### Avoid saying in v0.1

Do not describe the system as:

- a trading signal
- a real-time news analyzer
- an AI economist
- an investment recommendation engine
- a fully validated predictive model

### Preferred wording

Use language such as:

> Experimental macro regime classification based on official macroeconomic data, market-implied signals, and transparent scoring rules.

---

## 3. Scope

### In scope for v0.1

- U.S. macro only
- Local-first Python project
- Historical and latest-data ingestion
- FRED-first data ingestion
- DuckDB and Parquet local storage
- Structured macro dimension scoring
- Market-implied signal scoring
- Explainable rule-based regime classifier
- Confidence score
- CLI output
- JSON output
- Historical backtest/evaluation notebook
- Unit tests for scoring and regime rules

### Out of scope for v0.1

- Reddit
- X/Twitter
- GDELT
- FinBERT
- BERTopic
- SEC filings
- FOMC NLP
- real-time streaming
- Kafka/Flink
- cloud database
- Supabase
- GitHub Actions
- Next.js dashboard
- paid data vendors
- portfolio allocation recommendations

These can be added later only after the structured macro engine is stable.

---

## 4. Design Principle

The system should classify macro regimes from economically credible evidence, not from generic sentiment.

The model should not ask:

> Is this article positive or negative?

It should ask:

> Does this data point increase or decrease the probability of a specific macro condition?

Version 0.1 therefore begins with structured macro and market data. Text and news are deferred to later versions.

---

## 5. Recommended v0.1 Architecture

```text
macro-regime-engine/
  README.md
  pyproject.toml
  .env.example

  config/
    sources.yaml
    dimensions.yaml
    regimes.yaml
    model.yaml

  data/
    raw/
    processed/
    features/
    models/
    outputs/

  notebooks/
    01_backtest_regimes.ipynb
    02_feature_diagnostics.ipynb

  src/
    macro_engine/
      __init__.py
      cli.py

      ingest/
        __init__.py
        fred.py
        registry.py

      storage/
        __init__.py
        duckdb_store.py
        parquet_store.py

      normalize/
        __init__.py
        calendar.py
        transforms.py
        vintages.py

      features/
        __init__.py
        indicators.py
        dimensions.py
        normalization.py
        source_health.py

      models/
        __init__.py
        baseline_rules.py
        confidence.py
        regime_schema.py

      evaluation/
        __init__.py
        backtest.py
        benchmark_periods.py
        diagnostics.py

      outputs/
        __init__.py
        report.py
        json_writer.py

  tests/
    test_transforms.py
    test_dimension_scores.py
    test_regime_rules.py
    test_confidence.py
```

---

## 6. Data Strategy

### v0.1 data philosophy

Use one reliable API path first, then expand.

For v0.1, use **FRED as the primary source interface** because it provides a consistent programmatic path to many U.S. macroeconomic, rates, credit, inflation, labor, and market time series.

Later versions can add direct BLS, BEA, Treasury, Census, Federal Reserve, SEC, and news connectors.

---

## 7. Source Credibility Tiers

### Tier 0: Official structured macro data

Highest credibility. These should anchor the model.

Examples:

- inflation
- GDP
- unemployment
- payrolls
- wages
- industrial production
- retail sales
- housing starts
- permits
- consumer spending
- savings rate

### Tier 1: Market-implied macro signals

High credibility but more market-sensitive and volatile.

Examples:

- yield curve
- 2-year Treasury yield
- 10-year Treasury yield
- breakeven inflation
- credit spreads
- VIX
- S&P 500
- dollar index
- oil
- gold

### Tier 2: Official policy and institutional text

Deferred to v0.2.

Examples:

- FOMC statements
- FOMC minutes
- Fed speeches
- Beige Book
- Treasury announcements
- SEC filings

### Tier 3: Broad news and event flow

Deferred to v0.3.

Examples:

- GDELT
- curated RSS feeds
- financial news APIs

### Tier 4: Social / crowd expectations

Optional and experimental. Not a core source.

Examples:

- Reddit
- X/Twitter
- search trends
- consumer forums

---

## 8. v0.1 Data Series Registry

The project should maintain a series registry in `config/sources.yaml`. Every series should have:

```yaml
series_id: CPIAUCSL
name: Consumer Price Index for All Urban Consumers
source: FRED
category: inflation
frequency: monthly
transform: yoy_pct_change
expected_direction: higher_is_inflationary
source_tier: 0
required: true
```

### Candidate v0.1 series

These are initial candidates. Final implementation should verify every series ID during ingestion and record failures in source health metadata.

#### Inflation

| Dimension | Candidate series | Purpose |
|---|---|---|
| Inflation pressure | CPIAUCSL | headline CPI |
| Inflation pressure | CPILFESL | core CPI |
| Inflation pressure | PCEPI | PCE price index |
| Inflation pressure | PCEPILFE | core PCE price index |
| Inflation expectations | T10YIE | 10-year breakeven inflation |
| Inflation expectations | T5YIE | 5-year breakeven inflation |

#### Growth

| Dimension | Candidate series | Purpose |
|---|---|---|
| Growth momentum | GDPC1 | real GDP |
| Growth momentum | INDPRO | industrial production |
| Growth momentum | RSAFS | retail sales |
| Growth momentum | ISM/MAN_PMI equivalent if available | manufacturing cycle |

#### Labor

| Dimension | Candidate series | Purpose |
|---|---|---|
| Labor tightness | UNRATE | unemployment rate |
| Labor tightness | PAYEMS | nonfarm payrolls |
| Labor tightness | ICSA | initial claims |
| Labor tightness | CES0500000003 | average hourly earnings |
| Labor tightness | JTSJOL | job openings |

#### Policy and rates

| Dimension | Candidate series | Purpose |
|---|---|---|
| Policy stance | FEDFUNDS | effective federal funds rate |
| Policy stance | DGS2 | 2-year Treasury yield |
| Policy stance | DGS10 | 10-year Treasury yield |
| Policy stance | T10Y2Y | 10Y minus 2Y Treasury spread |
| Policy stance | DFII10 | 10-year TIPS real yield |

#### Financial conditions and credit

| Dimension | Candidate series | Purpose |
|---|---|---|
| Financial conditions | NFCI | national financial conditions |
| Credit stress | BAA10Y | Baa corporate spread over 10Y Treasury |
| Credit stress | BAMLC0A0CM | investment-grade corporate spread |
| Credit stress | BAMLH0A0HYM2 | high-yield corporate spread |

#### Market risk appetite

| Dimension | Candidate series | Purpose |
|---|---|---|
| Risk appetite | SP500 | equity market trend |
| Risk appetite | VIXCLS | implied equity volatility |
| Risk appetite | DTWEXBGS | broad trade-weighted dollar |
| Risk appetite | DCOILWTICO | WTI oil |
| Risk appetite | GOLDAMGBD228NLBM | gold price |

#### Housing

| Dimension | Candidate series | Purpose |
|---|---|---|
| Housing cycle | MORTGAGE30US | 30-year mortgage rate |
| Housing cycle | HOUST | housing starts |
| Housing cycle | PERMIT | building permits |
| Housing cycle | EXISTINGHOME equivalent if available | existing home sales |

#### Consumer health

| Dimension | Candidate series | Purpose |
|---|---|---|
| Consumer health | UMCSENT | consumer sentiment |
| Consumer health | PSAVERT | personal savings rate |
| Consumer health | DSPIC96 | real disposable personal income |
| Consumer health | PCECC96 | real personal consumption expenditures |
| Consumer health | TOTALSL | consumer credit outstanding |

#### Fiscal impulse

| Dimension | Candidate series | Purpose |
|---|---|---|
| Fiscal impulse | FYFSGDA188S | federal surplus or deficit as % of GDP |
| Fiscal impulse | GFDEBTN | federal debt |
| Fiscal impulse | FGEXPND | federal government expenditures |

---

## 9. Core Macro Dimensions

Version 0.1 should produce a daily or monthly-aligned score for each core dimension.

### Dimension list

| Dimension | Meaning |
|---|---|
| `inflation_pressure` | Are price pressures rising or falling? |
| `growth_momentum` | Is real economic activity strengthening or weakening? |
| `labor_tightness` | Is the labor market tight, balanced, or weakening? |
| `policy_stance` | Is monetary policy restrictive or supportive? |
| `financial_conditions` | Are credit/liquidity conditions easy or tight? |
| `market_risk_appetite` | Are markets pricing risk-on or risk-off? |
| `consumer_health` | Are households strengthening or weakening? |
| `housing_cycle` | Is housing expansionary or contracting? |
| `fiscal_impulse` | Is fiscal policy adding or subtracting demand? |

### Deferred dimensions

These should wait for text/news layers:

| Deferred dimension | Version |
|---|---|
| `geopolitical_stress` | v0.3 |
| `corporate_health` | v0.2 or v0.3 |
| `energy_supply_shock` | partial v0.1 through oil, richer v0.3 with news |
| `policy_communication_tone` | v0.2 |
| `macro_narrative_velocity` | v0.3 |

---

## 10. Directional Semantics

Every dimension must have a clear interpretation.

### Score scale

Each dimension should be normalized to approximately:

```text
-1.0 = strongly contractionary / risk-negative / disinflationary depending on dimension
 0.0 = neutral
+1.0 = strongly expansionary / inflationary / risk-positive depending on dimension
```

However, raw direction is not always good or bad. For example, high inflation is positive on the `inflation_pressure` dimension but negative for a goldilocks regime.

### Dimension-specific interpretation

| Dimension | Positive score means | Negative score means |
|---|---|---|
| `inflation_pressure` | inflation pressure rising / elevated | inflation pressure falling / benign |
| `growth_momentum` | growth accelerating / resilient | growth slowing / contracting |
| `labor_tightness` | tight labor market / wage pressure | labor weakening / unemployment rising |
| `policy_stance` | easier or more supportive policy | tighter or more restrictive policy |
| `financial_conditions` | easier credit/liquidity | tighter credit/liquidity / stress |
| `market_risk_appetite` | risk-on pricing | risk-off pricing |
| `consumer_health` | strong household balance/activity | weak consumer conditions |
| `housing_cycle` | housing expansion | housing contraction |
| `fiscal_impulse` | fiscal support expanding | fiscal drag increasing |

Important: `policy_stance` should be scored so positive means supportive/easier and negative means restrictive/tighter. This avoids confusion when combining with growth and risk appetite.

---

## 11. Data Transformations

Each raw series should be transformed into comparable features.

### Required transformations

| Transform | Use case |
|---|---|
| `level` | rates, spreads, unemployment, VIX |
| `diff_1m` | month-over-month level change |
| `diff_3m` | 3-month change |
| `yoy_pct_change` | CPI, PCE, industrial production, wages |
| `mom_pct_change` | retail sales, production, consumption |
| `rolling_z_3y` | normalize against trailing 3-year history |
| `rolling_z_5y` | normalize against trailing 5-year history |
| `percentile_5y` | interpretable high/low regime context |
| `trend_3m` | near-term acceleration/deceleration |
| `trend_6m` | medium-term acceleration/deceleration |

### Recommended normalization

For each transformed feature:

```text
z_score_t = (value_t - rolling_mean_t) / rolling_std_t
```

Then squash extreme values:

```text
bounded_score = tanh(z_score / 2)
```

This gives a stable `-1` to `+1` style signal while preserving direction.

---

## 12. Feature Direction Mapping

Each feature must define whether higher values are positive or negative for the target dimension.

Examples:

```yaml
- series_id: UNRATE
  dimension: labor_tightness
  transform: diff_3m
  direction: inverse
  reason: rising unemployment means labor market weakening

- series_id: PAYEMS
  dimension: labor_tightness
  transform: yoy_pct_change
  direction: direct
  reason: payroll growth means labor market strength

- series_id: VIXCLS
  dimension: market_risk_appetite
  transform: rolling_z_3y
  direction: inverse
  reason: higher VIX means risk-off

- series_id: DGS2
  dimension: policy_stance
  transform: diff_6m
  direction: inverse
  reason: rising front-end yields indicate tighter policy
```

---

## 13. Dimension Score Construction

Each dimension should aggregate multiple feature signals.

### Feature signal formula

```text
feature_signal = bounded_z_score * direction_multiplier * feature_weight
```

Where:

```text
direction_multiplier = +1 for direct
                       -1 for inverse
```

### Dimension score formula

```text
dimension_score = weighted_mean(feature_signals)
```

### Dimension confidence formula

```text
dimension_confidence = available_required_weight / total_required_weight
```

Then adjust for freshness:

```text
dimension_confidence *= freshness_score
```

### Freshness score

Each series should define an expected reporting frequency:

| Frequency | Stale after |
|---|---|
| daily | 5 calendar days |
| weekly | 14 calendar days |
| monthly | 45 calendar days |
| quarterly | 120 calendar days |

If a required series is stale, confidence should decline but the model should still run.

---

## 14. v0.1 Regime Set

Version 0.1 should classify into seven regimes.

| Regime | Definition |
|---|---|
| `goldilocks_expansion` | Growth positive, inflation benign, financial conditions supportive, risk appetite healthy |
| `inflationary_expansion` | Growth positive but inflation pressure high and policy restrictive/tightening |
| `inflationary_slowdown` | Inflation pressure high while growth momentum weakens |
| `recessionary_deleveraging` | Growth weak, labor weakening, credit/liquidity stress rising, risk appetite falling |
| `policy_easing_recovery` | Inflation falling, policy support rising, risk appetite recovering |
| `liquidity_driven_risk_on` | Risk assets improving mainly because financial conditions/policy are easier, not because growth is clearly strong |
| `crisis_risk_off` | Market stress and credit/liquidity stress dominate the macro signal |

---

## 15. Baseline Rule-Based Classifier

Version 0.1 should start with an explainable scoring model, not a black-box classifier.

### Inputs

```python
DimensionVector = {
    "inflation_pressure": float,
    "growth_momentum": float,
    "labor_tightness": float,
    "policy_stance": float,
    "financial_conditions": float,
    "market_risk_appetite": float,
    "consumer_health": float,
    "housing_cycle": float,
    "fiscal_impulse": float,
}
```

### Regime scoring logic

Each regime receives a score based on dimension alignment.

#### Goldilocks expansion

```text
goldilocks_score =
    + growth_momentum * 0.25
    - abs(max(inflation_pressure, 0)) * 0.20
    + financial_conditions * 0.20
    + market_risk_appetite * 0.20
    + consumer_health * 0.10
    + housing_cycle * 0.05
```

#### Inflationary expansion

```text
inflationary_expansion_score =
    + growth_momentum * 0.25
    + inflation_pressure * 0.25
    - policy_stance * 0.20
    + labor_tightness * 0.15
    + market_risk_appetite * 0.10
    + consumer_health * 0.05
```

Note: because `policy_stance` is positive when supportive and negative when restrictive, subtracting policy stance rewards restrictive policy for this regime.

#### Inflationary slowdown

```text
inflationary_slowdown_score =
    + inflation_pressure * 0.30
    - growth_momentum * 0.25
    - financial_conditions * 0.15
    - policy_stance * 0.15
    - housing_cycle * 0.10
    - consumer_health * 0.05
```

#### Recessionary deleveraging

```text
recessionary_deleveraging_score =
    - growth_momentum * 0.25
    - labor_tightness * 0.20
    - financial_conditions * 0.25
    - market_risk_appetite * 0.15
    - consumer_health * 0.10
    - housing_cycle * 0.05
```

#### Policy easing recovery

```text
policy_easing_recovery_score =
    - inflation_pressure * 0.20
    + policy_stance * 0.25
    + financial_conditions * 0.20
    + market_risk_appetite * 0.15
    + growth_momentum * 0.10
    + consumer_health * 0.10
```

#### Liquidity-driven risk-on

```text
liquidity_risk_on_score =
    + market_risk_appetite * 0.30
    + financial_conditions * 0.25
    + policy_stance * 0.20
    + max(-inflation_pressure, 0) * 0.10
    + growth_momentum * 0.05
    + fiscal_impulse * 0.10
```

#### Crisis risk-off

```text
crisis_risk_off_score =
    - market_risk_appetite * 0.30
    - financial_conditions * 0.30
    - growth_momentum * 0.15
    + max(inflation_pressure, 0) * 0.10
    - consumer_health * 0.10
    - housing_cycle * 0.05
```

### Convert scores to probabilities

Use softmax:

```text
P(regime_i) = exp(score_i / temperature) / sum(exp(score_j / temperature))
```

Recommended initial temperature:

```text
temperature = 0.35
```

Lower temperature makes the model more decisive. Higher temperature makes probabilities more diffuse.

---

## 16. Primary Regime Selection

The primary regime is the regime with the highest probability.

```python
primary_regime = argmax(regime_probabilities)
```

### Ambiguous regime handling

If the top probability is below `0.35`, mark the classification as low conviction.

If the top two regimes are within `0.08`, mark it as a transition zone.

Example:

```json
{
  "primary_regime": "inflationary_slowdown",
  "secondary_regime": "recessionary_deleveraging",
  "transition_zone": true,
  "explanation": "Inflation remains elevated while growth and financial conditions are weakening."
}
```

---

## 17. Confidence Score

The confidence score should not be the same as the top regime probability.

Regime probability answers:

> Which regime best fits the current dimension vector?

Confidence answers:

> How much should we trust this classification given data availability, freshness, agreement, and decisiveness?

### Recommended formula

```text
confidence =
    0.30 * data_completeness
  + 0.25 * data_freshness
  + 0.25 * signal_agreement
  + 0.20 * regime_decisiveness
```

### Components

#### Data completeness

```text
data_completeness = available_required_feature_weight / total_required_feature_weight
```

#### Data freshness

Average freshness score across required dimensions.

```text
freshness_score = 1.0 if current
                = 0.5 if stale but usable
                = 0.0 if missing/unusable
```

#### Signal agreement

Measures whether important dimensions point toward a coherent regime.

Example:

```text
signal_agreement = 1 - normalized_std(weighted_regime_support_scores)
```

Simpler v0.1 fallback:

```text
signal_agreement = average absolute alignment of top regime's required dimensions
```

#### Regime decisiveness

```text
regime_decisiveness = top_probability - second_probability
```

Rescale to `0..1`.

---

## 18. Output Contract

Every classification run should produce a JSON object.

### Required output schema

```json
{
  "as_of": "2026-05-08",
  "generated_at": "2026-05-08T12:00:00Z",
  "model_version": "0.1.0",
  "data_version": "fred-v1",
  "primary_regime": "inflationary_slowdown",
  "secondary_regime": "recessionary_deleveraging",
  "transition_zone": true,
  "confidence": 0.72,
  "regime_probabilities": {
    "goldilocks_expansion": 0.08,
    "inflationary_expansion": 0.16,
    "inflationary_slowdown": 0.34,
    "recessionary_deleveraging": 0.29,
    "policy_easing_recovery": 0.05,
    "liquidity_driven_risk_on": 0.04,
    "crisis_risk_off": 0.04
  },
  "dimension_scores": {
    "inflation_pressure": {
      "score": 0.72,
      "confidence": 0.91,
      "trend_3m": 0.18,
      "top_features": ["core CPI", "breakeven inflation"]
    },
    "growth_momentum": {
      "score": -0.31,
      "confidence": 0.84,
      "trend_3m": -0.22,
      "top_features": ["industrial production", "retail sales"]
    }
  },
  "top_drivers": [
    "Inflation pressure remains elevated.",
    "Growth momentum has weakened over the last 3 months.",
    "Financial conditions are tightening."
  ],
  "watchlist": [
    "If labor tightness weakens further, recessionary_deleveraging probability may rise.",
    "If inflation pressure falls while risk appetite improves, policy_easing_recovery probability may rise."
  ],
  "source_health": {
    "total_series": 35,
    "available_series": 33,
    "stale_series": 2,
    "missing_series": 0
  }
}
```

---

## 19. Human-Readable Report Output

The CLI should also produce a short markdown report.

Example:

```markdown
# Macro Regime Report

As of: 2026-05-08
Model: 0.1.0

Primary regime: inflationary_slowdown
Confidence: 0.72
Transition zone: yes

## Why

- Inflation pressure remains elevated.
- Growth momentum has weakened over the last 3 months.
- Financial conditions are tightening.

## Top regime probabilities

1. inflationary_slowdown: 34%
2. recessionary_deleveraging: 29%
3. inflationary_expansion: 16%

## Watchlist

- If labor conditions weaken further, recessionary_deleveraging risk rises.
- If inflation falls and policy becomes more supportive, policy_easing_recovery probability rises.
```

---

## 20. Storage Design

Use local storage only in v0.1.

### DuckDB tables

#### `raw_observations`

```sql
CREATE TABLE raw_observations (
    source TEXT,
    series_id TEXT,
    date DATE,
    value DOUBLE,
    realtime_start DATE,
    realtime_end DATE,
    inserted_at TIMESTAMP,
    metadata JSON
);
```

#### `feature_values`

```sql
CREATE TABLE feature_values (
    as_of DATE,
    series_id TEXT,
    transform TEXT,
    raw_value DOUBLE,
    transformed_value DOUBLE,
    z_score DOUBLE,
    bounded_score DOUBLE,
    direction_multiplier DOUBLE,
    feature_signal DOUBLE,
    confidence DOUBLE
);
```

#### `dimension_scores`

```sql
CREATE TABLE dimension_scores (
    as_of DATE,
    dimension TEXT,
    score DOUBLE,
    confidence DOUBLE,
    trend_1m DOUBLE,
    trend_3m DOUBLE,
    source_count INTEGER,
    metadata JSON
);
```

#### `regime_classifications`

```sql
CREATE TABLE regime_classifications (
    as_of DATE,
    model_version TEXT,
    primary_regime TEXT,
    secondary_regime TEXT,
    transition_zone BOOLEAN,
    confidence DOUBLE,
    probabilities JSON,
    top_drivers JSON,
    watchlist JSON,
    source_health JSON,
    generated_at TIMESTAMP
);
```

### Parquet outputs

Save major tables as Parquet for portability:

```text
data/processed/raw_observations.parquet
data/features/feature_values.parquet
data/features/dimension_scores.parquet
data/outputs/regime_classifications.parquet
```

---

## 21. CLI Design

The package should expose a CLI named `macro-engine`.

### Commands

#### Initialize project storage

```bash
macro-engine init
```

Creates local DuckDB database and required folders.

#### Update data

```bash
macro-engine update --start 2010-01-01 --end today
```

Fetches FRED data and stores raw observations.

#### Build features

```bash
macro-engine build-features --as-of today
```

Builds transformed features and dimension scores.

#### Classify regime

```bash
macro-engine classify --as-of today
```

Outputs current regime classification.

#### Full run

```bash
macro-engine run --start 2010-01-01 --as-of today
```

Runs ingestion, feature building, classification, and report generation.

#### Backtest

```bash
macro-engine backtest --start 1990-01-01 --end today
```

Classifies every historical period and compares against benchmark periods.

#### Explain

```bash
macro-engine explain --as-of today
```

Prints a human-readable explanation of the classification.

---

## 22. Configuration Files

### `config/sources.yaml`

Defines series, transforms, weights, and source metadata.

Example:

```yaml
sources:
  - series_id: CPIAUCSL
    name: Consumer Price Index
    provider: FRED
    dimension: inflation_pressure
    frequency: monthly
    transform: yoy_pct_change
    direction: direct
    weight: 0.25
    source_tier: 0
    required: true
    stale_after_days: 45

  - series_id: VIXCLS
    name: CBOE Volatility Index
    provider: FRED
    dimension: market_risk_appetite
    frequency: daily
    transform: rolling_z_3y
    direction: inverse
    weight: 0.30
    source_tier: 1
    required: true
    stale_after_days: 5
```

### `config/dimensions.yaml`

Defines dimension semantics.

```yaml
dimensions:
  inflation_pressure:
    description: Are price pressures rising or falling?
    positive_means: Inflation pressure rising or elevated
    negative_means: Inflation pressure falling or benign
    min_required_weight: 0.60

  growth_momentum:
    description: Is real economic activity strengthening or weakening?
    positive_means: Growth strengthening
    negative_means: Growth weakening or contracting
    min_required_weight: 0.60
```

### `config/regimes.yaml`

Defines regime scoring weights.

```yaml
regimes:
  inflationary_slowdown:
    description: Inflation pressure high while growth momentum weakens.
    weights:
      inflation_pressure: 0.30
      growth_momentum: -0.25
      financial_conditions: -0.15
      policy_stance: -0.15
      housing_cycle: -0.10
      consumer_health: -0.05
```

### `config/model.yaml`

Defines model hyperparameters.

```yaml
model:
  version: 0.1.0
  softmax_temperature: 0.35
  transition_zone_probability_gap: 0.08
  low_conviction_threshold: 0.35
  normalization:
    rolling_window_years: 5
    zscore_clip: 3.0
    bounded_transform: tanh
```

---

## 23. Backtesting and Evaluation

Version 0.1 must include historical evaluation, even if approximate.

### Benchmark periods

Create `evaluation/benchmark_periods.py` with known macro episodes.

Initial benchmark examples:

| Period | Expected model behavior |
|---|---|
| 2000-2002 | recessionary / risk-off behavior |
| 2003-2006 | expansion / risk-on behavior |
| 2007-2009 | crisis risk-off / recessionary deleveraging |
| 2010-2012 | policy easing recovery / weak expansion |
| 2015-2016 | slowdown / dollar/oil stress |
| 2020-03 to 2020-06 | crisis risk-off then policy easing recovery |
| 2021 | inflationary expansion |
| 2022 | inflationary slowdown / policy tightening |
| 2023 regional banking stress | financial conditions stress episode |

### Evaluation metrics

Use simple diagnostics first:

| Metric | Purpose |
|---|---|
| regime timeline chart | visually inspect regime continuity |
| transition count | detect excessive regime flipping |
| average regime duration | check persistence |
| benchmark hit rate | rough match against expected historical episodes |
| confidence vs error | check whether confidence means anything |
| top-driver sanity check | inspect explanations by period |

### Anti-goal

Do not optimize the v0.1 model to maximize market returns. That creates overfitting risk too early.

---

## 24. Testing Requirements

### Unit tests

#### Transform tests

- percent change calculation
- year-over-year calculation
- rolling z-score calculation
- tanh bounding
- inverse/direct direction mapping

#### Dimension tests

- missing series lowers confidence
- stale series lowers confidence
- inverse direction works correctly
- dimension score stays inside expected range

#### Regime tests

- high growth + low inflation + easy conditions favors `goldilocks_expansion`
- high inflation + strong growth favors `inflationary_expansion`
- high inflation + weak growth favors `inflationary_slowdown`
- weak growth + weak labor + tight credit favors `recessionary_deleveraging`
- easy policy + improving risk favors `policy_easing_recovery`
- weak credit + high VIX favors `crisis_risk_off`

#### Output tests

- JSON schema is valid
- probabilities sum to 1
- confidence is between 0 and 1
- primary regime exists in probability map
- report generation does not fail with partial data

---

## 25. Implementation Order

### Phase 1: Project skeleton

1. Create repo structure.
2. Add `pyproject.toml`.
3. Add configs.
4. Add CLI skeleton.
5. Add basic tests.

### Phase 2: Data ingestion

1. Implement FRED client.
2. Implement source registry loader.
3. Fetch one test series.
4. Fetch all configured series.
5. Save to DuckDB and Parquet.
6. Add source health report.

### Phase 3: Feature engineering

1. Implement transforms.
2. Implement rolling normalization.
3. Implement direction mapping.
4. Implement feature signal construction.
5. Implement dimension aggregation.
6. Test missing/stale behavior.

### Phase 4: Regime classifier

1. Implement regime scoring from config.
2. Implement softmax probabilities.
3. Implement primary/secondary regime selection.
4. Implement transition-zone logic.
5. Implement confidence formula.
6. Implement driver extraction.

### Phase 5: Outputs

1. Write JSON output.
2. Write markdown report.
3. Add CLI commands.
4. Add example output files.

### Phase 6: Historical evaluation

1. Run backtest from 1990 or earliest available date.
2. Create regime timeline.
3. Compare against benchmark periods.
4. Inspect top drivers.
5. Tune weights only after documented review.

---

## 26. v0.1 Acceptance Criteria

The project is v0.1-complete when:

- It can fetch configured FRED series.
- It can store raw observations locally.
- It can build normalized feature signals.
- It can produce all core dimension scores.
- It can classify the current regime.
- It can output JSON and markdown reports.
- It can run a historical backtest.
- It handles missing/stale data gracefully.
- Unit tests pass.
- Regime probabilities sum to 1.
- The model produces plausible classifications for major historical episodes.
- All assumptions and weights are config-driven, not hardcoded.

---

## 27. Known Limitations of v0.1

Version 0.1 will not understand news narratives, central bank language, geopolitical shocks, or corporate guidance.

It will also be limited by:

- macro data release lags
- data revisions
- FRED series availability
- simplified rule-based weighting
- no real-time text ingestion
- no probabilistic latent-state model yet
- no direct vintage/realtime database handling beyond what the provider exposes

These limitations are acceptable for v0.1 as long as the product is positioned correctly.

---

## 28. Roadmap After v0.1

### v0.2: Policy and institutional text layer

Add:

- FOMC statements
- FOMC minutes
- Fed speeches
- Beige Book
- target-aware macro text classification
- policy tone dimension

### v0.3: News and event layer

Add:

- GDELT
- curated RSS feeds
- geopolitical stress dimension
- narrative velocity
- event shock detection

### v0.4: Probabilistic regime model

Add:

- Hidden Markov Model
- change-point detection
- regime transition probabilities
- confidence calibration

### v0.5: Dashboard/API

Add:

- local dashboard
- FastAPI service
- charts
- explainability views
- exportable reports

### v1.0: Deployment-ready package

Add:

- Docker
- scheduled runs
- production logging
- monitoring
- optional cloud storage
- documentation
- release workflow

---

## 29. Immediate Next Deliverable

The next deliverable should be the concrete `config/sources.yaml` and `config/regimes.yaml` files.

Those files will lock the model assumptions before code is written.

Recommended next document:

**v0.1 Config Draft: Sources, Dimensions, and Regime Weights**

This should define:

1. exact FRED series IDs
2. transformations
3. direction mappings
4. feature weights
5. required vs optional series
6. stale-data thresholds
7. regime scoring weights
8. minimum data requirements

Once that is reviewed, implementation can begin in VS Code.

