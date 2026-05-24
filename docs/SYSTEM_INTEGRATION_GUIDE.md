# Macro Regime Identifier — System Integration Guide

> **Version**: v1.2-rc1 | **Generated**: 2026-05-24
> **Dashboard**: https://riperdy-tech.github.io/Macro-Regime-Identifier/
> **Repository**: https://github.com/riperdy-tech/Macro-Regime-Identifier

This document describes the complete architecture, data flow, input sources,
output schema, and integration points of the Macro Regime Identifier — a
local-first U.S. macro regime diagnostic platform. It is written for engineers
integrating this system into downstream stock analysis, screening, or portfolio
research platforms.

**Important**: This system produces diagnostic research outputs only. It is not
investment advice, a trading system, an allocation system, or a predictive
validation model.

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA SOURCES                             │
│  FRED API (12 series)  │  Synthetic/Mock News (committed)   │
│  DeepSeek AI (optional) │  Real News CSV (local-only)       │
└──────────────┬──────────────────────┬───────────────────────┘
               │                      │
               ▼                      ▼
┌──────────────────────────┐  ┌───────────────────────────────┐
│   MACRO PIPELINE         │  │   NEWS PIPELINE               │
│                          │  │                               │
│  Ingest (FRED)           │  │  Ingest (CSV/RSS/synthetic)   │
│  → Build Features        │  │  → AI Classify (mock/live)    │
│  → As-of Alignment       │  │  → Theme Scores               │
│  → Dimension Scores      │  │  → Sector Impact Scores       │
│  → Regime Probabilities  │  │  → Deterministic Aggregation  │
│  → Historical Diagnostic │  │                               │
│  → Reports (JSON/MD)     │  │                               │
└──────────┬───────────────┘  └───────────┬───────────────────┘
           │                              │
           ▼                              ▼
┌──────────────────────────┐  ┌───────────────────────────────┐
│   SECTOR PIPELINE        │  │   MONITORING                  │
│                          │  │                               │
│  Sector Macro Scores     │  │  Input Quality                │
│  (regime × dimension     │  │  Classification Rates         │
│   exposure × prior)      │  │  Source Coverage              │
│                          │  │  Overlay Impact               │
└──────────┬───────────────┘  └───────────┬───────────────────┘
           │                              │
           ▼                              │
┌──────────────────────────┐              │
│   COMBINED OVERLAY       │◄─────────────┘
│                          │
│  75% macro + 25% news    │
│  Bounded [-0.5, +0.5]    │
│  With uncertainty penalty│
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                    EXPORTS                                   │
│                                                              │
│  outputs/*.json   →  dashboard/public/data/  →  GitHub Pages │
│  outputs/*.md     →  human-readable reports                  │
│  data/macro_engine.duckdb  →  local database (gitignored)    │
└──────────────────────────────────────────────────────────────┘
```

### Processing Layers

| Layer | Input | Output | Deterministic? |
|---|---|---|---|
| Macro | 12 FRED series | Regime probabilities, dimension scores | Yes |
| Sector | Regime + dimension scores | 11 sector macro scores | Yes |
| News | Text items | Theme & sector classifications | Scores: Yes. Classification: AI (optional) |
| Combined | Sector macro + sector news | Bounded combined overlay | Yes |
| Monitoring | All above | Quality checks | Yes |

---

## 2. Data Sources

### 2.1 FRED Macro Series (13 configured, 12 enabled)

All fetched from the Federal Reserve Economic Data (FRED) API.
Requires `FRED_API_KEY` (32-char lowercase string).

| Series ID | Name | Dimension | Frequency | Stale After |
|---|---|---|---|---|
| INDPRO | Industrial Production Total Index | growth | monthly | 45 days |
| PAYEMS | All Employees, Total Nonfarm | growth | monthly | 45 days |
| UNRATE | Unemployment Rate | growth | monthly | 45 days |
| CPIAUCSL | CPI All Urban Consumers | inflation | monthly | 45 days |
| PCEPI | PCE Price Index | inflation | monthly | 45 days |
| FEDFUNDS | Effective Federal Funds Rate | policy | monthly | 45 days |
| DGS10 | 10-Year Treasury Rate | policy | daily | 5 days |
| BAA10Y | Baa Corp Yield vs 10Y | credit_liquidity | daily | 5 days |
| AAA10Y | Aaa Corp Yield vs 10Y | credit_liquidity | daily | 5 days |
| T10Y2Y | 10Y-2Y Treasury Spread | yield_curve | daily | 5 days |
| VIXCLS | VIX Close | market_risk_appetite | daily | 5 days |
| USSLIND | Leading Index | growth | monthly | 45 days |
| UMCSENT | Michigan Consumer Sentiment | growth | monthly | 45 days |

### 2.2 News Sources

- **Synthetic sample** (`synthetic_sample`): 270 pre-classified mock items (committed)
- **Real news CSV**: Local files under `data/news_pilot/` (gitignored, 12 source groups)
- **Live AI**: DeepSeek v4-flash via `DEEPSEEK_API_KEY` (optional)

### 2.3 Storage

- **DuckDB**: `data/macro_engine.duckdb` (local, gitignored)
  - Contains all observations, features, dimensions, regimes, sectors, news classifications
  - ~40 tables
- **Parquet**: `data/raw/fred/` (raw FRED observations, gitignored)

---

## 3. Macro Regime Engine

### 3.1 Pipeline Flow

```
FRED API → raw_observations → features → asof_features
    → dimensions → regimes → historical_diagnostic → reports
```

### 3.2 Feature Engineering

12 raw FRED series are transformed into 15 features:
- **YoY change** (e.g., `industrial_production_yoy_z`) — z-score normalized year-over-year
- **6-month change** (e.g., `unemployment_6m_change_z`) — z-score normalized
- **Level z-score** (e.g., `vix_level_z`)
- **Spread/difference** (e.g., `t10y2y_level_z`)
- **Inverted** (e.g., `fedfunds_level_inv_z` — inverted so positive = easier policy)

All features are z-score normalized against their own history (1990+).
Output: 354,445 feature rows, 340,320 valid (as of May 2026).

### 3.3 Dimensions (6 configured)

Each dimension aggregates weighted feature contributions into a single score:

| Dimension | Polarity | Key Features |
|---|---|---|
| `inflation_pressure` | Positive = rising | CPI YoY z, PCE YoY z |
| `growth_momentum` | Positive = accelerating | IP YoY z, Payrolls YoY z, Unemp 6m chg z, Sentiment z |
| `labor_tightness` | Positive = tight | Payrolls YoY z, Unemp 6m chg z |
| `policy_stance` | Positive = easier | Fed Funds inv z, 10Y z |
| `financial_conditions` | Positive = easier | Baa spread inv z, Aaa spread inv z |
| `market_risk_appetite` | Positive = risk-on | VIX inv z |

Dimension scores are continuous values. Positive ≠ "good" — it means the
dimension is in its defined positive direction.

### 3.4 Regimes (6 configured)

Each regime scores dimensions with specific weights and polarity modes:

| Regime | Growth | Inflation | Policy | Financial | Labor | Risk |
|---|---|---|---|---|---|---|
| `goldilocks` | +0.25 | −0.25 | — | +0.20 | neutral | +0.20 |
| `reflation` | +0.25 | **+0.30** | — | −0.05 | — | +0.15 |
| `tightening` | −0.15 | −0.10 | **+0.40** | −0.15 | — | −0.10 |
| `stagflation` | −0.25 | **+0.35** | — | −0.20 | +0.15 | — |
| `recession` | **−0.40** | −0.10 | +0.15 | −0.20 | −0.10 | — |
| `financial_stress` | −0.15 | — | −0.15 | **−0.35** | — | **−0.25** |

Raw regime scores are converted to probabilities via softmax.
A transition filter prevents regime flipping on small probability differences
(requires 3% margin to confirm a new regime). The filter also penalizes
excessively frequent switches.

### 3.5 Regime Output Schema

```json
{
  "date": "2026-05-01 00:00:00",
  "reported_regime": "reflation",
  "reported_regime_probability": 0.3335,
  "reported_confidence": 0.0329,
  "raw_dominant_regime": "reflation",
  "raw_dominant_probability": 0.3335,
  "regime_probabilities": {
    "reflation": 0.3335,
    "tightening": 0.3006,
    "stagflation": 0.2004,
    "goldilocks": 0.1048,
    "recession": 0.0607
  },
  "transition_filter_applied": true,
  "transition_filter_reason": "switch_confirmed",
  "valid": true
}
```

Key fields for downstream integration:
- `reported_regime`: The filtered, user-facing regime label
- `regime_probabilities`: Full probability distribution (sums to ~1.0)
- `reported_confidence`: How confident the system is in the regime call
- `raw_dominant_regime`: Before transition filter — may differ from reported

---

## 4. Sector Diagnostic Layer

### 4.1 How Sector Scores Work

Sector scores combine three inputs:

```
sector_score = Σ(dimension_score × dimension_exposure) + Σ(regime_probability × regime_prior)
```

- **Dimension exposures** (`config/sector_exposures.yaml`): How a sector responds to each macro dimension (e.g., energy has +0.7 exposure to inflation_pressure)
- **Regime priors** (`config/sector_regime_priors.yaml`): How a sector historically performs in each regime (e.g., energy has +0.30 prior for reflation)

### 4.2 11 Sectors Tracked

| Sector ID | Label | Proxy Ticker |
|---|---|---|
| `energy` | Energy | XLE |
| `materials` | Materials | XLB |
| `industrials` | Industrials | XLI |
| `consumer_discretionary` | Consumer Discretionary | XLY |
| `consumer_staples` | Consumer Staples | XLP |
| `health_care` | Health Care | XLV |
| `financials` | Financials | XLF |
| `information_technology` | Information Technology | XLK |
| `communication_services` | Communication Services | XLC |
| `utilities` | Utilities | XLU |
| `real_estate` | Real Estate | XLRE |

### 4.3 Sector Output Schema

```json
{
  "date": "2026-05-01",
  "reported_macro_regime": "reflation",
  "macro_confidence": 0.0329,
  "sector_ranking": [
    {
      "rank": 1,
      "sector_id": "energy",
      "raw_sector_score": 1.156,
      "confidence_adjusted_score": 0.485,
      "proxy_ticker": "XLE",
      "top_supporting_components": [
        {
          "component_id": "inflation_pressure",
          "contribution": 0.972,
          "weight_or_exposure": 0.7
        }
      ],
      "top_opposing_components": [
        {
          "component_id": "credit_liquidity",
          "contribution": -0.137,
          "weight_or_exposure": -0.2
        }
      ]
    }
  ]
}
```

Key fields:
- `raw_sector_score`: Raw attractiveness score (higher = more tailwind from macro)
- `confidence_adjusted_score`: Scaled by macro confidence (low confidence shrinks scores toward zero)
- `proxy_ticker`: ETF ticker for reference (not a recommendation)
- `top_supporting_components`: What's driving the positive score
- `top_opposing_components`: What's dragging the score down

---

## 5. News / Event Diagnostic Layer

### 5.1 Architecture

```
News Text → AI/Mock Classification → Deterministic Aggregation → Scores
```

### 5.2 Classification

Each news item is classified into:
- **Macro themes**: monetary_tightening, growth_slowdown, inflation_pressure, etc. (config/news_themes.yaml)
- **Sector impacts**: Positive/negative/neutral tailwind per sector

Mock mode uses pre-classified synthetic data. Live mode calls DeepSeek v4-flash.

### 5.3 News Scoring

Classifications are aggregated deterministically:
- **Theme scores**: Count of items per theme, weighted by recency and confidence
- **Sector scores**: Net tailwind/headwind per sector, bounded to [-1.0, +1.0]
- **Daily + Weekly** aggregations produced

### 5.4 12 News Source Groups

```
consumer, credit_financial_conditions, defensive_sectors, energy_commodities,
geopolitical, healthcare, inflation_rates, labor, macro_general,
manufacturing_industrials, real_estate, technology_ai
```

---

## 6. Combined Sector Diagnostic

### 6.1 Overlay Formula

```
combined_score = 0.75 × normalized_macro_score + 0.25 × bounded_news_score
```

- News component is bounded to [-0.5, +0.5] to prevent news from dominating
- Uncertainty penalty applied when news confidence is low
- Overlay rank change monitored: max single-sector rank shift is tracked

### 6.2 Combined Output Schema

```json
{
  "combined_experimental_ranking": [
    {
      "rank": 1,
      "sector_id": "energy",
      "combined_score": 1.8396,
      "sector_macro_score": 2.3029,
      "sector_news_score": 0.5,
      "news_item_count": 14,
      "diagnostic_confidence": 0.2122,
      "macro_component_weight": 0.75,
      "news_component_weight": 0.25,
      "components": [
        {"component_name": "normalized_sector_macro_score", "component_value": 2.3029, "component_weight": 0.75},
        {"component_name": "bounded_sector_news_score", "component_value": 0.5, "component_weight": 0.25},
        {"component_name": "news_uncertainty_penalty", "component_value": 0.0125, "component_weight": 1.0}
      ]
    }
  ]
}
```

---

## 7. Dashboard Data Files (GitHub Pages)

All files served as static JSON from:
```
https://riperdy-tech.github.io/Macro-Regime-Identifier/data/
```

### 7.1 Manifest

`/data/manifest.json` — Entry point. Lists all available files and metadata:
```json
{
  "generated_at": "2026-05-24T00:22:00Z",
  "data_status": "complete",
  "available_files": [
    "daily_diagnostic_summary.json",
    "current_sector_ranking.json",
    "news_score_report.json",
    "combined_sector_diagnostic.json",
    "news_monitoring_report.json",
    "news_accumulation_report.json",
    "news_source_coverage_report.json",
    "history_index.json"
  ],
  "missing_files": [],
  "latest_macro_date": "2026-05-01",
  "latest_news_score_date": "2026-05-21"
}
```

### 7.2 File Descriptions

| File | Content | Update Frequency |
|---|---|---|
| `daily_diagnostic_summary.json` | Regime + sector + news + monitoring summary | Daily |
| `current_sector_ranking.json` | 11-sector macro ranking with components | Daily |
| `news_score_report.json` | News theme and sector scores | Daily |
| `combined_sector_diagnostic.json` | Macro + news combined overlay | Daily |
| `news_monitoring_report.json` | Input quality, classification rates, overlay impact | Daily |
| `news_accumulation_report.json` | Historical accumulation tracking, readiness label | Daily |
| `news_source_coverage_report.json` | Source group coverage, staleness, unmapped items | Daily |
| `history_index.json` | Run history with dates, statuses, regimes, top sectors | Cumulative |

### 7.3 Data Freshness

- **Generated**: Mon-Fri ~22:37 UTC by GitHub Actions
- **Macro data**: Usually 2-4 weeks behind (FRED release lag)
- **News data**: As of last classification run
- **Staleness**: If workflow fails, data freezes until next successful run

---

## 8. Integration Points for Downstream Platforms

### 8.1 HTTP/REST (Recommended)

Fetch the latest data directly from GitHub Pages:

```python
import requests

BASE = "https://riperdy-tech.github.io/Macro-Regime-Identifier/data"

# Get manifest to check freshness
manifest = requests.get(f"{BASE}/manifest.json").json()
if manifest["data_status"] != "complete":
    raise ValueError("Dashboard data incomplete")

# Get current regime
regime_data = requests.get(f"{BASE}/daily_diagnostic_summary.json").json()
current_regime = regime_data["macro"]["reported_regime"]
regime_probs = regime_data["macro"]["regime_probabilities"]

# Get sector ranking
sectors = requests.get(f"{BASE}/current_sector_ranking.json").json()
top_sectors = [(s["sector_id"], s["confidence_adjusted_score"], s["proxy_ticker"])
               for s in sectors["sector_ranking"][:5]]

# Get combined overlay
combined = requests.get(f"{BASE}/combined_sector_diagnostic.json").json()
combined_top = [(s["sector_id"], s["combined_score"])
                for s in combined["combined_experimental_ranking"][:5]]
```

### 8.2 Key Signals for Stock Analysis

| Signal | Source | Meaning |
|---|---|---|
| `reported_regime` | daily_diagnostic_summary | Current macro regime label |
| `regime_probabilities` | daily_diagnostic_summary | Full probability distribution across 6 regimes |
| `confidence_adjusted_score` | current_sector_ranking | Sector macro tailwind (higher = more favorable) |
| `combined_score` | combined_sector_diagnostic | Macro + news combined overlay |
| `news_item_count` | combined_sector_diagnostic | How many news items mention this sector |
| `sector_news_score` | combined_sector_diagnostic | Pure news sentiment (-1.0 to +1.0) per sector |
| `diagnostic_confidence` | combined_sector_diagnostic | Confidence in the combined diagnostic |
| `readiness_label` | news_accumulation_report | Operating maturity (insufficient_history → early_history → monitor_ready → validation_candidate) |

### 8.3 Integration Patterns

**Pattern A: Regime-aware sector filter**
```
if regime == "reflation":
    focus on energy, materials, industrials  (top macro scores)
elif regime == "goldilocks":
    focus on technology, consumer_discretionary
elif regime == "recession":
    focus on consumer_staples, health_care, utilities
```

**Pattern B: Score-based sector weighting**
```
position_score = 0.6 × confidence_adjusted_score + 0.4 × combined_score
```

**Pattern C: Regime probability-weighted allocation**
```
for sector in sectors:
    expected_score = Σ(regime_prob[r] × sector_regime_prior[sector][r])
```

### 8.4 Important Caveats

1. **Scores are diagnostics, not predictions.** They describe current macro
   conditions, not future returns.
2. **Confidence is low.** Typical macro confidence is 3-20%. Scores should be
   interpreted as weak directional signals.
3. **News overlay is experimental.** The 25% news weight in combined diagnostics
   is a design choice, not an optimized parameter.
4. **No point-in-time guarantee.** Historical diagnostics use revised FRED data,
   not real-time vintages.
5. **Sector proxies are references only.** XLE, XLF, XLK, etc. are for
   validation comparison, not investment recommendations.

---

## 9. Automation & Scheduling

### 9.1 GitHub Actions

```
Workflow: .github/workflows/daily-dashboard.yml
Schedule: Mon-Fri 22:37 UTC (cron: "37 22 * * 1-5")
Runtime:  ~8 minutes (mock mode)
Secrets:  FRED_API_KEY, DEEPSEEK_API_KEY (optional)
```

### 9.2 Local CLI Commands

```powershell
# Full daily run (individual steps workaround for Python 3.14)
python -m macro_engine.cli ingest --config config/phase_b_sources.yaml
python -m macro_engine.cli build-features --config config/phase_b_sources.yaml
python -m macro_engine.cli build-dimensions --config config/phase_b_sources.yaml
python -m macro_engine.cli build-regimes --config config/phase_b_sources.yaml
python -m macro_engine.cli run-historical-diagnostic --config config/phase_b_sources.yaml
python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml
python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml
python -m macro_engine.cli export-dashboard-data

# Query current state
python -m macro_engine.cli current-regime
python -m macro_engine.cli current-sector-ranking
python -m macro_engine.cli current-combined-sector-ranking
```

### 9.3 Windows Task Scheduler (alternative)

Run `scripts/run_daily_diagnostic.ps1` via Task Scheduler for local-only
automation with access to gitignored data files and live AI.

---

## 10. Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python ≥3.11, DuckDB, pandas, numpy, Pydantic, Typer |
| AI Classification | DeepSeek v4-flash (OpenAI-compatible API) |
| Frontend | Vite, React 19, TypeScript |
| Deployment | GitHub Actions, GitHub Pages |
| Config | YAML (all scoring rules, dimensions, regimes in config files) |
| Testing | pytest (190 tests), ruff (linting) |

---

## 11. Readiness Thresholds

The platform tracks operating maturity:

```
insufficient_history   < 5 real operating dates
early_history          5–20 dates
monitor_ready          20+ dates, good source coverage
validation_candidate   60+ dates, stable coverage
```

Current status: `insufficient_history` (3 real operating dates as of May 2026).
Validation cannot be claimed until `monitor_ready` or `validation_candidate`
is reached.

---

## 12. Limitations

- **Not predictive.** Historical diagnostics use revised data. No forward
  return validation has been performed.
- **U.S.-only.** All FRED series are U.S. macroeconomic indicators.
- **Low confidence.** Regime probabilities are wide and confidence is low
  (typically 3-20%).
- **Sector assumptions are heuristic.** Exposure weights and regime priors
  are config assumptions, not empirically derived.
- **News is mock by default.** Live AI classification requires DeepSeek key
  and real news sources.
- **Python 3.14 compatibility.** The `run_pipeline` wrapper hangs on 3.14;
  documented workaround is to use individual CLI steps.
- **Dashboard is static.** Data only updates when the GitHub Actions
  workflow succeeds. No real-time or streaming data.
