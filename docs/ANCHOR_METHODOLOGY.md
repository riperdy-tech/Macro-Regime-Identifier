# Capital-Market Anchors — Methodology (MRI v0.2)

Three monthly, point-in-time anchors are published by the Macro Regime Indicator as
**additive** output artifacts, so a downstream underwriting engine can replace valuation
constants it previously had to assume:

| Artifact | What it is | Constant it exists to replace |
| --- | --- | --- |
| `outputs/cost_of_capital_anchor.json` | The market's cost of **equity**: decomposed risk-free curve, implied ERP, sector risk loadings | `SECTOR_WACC`, `DEFAULT_WACC`, `UTIL_COE`, `FIN_COE` |
| `outputs/long_run_growth_anchor.json` | Long-run **nominal** growth from observed potential output + market inflation expectations | `TERMINAL_G = 0.025` |
| `outputs/sector_multiple_bands.json` | Regime-conditional justified-multiple bands + a Gordon arithmetic cross-check | unconditional multiple thresholds |

Configuration: `config/anchors.yaml`. Orchestration: `src/macro_engine/anchors/service.py`.
CLI: `build-anchors`, `build-cost-of-capital-anchor`, `build-growth-anchor`,
`build-multiple-bands`.

---

## 0. The two rules that govern every number here

**Rule 1 — a leg that cannot be measured is `null`, never a plausible number.**
The payload sketch in the specification types every leg as `float`. They are
`float | None` in `anchors/models.py`, because "we could not measure this" and "this
measured zero" are different facts and a consumer must be able to tell them apart.
`degraded` plus `provenance.degradation_reasons` name what was missing.

**Rule 2 — provenance travels with the value.**
Every payload carries `asof`, `built_at`, the observation date of each input series, the
scoring mode used, and a loading/panel/ERP source label. A stale input is therefore
visible at the point of consumption rather than ageing silently.

Anchors are **deterministic and LLM-free**. An anchor that cannot be reproduced cannot be
audited, and an anchor that is not audited has no business setting a discount rate. LLM
use in this engine is confined to the news path.

---

## 1. Why a cost of EQUITY, and never a WACC

The downstream cash-flow definition this anchors is

```
base_cf = net income + D&A − capex
```

discounted against **market capitalisation**. Net income is already net of interest, so
`base_cf` is a **levered** flow. Pairing a levered flow with a firm-level WACC and an
enterprise value subtracts the debt claim twice. That was tried downstream on
2026-08-07, proven to double-count, and reverted (`valuation_backbone.py`, `SECTOR_WACC`
comment). Re-introducing WACC here would be a regression, so this anchor is a cost of
equity by construction and publishes no firm-level rate.

MRI also does **not** touch sector spreads. The consumer measured that spreads carry
roughly 15 tier flips of ranking information while level shifts are ranking-neutral
(ρ ≥ 0.995, audit C1). So MRI owns the **level**, publishes **loadings** as information,
and leaves the spread structure where it is.

---

## 2. Units contract

FRED quotes every yield and spread in **percent**; this package works in **decimal
fractions** end to end (the consumer's constants are decimal: `TERMINAL_G = 0.025`).

The conversion is declared per series in `config/anchors.yaml`:

```yaml
nominal_10y: {series: DGS10, units: percent}
```

and applied once, at the single point of entry (`SeriesRef.to_decimal`). This is not
ceremony: an earlier revision of this layer compared a percent-quoted yield against
decimal thresholds and published a **445% "10-year yield"**. Declaring the convention per
series makes that class of error impossible to introduce silently, rather than relying on
whoever adds the next series to remember.

Arithmetic identity worth asserting (and asserted in
`tests/test_anchor_cost_of_capital.py`): when the three legs come from the same vintage,
`nominal_10y − real_10y == breakeven_10y`. A unit or series mix-up breaks this by orders
of magnitude.

---

## 3. Cost-of-capital anchor

### 3.1 Risk-free decomposition — FRED-observable

| Leg | Series | Notes |
| --- | --- | --- |
| nominal 10y | `DGS10` | |
| real 10y (TIPS) | `DFII10` | |
| breakeven 10y | `T10YIE` | |
| term premium | `THREEFYTP10` (Kim-Wright) | else the computed proxy below |

**Series IDs verified against the live FRED API, 2026-09-20.** The NY Fed ACM term premium is
**not published on FRED** — searches for "term premium", "ACM term premium" and "Adrian Crump
Moench" return zero hits, and the earlier `ACMTP10` entry in this config was an unverified
guess that resolved to HTTP 400. `THREEFYTP10` is available and current, but is **daily**, not
monthly as first configured. Both facts were established by measurement, not assumption.

`term_premium_source` always names which was used: `observed:<SERIES>` or
`proxy:<NOMINAL>-<SHORT>`. The proxy is `DGS10 − DTB3` — a constant-short-rate expectations
benchmark. It is crude **by construction** and is labelled as a proxy in the payload and in
`degradation_reasons`; it is never presented as an observed term premium.

### 3.1a Curve consistency — the decomposition must describe ONE day

Three legs read independently can span different vintages, and a curve assembled from
different days is not a curve. This is not hypothetical: a partial ingest left `DGS10` four
months stale while `DFII10` and `T10YIE` were current, producing
`nominal − real = 1.84%` against a published `breakeven` of `2.33%`.

Two independent tests run on every build (`cost_of_capital.risk_free.consistency`):

* **Date spread** — the legs' observation dates must fall inside
  `max_leg_date_spread_days`. A breach is a data-freshness failure and is reported.
* **Breakeven identity** — `nominal − real` must reproduce the published breakeven within
  `breakeven_tolerance`. FRED's breakeven is fitted from the TIPS curve, so a few bp of
  timing/liquidity noise is normal; a wider gap means the numbers disagree about the same day,
  which no amount of freshness explains. This one **degrades the anchor**.

As published the three legs now reconcile exactly: `4.94% − 2.61% = 2.33%`.

### 3.2 Inflation backdrop

`cpi_yoy` and `pce_yoy` are year-over-year changes of `CPIAUCSL` and `PCEPI` index
levels. A non-positive index level is refused rather than converted into a rate: it is
corruption, not data.

### 3.3 Equity risk premium — needs an equity-side input

MRI holds **no equity data**. The implied ERP (solve the discount rate at which an
aggregate DCF equals aggregate market capitalisation, Damodaran-style) therefore requires
a file the operator supplies:

`cost_of_capital.erp.equity_aggregate_path` → a JSON object:

```json
{
  "asof": "2026-04-30",
  "source": "human-readable provenance",
  "growth": 0.045,
  "coverage_share": 0.85,
  "constituents": [
    {"ticker": "AAA", "cash_flow": 1.2e9, "market_cap": 3.0e10, "growth": 0.06}
  ]
}
```

The implied rate is solved by bisection over the same two-stage fade-to-terminal shape the
consumer's `dcf_value()` uses (re-implemented here, never imported — MRI must not depend on
its consumer). Two properties are load-bearing:

* **The search domain starts strictly above `terminal_growth`.** A Gordon terminal value is
  undefined at or below perpetual growth, so a configured lower bound beneath it names a
  rate at which the model does not exist. Clipping *restricts* the domain; it never widens
  the search to manufacture an answer.
* **ERP = solved rate − observed nominal 10y.** Subtracting the observable is what makes it
  a premium rather than a second discount rate in disguise.

Withheld, with the reason stated, when: fewer than `min_constituents` usable constituents,
coverage below `min_coverage_share`, no risk-free reference, or no root inside the valid
domain.

`erp_percentile_vs_history` additionally needs a documented, versioned ERP history
(`cost_of_capital.erp.regime_estimate.history_path`, long-format CSV `date,erp`). This is
used **only** to place today in a percentile context. It is never presented as an implied
value.

**The history is real and sourced, not recalled.** `scripts/fetch_erp_history.py` downloads
Aswath Damodaran's published *Historical Implied Equity Risk Premiums* workbook and extracts
the `Implied ERP (FCFE)` column — 65 annual observations, 1961–2025. The parsed CSV is what the
anchor reads, so the vintage is pinned in the repo rather than depending on a live scrape:

| | |
| --- | --- |
| observations | 65 (1961–2025) |
| p10 / p25 / median / p75 / p90 | 3.04% / 3.48% / **4.10%** / 5.08% / 5.86% |
| range | 2.05% – 6.45% |
| latest (2025) | 4.23% |

### 3.3a `erp_basis` — a universe solve is not a market solve

`scripts/build_equity_aggregate.py` builds the aggregate from the screener corpus with three
filters, each for a stated reason rather than tidiness:

| filter | why |
| --- | --- |
| **USD reporters only** (`FX is None`) | **Correctness, not hygiene.** The solve sums cash flows and compares them to market caps; mixing a TWD or EUR cash flow into a USD aggregate is meaningless. The corpus marks some foreign reporters `converted: True` and others `unreviewed_currency_mismatch` with conversion explicitly unproven, so no foreign reporter's fundamentals can be assumed to be USD. **496 names excluded.** |
| **Share classes collapsed by CIK** | `GOOG` ($4.04T) and `GOOGL` ($4.01T) are the same company and each figure is the TOTAL issuer capitalisation, so summing them double-counted Alphabet by ~$4T. Largest class kept. **236 classes collapsed**, corpus $90.79T → $85.29T. |
| **Positive owner earnings only** | `NI + D&A − capex` below zero has no capitalised value to aggregate, and a negative "cash flow" drags the solved rate toward nonsense. |

Built: **1,676 constituents** covering **$65.56T = 76.9%** of the collapsed universe's
capitalisation (floor: 60%).

**The label is load-bearing.** The solved rate is a **universe-implied** cost of equity, not a
market-implied one — the universe is a screener corpus, not an index. So:

* `erp_basis` is `"universe"`, published beside the value.
* `implied_erp` / `implied_cost_of_equity` carry the result under basis-neutral names.
* **`market_implied_erp` and `market_implied_coe` stay `null`**, reserved for a true index
  aggregate. A consumer reading "market_implied" must be able to trust the word.

  **BLOCKED, and by what.** Filling them needs constituent-level index data this repository does
  not hold and cannot derive: float-adjusted weights, index membership history, and trailing
  aggregate earnings for a licensed index. The corpus that IS held is a screener universe
  (1,676 constituents after collapsing 236 share classes by CIK, 76.9% of the collapsed corpus by
  market cap, 496 non-USD reporters excluded because their multiples would be currency-mixed).
  Substituting it for an index would put a universe number under a market label — the exact
  mislabelling this section forbids — so the fields stay null until the data is licensed.
  Unblocking it is a data-acquisition decision, not an implementation task.

As published at 2026-09-20: **implied ERP 4.24%, implied cost of equity 9.18%**.

**External sanity check.** The solved 4.24% sits between the Damodaran history's median (4.10%)
and its latest observation (4.23%) — two entirely independent constructions agreeing to within
a basis point on the level of the US equity risk premium. That is the strongest available
evidence that the universe solve is realistic rather than an artefact of the corpus.

**A divergence worth naming.** The anchor's 9.18% is materially below RS2's self-referential
calibration (12.77%), and the reason is methodological, not a disagreement about the market:
`build_coe_calibration` aggregates **net income**, while the engine discounts
**`NI + D&A − capex`**. Owner earnings exceed net income whenever D&A exceeds capex, so the same
market cap is justified by a lower discount rate. The self-referential calibration was solving
for a rate that made a DIFFERENT cash-flow definition match market cap — the same class of
incoherence as the terminal-growth mismatch, and the anchor resolves it by using the definition
the engine actually discounts.

### 3.3b `erp_source: regime_estimate` — the documented degraded ERP

With no equity aggregate there is no implied ERP to publish. The anchor's documented fallback
is to publish the **historical distribution** instead, and to say so:

* `erp_source` becomes `regime_estimate`.
* `erp_history` carries `n`, `start`, `end`, `p10/p25/median/p75/p90`, `latest`, `min`, `max`.
* `erp_history_source` names the provenance.
* **`market_implied_erp` stays `null`.** This is the load-bearing distinction: a historical
  estimate and an implied value are different objects, and conflating them would put an
  unmeasured number into a discount rate. `market_implied_coe` stays null with it, so a
  consumer cannot mistake the history for a level.

With the curve complete and the ERP published as a regime estimate, the cost-of-capital anchor
is **not degraded** — every leg it promises is published in a labelled form. The consumer's
adoption gate still refuses the level, because `market_implied_coe` is what it asks for.

### 3.4 Sector risk loadings — gated on declared provenance

Loadings are equity beta vs the benchmark, measured from the sector ETF panel the
validation layer already loads (`config/sector_validation.yaml`), over
`window_years`, requiring `min_return_observations`.

They are **gated**: `cost_of_capital.sector_loadings.price_panel_source` must be
`stored_sector_proxy_prices` before anything is derived. The gate was written because the
panel in this working copy was **synthetic**, measured as: realised SPY volatility of 5.1%
annualised (real ≈ 16–20%), a *rising* SPY through the March 2020 crash (363 → 374 while the
real index fell from ~298 to ~222), and an XLE/SPY return correlation of −0.12 (real ≈ +0.6).
It was gitignored and byte-identical to `data/sector_proxy_prices_sample.csv`. A beta computed
from it is not evidence, it is a number shaped like evidence.

**Now enabled, on a verified real panel.** Stooq — the configured provider and CI's source —
began serving an HTML JavaScript bot-challenge on every endpoint (`stooq.com`, `stooq.pl`,
with and without a date range), so `scripts/fetch_sector_proxy_prices.py` refreshes the panel
from Yahoo's public chart endpoint into the same `ticker,date,close` CSV that MRI's existing
`csv` provider already reads. No provider code changed and the committed `provider: stooq`
preference is untouched. The fetcher **refuses to write** a panel whose benchmark volatility
is implausible, so the synthetic file cannot be reintroduced by accident.

Verified panel: 108,247 rows, 18 tickers, 1998-12-22 … 2026-09-18, SPY annualised vol
**19.2%**, worst drawdown **−56.5%**, largest daily move 14.5%. Published betas (5y vs SPY)
are plausible: semiconductors 1.74, consumer discretionary 1.73, information technology 1.37,
consumer staples 0.36, health care 0.59.

Two measurement notes that cost bugs:

* **`ddof` consistency.** pandas' `cov()` defaults to `ddof=1`. Pairing it with `var(ddof=0)`
  inflates beta by a silent `n/(n−1)` — negligible at n=400, material on a thin sample. Both
  legs now use the same `ddof`.
* **A refresh must replace the whole ticker.** `upsert_sector_proxy_prices` deleted on
  `(ticker, date)`, so refreshing the synthetic panel with real prices left the OLD values on
  market holidays — the days the new source has no row for. The stitched series printed SPY
  **+83% on Juneteenth 2022** and 62% annualised volatility. The delete is now scoped by
  ticker, making a refresh authoritative.

---

## 4. Long-run growth anchor

**v0.3 (S2, 2026-09-23) — the regime label was removed from this computation.** The
published regime (`goldilocks` 0.287, over `reflation` 0.243 — a contested call) was
moving a *perpetual* growth rate through `regime_sensitivity`, and the label changes
every ~13 months on the clean timeline. `P0_0_MRI_TARGET_ARCHITECTURE.md` §5 measured
that the trend-only channel, taken at the single latest observation, is **not** slow
enough to replace it outright (89 rung changes in 268 months, one of 225 bp, in the
2009-01 TIPS-liquidity collapse) — so v0.3 both removes the label AND smooths the leg
it now depends on entirely:

```
real_potential            = log-linear OLS trend of log(observed potential output), annualised
inflation_expectation_12m = trailing 12-calendar-month mean of the monthly mean of T5YIFR,
                             else T10YIE (inflation_expectation_spot is the single latest
                             observation, published beside it, consumed by nothing)
nominal_gdp_trend         = real_potential + inflation_expectation_12m
raw_trend_g               = nominal_gdp_trend × max_share_of_nominal_trend        (unchanged, 0.85)
candidate_rung            = round(clamp(raw_trend_g, floor, ceiling) / round_to) × round_to
rung_state                = advance_rung_state(candidate_rung, prior published rung_state)
terminal_g_suggestion     = terminal_g_rung = rung_state.current_rung
delta                     = terminal_g_suggestion − downstream_prior_in_use
```

`advance_rung_state` and `resolve_growth_rung_state` (`src/macro_engine/anchors/growth.py`)
implement a dead-band + N-consecutive-monthly-build candidate-confirmation rule: the rung
changes only once the clamped raw trend is beyond the *far* edge of the current rung
(`abs(raw − current) > round_to`, not merely past the midpoint — that distinction is
specifications B vs C below) **and** that specific candidate rung holds for
`growth.rung.confirm_months` consecutive monthly builds (default 3, specification E, the
operator's ruling on §10 Q2; `confirm_months: 1` is specification C). Under candidate
confirmation (`growth.rung.rule: candidate`), if raw departs but the candidate rung shifts from
one month to the next, confirmation resets for the new candidate. To make a held rung visible
when raw moves faster than confirmation, `rung_state.gap_bp` publishes `round((raw − current) × 1e4)`.

State (`current_rung`, `candidate_rung`, `months_confirmed`, `last_change_date`, `changes_last_10y`,
`gap_bp`, `rule`, `method_version`) is persisted in `anchor_runs.long_run_growth_json.rung_state`
and read back by `anchors/service.py` on every build. When prior state lacks `rule == "candidate"`
and `method_version == 1`, state is seeded by historical replay from `growth.rung.replay_start`
(`2004-06-01`). Any missed calendar months between builds are caught up in sequential calendar
order, so a multi-month gap between builds arrives at the same state as consecutive monthly builds.

**Measured (`scripts/measure_growth_rung_path.py`), 2004-06 → 2026-09, 268
months, on the live store (month-start grid, complete months inflation leg):**

| specification | rung changes | largest single change | largest gap (raw − published rung) | changes last 10y | today's rung |
| --- | --- | --- | --- | --- | --- |
| B (smoothed leg, no dead-band, naive round-to-nearest) | 19 | 25 bp | 25 bp | 4 | — |
| C (`confirm_months: 1`, dead-band only) | 11 | 25 bp | 25 bp | 3 | 3.75% |
| E (`confirm_months: 3`, candidate confirmation, adopted) | **9** | **75 bp** (2009-06) | **69 bp** (2009-05) | **3** | **3.75%** |
| Shipped variant (`confirm_months: 3`, departure confirmation) | 10 | 50 bp | 48 bp | 3 | 3.75% |

Specification E exactly reproduces the target architecture's §5.2 9-date path (including the 75 bp
step from 4.50% to 3.75% in 2009-06, 3 changes in the last ten years, and today's rung at 3.75%).
An earlier report observed 10 changes and attributed the difference to FRED revisions accrued in
the intervening months; measurement shows that the difference was purely mechanical:
1. Candidate confirmation vs departure confirmation: candidate confirmation requires the
   *candidate* rung to hold for 3 consecutive months, whereas departure confirmation only
   required raw to stay departed from the current rung for 3 months regardless of candidate shifts.
   When raw moves rapidly (e.g. 2008-2009), the candidate changes frequently, so the rung holds
   longer and releases in a larger step.
2. Grid definition: evaluation on a month-start grid (first calendar day of month) using only
   complete calendar months for the inflation leg (`[M-12mo, M-1mo]`) avoids partial-month leakage
   and future timestamps in provenance.

Sources: `GDPPOT` (CBO Real Potential GDP, quarterly) for the real leg; `T5YIFR` with
`T10YIE` as the documented fallback for the inflation leg. Both are observed series,
which makes the **entire** growth anchor FRED-provable — the one anchor with no
equity-side dependency at all.

**A trap worth naming: `GDPPOT` carries projections.** FRED publishes CBO's Real Potential GDP
out to roughly a decade ahead — the stored series runs to **2036**. Before the as-of cap, the
newest stored observation *was* that projection, the build dated itself **2036-10-01**, and
every `date <= as_of` filter silently admitted projected values. The anchor would have
restated CBO's forecast while presenting itself as an observation. An anchor describes what IS
KNOWN, so `_resolve_as_of` now caps at today.

**Why the trend is fitted, not differenced.** An endpoint CAGR lets one bad quarter define
a decade. Measured on a synthetic 2%/yr series with a 30% spike in the final quarter: the
fitted log trend moves by well under 0.5pp while the endpoint CAGR over the same window
moves by ~2.7pp (`test_trend_is_not_dominated_by_endpoint_noise`).

**Why a UNIFORM terminal rate is right.** No company outgrows its economy in perpetuity, so
a single perpetual rate across issuers is physically correct. The defect was never the
cross-sectional uniformity — it was that the constant had no source, no version and no
revision path *while feeding every issuer's `expectations_gap_pts`* through the implied-growth
solver. A wrong value there shifts every name in the same direction, which is precisely why
this ships sourced, versioned, revisable, and with its delta against the constant it
replaces disclosed.

**`regime_sensitivity`, `regime_applied`, `regime_adjustment` are v0.3 DEPRECATED.**
Always `{}` / `null` / `null` now — the label has no code path to any field of this
anchor (enforced by `tests/test_anchor_service.py`, which flips the stored regime label
and rebuilds the whole bundle). Kept for one release so a v0.2 reader does not see the
keys disappear outright; `rs2_data.anchor_terminal_g()` reads only
`terminal_g_suggestion`, `degraded` and `asof`, so it is unaffected either way. See
`LongRunGrowthAnchor.deprecations` and `provenance.regime_leg =
{"used": false, "reason": "label_channel_removed_v0.3"}`.

**P0.3 (anchors disclose and degrade on a stale regime leg) — where it still applies.**
The growth anchor has no regime leg left to go stale (`provenance.regime_leg.used ==
false`). `sector_multiple_bands.json`'s `regime_state` buckets still condition on the
dimension-derived macro state (§5, unchanged by S2), so *that* payload's
`provenance.regime_leg` carries `{date, regime_or_state, age_days}` from the state row
actually used, and the payload goes `degraded: true` with a
`regime_leg_stale:<date>` reason once `age_days` exceeds `regime_status.
CURRENT_REGIME_MAX_AGE_DAYS` (45, the one owner).

---

## 5. Regime-conditional multiple bands

**CLOSED 2026-09-20.** This section previously documented an open gap: the market-observed leg
needs a dated, sector-tagged valuation panel, MRI held none, and the local sector ETF file is
`ticker,date,close` — a price-only series cannot produce a P/E. Substituting a price-relative
statistic and calling it a multiple would have been fabrication, so the leg published
`panel_source: unavailable`.

The gap is now closed **from data the shop already had**, via
`scripts/build_valuation_panel.py`:

| source | use |
| --- | --- |
| `stocks.csv` | ticker → sector (Yahoo → MRI sector id, total over the real vocabulary) |
| `fundamentals_history.json` | annual `net_income` / `shares_diluted` by fiscal year → EPS |
| `backtest_prices.json` | monthly prices, ~2014 onward |

Built panel: **325,279 ticker-months, 3,492 names, all 11 sectors, 150 months (2014-01 …
2026-06)**. Read by the existing `external_valuation_panel` loader — no new provider code.

### What this panel IS, and is not

Every one of these is a real limitation, published on the payload in `measure`,
`measure_definition` and `panel_meta.caveats`, not buried here:

* **Trailing, not forward.** `price / (net income per diluted share of the most recently
  PUBLISHED fiscal year)`. The quantiles live under the key `ntm_pe` because that is the
  published contract, but the payload's `measure` field says `pe_ttm`. A trailing number under
  a forward-sounding key without a label would be exactly the mislabelling these anchors exist
  to remove.
* **Annually stepped earnings.** EPS changes only when a fiscal year is reported, so within-year
  variation is price movement. Sector medians are meaningful; one name's month-to-month
  "multiple change" is mostly its price.
* **A conservative 90-day-equivalent publication lag.** Fiscal years are labelled by end-year
  with no period-end date stored, so FY(N) is treated as first usable in March of N+1. For a
  December year-end that is about right; for a June year-end it is deliberately late. The rule
  can only make the figure **staler**, never leak it early.
* **Survivorship.** The universe is today's listings, so delisted names are absent.
* **Loss-makers are excluded** rather than assigned a multiple; non-positive EPS yields no P/E,
  and multiples above 400x are dropped as near-zero-earnings artefacts. This lifts the measured
  multiple most for CYCLICAL groups at a trough, where the excluded names are precisely the ones
  earning nothing: `semiconductors` reads a 71x median while `banks` reads 12.9x. Read a cyclical
  band as "the multiple of the names still earning", not as the sector's multiple.

None of this is fatal to a regime-conditional **distribution**, which is what is published and
which needs the cross-section to be representative month by month. It is fatal to treating any
single row as a precise valuation.

### The panel is hierarchical, and now covers every tracked sector

The six sub-industries (`banks`, `biotech`, `homebuilders`, `oil_gas_ep`, `semiconductors`,
`software`) were tracked, enabled and consumed like any other sector, but a Yahoo **Sector** value
can never produce one of their ids — so they were permanently unmeasurable while the anchors
faithfully reported `bands: no panel observations for sector banks` six times over. The finer
vocabulary was in the same corpus all along, in its `Industry` column (159 distinct values), so the
gap was a mapping gap, not a data gap.

The builder now maps both levels and emits a ticker under **both** ids: `banks` is its own band
while `financials` remains the sector aggregate containing it. Emitting only the finer id would
silently shrink the parent; only the parent would leave the sub-industry empty. The relationship is
published in `panel_meta.subindustry_parents`, with a caveat stating that the two levels are
overlapping samples rather than disjoint ones.

Coverage after the rebuild: **17 of 17 tracked sectors, 390,947 rows, 3,492 names, 150 months**
(2014-01 … 2026-06), up from 11 sectors and 325,279 rows.

### Published bands

Current macro state `growth=strong, inflation=high, rate_band=high, credit=neutral`, fully
conditioned, 154–1,351 observations per cell:

| sector | p25 | median | p75 |
| --- | --- | --- | --- |
| financials | 9.32 | **12.45** | 17.60 |
| communication services | 5.65 | **14.81** | 39.94 |
| energy | 8.52 | **16.08** | 29.28 |
| consumer staples | 8.32 | **17.79** | 28.32 |
| consumer discretionary | 9.75 | **17.55** | 33.74 |
| health care | 6.97 | **19.29** | 35.61 |
| materials | 11.87 | **19.55** | 33.14 |
| utilities | 16.65 | **20.21** | 23.85 |
| real estate | 10.56 | **22.46** | 43.52 |
| industrials | 12.78 | **24.20** | 45.31 |
| information technology | 13.86 | **29.56** | 66.52 |

The six **sub-industries** now band from the same fully-conditioned rung as the sectors above
(§"The panel is hierarchical"): banks median 12.9, biotech 9.5, homebuilders 11.2, oil & gas E&P
13.7, software 26.7, semiconductors 71.3 — each measured over its own names, not inherited from a
parent sector.

**The arithmetic leg is always computed** when the cost-of-capital and growth anchors are
available, and it survives when the observed leg does not. It is a Gordon justified multiple,
`payout / (CoE − g)`, and it withholds itself when `CoE − g` falls below `min_spread`, because a
collapsed denominator describes the Gordon form breaking down, not a real multiple. It is
reported in a separate field (`arithmetic_check`) so an arithmetic identity can never masquerade
as a market observation.

---

## 6. Point-in-time integrity (ALFRED vintages)

### 6.1 The problem

`calendar_asof` answers *"which observation period does this date refer to?"* and then
subtracts a fixed per-series `publication_lag_days` approximation. The approximation is the
weak link: real release lags vary by series and by episode, and a benchmark revision or a
re-seasonalisation rewrites history that the calendar rule cannot see.

### 6.2 The mechanism

Vintages live in their own table, `raw_observation_vintages`, keyed on the full vintage key
`(series_id, date, realtime_start, realtime_end)`. They are deliberately **not** stored
alongside the revisioned observations: `upsert_raw_observations()` deletes on
`(series_id, date)` by design (latest fetch wins), so the daily ingest would delete every
stored vintage of any period it touched. Separate tables make the point-in-time history
unreachable from the daily path — non-breaking by construction rather than by discipline.

* `FredClient.get_series_observations_vintage(series_id, as_of)` sends
  `realtime_start = realtime_end = as_of`, which is the actual question: *what was
  published then*, not *what do we believe about then now*.
* Resolution is **"the newest vintage with `realtime_start <= as_of`"**, not a bracket test.
  The bracket test (`realtime_start <= D <= realtime_end`) is wrong in practice: ALFRED clips
  `realtime_end` to the query's `realtime_end`, so a single-day vintage comes back with
  `realtime_start == realtime_end ==` that day, and the bracket matched **only** the exact
  backfilled dates — every other evaluation date silently resolved to nothing. The rule as
  implemented also gives the right answer for unclipped vintages, where several vintages of
  the same period coexist.
* `evaluation/asof.py::build_publication_index` recovers the **empirical**
  first-publication date of every observation as `min(realtime_start)`. Resolution is the
  granularity of the backfilled as-of dates — exactly the granularity the evaluation
  calendar asks about.
* `scoring_mode: point_in_time` replaces the fixed lag with that measured date.
* `scripts/backfill_vintages.py` and `ingest-fred-vintages` fetch only the vintages the
  diagnostic calendar actually uses. Two practical constraints, both learned by running it:
  the default series set is **every enabled source**, because PIT mode gates the *feature*
  series and an anchor-only backfill would report `pit_vintage_missing` everywhere; and each
  fetch is bounded by an observation window, because otherwise a vintage returns the whole
  history (~16,000 rows per request for a daily series) and the run stalls without storing a
  row.

### 6.2a Verified against live ALFRED

First verification window (2024-01 onward); superseded by the full-history backfill in §6.2b.
Backfilled and validated 2026-09-20: **20 series × 29 as-of dates = 155,881 vintage rows, 0
failed fetches** (2023-01-01 onward observation windows).

**The revision the property exists for, measured.** March 2024 non-farm payrolls, as published
on each date:

| as-of | PAYEMS 2024-03 as published | CPIAUCSL 2024-03 as published |
| --- | --- | --- |
| 2024-06-15 | 158,111 | 312.230 |
| 2025-06-15 | **157,517** | 312.107 |
| 2026-05-01 | **157,466** | 312.345 |

The annual benchmark revision rewrote March 2024 by **−645 thousand jobs**, and the
re-seasonalisation moved the CPI level — while a calendar-as-of read would have returned
157,466 for all three dates. Point-in-time resolution returns what was actually published.
**Zero look-ahead rows** in every case checked.

`point_in_time` remains **opt-in and not enabled**. Flipping it is one config line, but it
moves the factual basis of every historical diagnostic, so it is a judgement call backed by a
validation window rather than a default.

### 6.2b Full-history backfill and the PIT cutover, measured

Completed 2026-09-20: **20 series × 437 monthly as-of dates (1990-01 .. 2026-05) = 8,579,643
vintage rows, 0 failed fetches**. Three properties of the run are worth recording, because each
one cost a failed attempt to learn:

* **ALFRED latency is uneven per request, not per series.** The same series returned a 2015
  vintage in 0.29 s and a 2018 vintage in 17–19 s (HTTP 200, no rate-limit headers). A serial
  loop therefore ran at the speed of its worst request — ~4 requests/minute — which is how a
  two-hour backfill became a day-long one. Fetches are now pooled (`--workers`, paced at 100
  requests/minute against FRED's documented 120) and writes stay serial per series.
* **"The series does not exist in ALFRED" is not a failure.** For a date before a series' archive
  begins, ALFRED answers HTTP 400, not an empty list. Counting that as a failed fetch produced
  3,154 phantom failures that buried the real ones, and contradicted the contract
  `get_series_observations_vintage` already documented. It is now `NoVintageAvailable` →
  an empty vintage.
* **Archive depth is not uniform, and it decides the cutover.** Measured first-vintage date per
  series: CPIAUCSL / HOUST / INDPRO / M2SL / PAYEMS / UNRATE from 1990; GDPPOT 1991-02; FEDFUNDS
  1997-01; PCEPI 2000-08; DGS10 and DTB3 2005-07; DFII10 2005-11; ICSA 2009-06; NFCI 2011-06;
  BAA10Y / T10Y2Y / T10YIE / T5YIFR 2014-02; THREEFYTP10 2016-06; BAMLH0A0HYM2 2023-10 **as of
  that backfill run**. Unlike every other series above, BAMLH0A0HYM2's start date is not a fixed
  archive floor: FRED serves ICE's high-yield OAS on a rolling ~3-year window that advances with
  the current date, not a fixed backfill limit. Re-measured 2026-09-22 (S0.2): a fresh unbounded
  fetch now starts 2023-09-22, having started 2023-05-15 when last fetched 2026-05-14 — the same
  ~4-month roll as the elapsed time between those two fetches. Treat any first-vintage date quoted
  for this series as a snapshot, not a constant, and re-measure before relying on it.

`scripts/validate_pit_vs_calendar.py` builds the anchors twice at each as-of date — once per
scoring mode, `write=False` so no published artifact is touched — and reports every leg that
moves. Results:

| as-of | legs that move | point-in-time outcome |
| --- | --- | --- |
| 1995-06-30, 2000-06-30 | 4/10 | risk-free curve **unmeasurable** (`not_yet_published`); ERP undefined |
| 2008-06-30 | 10/10 | breakeven unmeasurable (T10YIE archive starts 2014-02) → ERP solve unavailable |
| 2015-06-30 | 8/10 | fully measurable; term premium falls back to the DGS10 − DTB3 **proxy** |
| 2020-06-30, 2024-06-30 | 7/10, 8/10 | fully measurable, `degraded=False` |
| 2026-09-20 | 10/10 | fully measurable |

Three findings decide the cutover:

1. **The revision effect is real and points the right way.** At 2024-06-30 CPI yoy reads
   **3.36% as published** against **2.97% as revised** (+39 bp). Inflating a 2024 regime
   classification with a base that was rewritten afterwards is precisely the look-ahead this
   mode removes.
2. **PIT is only usable from 2014-02.** Before 2005 the risk-free curve does not exist in the
   archive at all; between 2005 and 2014-02 the breakeven leg is missing, which removes the ERP
   solve. Before 2016-06 the observed ACM term premium is unavailable and the anchor silently
   changes *measurement* (proxy instead of observed). A calendar/PIT hybrid with a documented
   2014-02 boundary is the honest configuration; a blanket switch would gut the pre-2014
   diagnostic.
3. **PIT inherits the vintage calendar's freshness.** The newest stored vintage is the
   evaluation calendar's last date, 2026-05-01. Asking for a present-day read therefore
   resolved DGS10 to 2026-04-30 against the calendar read's 2026-09-17 — a −54 bp difference
   that is **staleness, not revision**. Running PIT in production requires fetching the current
   vintage on every pipeline run, not only a monthly backfill.

### 6.2c Keeping the archive current, and what the basis changes in the regime layer

Three things had to be true before point-in-time could be the shipped basis rather than a
validation tool, and each was a defect found by trying:

* **The archive must reach the present.** `vintage_asof_dates` now adds TODAY and the newest stored
  observation date to the evaluation calendar's month starts. Without them the newest vintage was
  the calendar's last month start (2026-05-01) and a present-day build resolved the 10-year to
  2026-04-30 — the −54 bp of §6.2b, i.e. staleness wearing the label "as published".
* **Negative answers must be remembered.** ALFRED answers "the series does not exist in ALFRED" for
  every date before a series' archive begins, and nothing recorded it, so each run re-asked all
  **3,154** dead pairs: ~30 minutes at the 100 requests/minute pace, for zero new rows, every run.
  They are now stored in `vintage_absence` (labelled `no_vintage_before_first_archive` when
  derived from the archive's shape rather than from asking) and skipped by `resume`. A refresh of
  the live archive went from ~32 minutes to **44 seconds**.
* **The daily pipeline must refresh them.** A `vintages` step runs before `anchors` whenever the
  configured basis is point-in-time, and the anchor build carries a **staleness gate**: if the
  newest visible vintage is more than `point_in_time_max_lag_days` (7) before the as-of, all three
  payloads degrade with a reason naming the lag. A stale basis is reported, never inferred.

`scripts/validate_regime_basis.py` measures what the basis changes BELOW the anchors: it runs the
`build-asof-features → build-dimensions → build-regimes` chain twice over identical stored data —
once on a copy with `calendar_asof`, once on the live store with the shipped hybrid — and compares.
Result over 437 evaluation dates:

| measure | value |
| --- | --- |
| as-of feature cells | 7,429 |
| cells whose value differs | 387 |
| cells withheld under point-in-time | 39 (all from 2014-03 onward) |
| cells point-in-time alone can evidence | **0** |
| regime labels / probabilities / confidences that differ | **0 of 17 timeline columns** |

Two things follow. Point-in-time is **strictly more conservative** — it never produces a value the
calendar rule cannot, and withholds 39 cells the approximation would have supplied. And the regime
timeline is unmoved: the 387 value changes are absorbed by the softmax without flipping a single
dominant or reported regime. So the historical diagnostic's conclusions are unchanged while its
inputs are now evidenced, which is the outcome that justifies the switch.

### 6.2d The resolver must be told which series it is answering about

The first validation run published **294.43 as the 10-year nominal Treasury yield**, and
−294.33 as the implied ERP. The anchor builders handed `point_in_time_observation` the entire
`raw_observation_vintages` table while every calendar-mode caller filtered its own series first;
with ~20 series sharing one as-of calendar, "the newest observation in the newest vintage"
returned whichever series sorted last. Every value was well-formed, so nothing downstream could
detect it.

`evaluation/asof.py::_single_series` now narrows to a named series and **raises** when it is
handed a multi-series frame without one — a caller that cannot say which series it means cannot
be answered correctly, and silence is indistinguishable from a measurement. All anchor call
sites name their series; `tests/test_pit_no_lookahead.py` pins both the refusal and the
single-series path.

### 6.3 The failure mode this avoids

An opted-in point-in-time read with no stored vintages returns
`(None, "pit_vintage_missing")` — it does **not** silently fall back to the approximation it
was chosen over. `tests/test_pit_no_lookahead.py` proves the property on both revision
patterns: a PAYEMS-style benchmark revision (as-of before the revision returns 156,400;
as-of after returns 155,300) and a CPI-style re-seasonalisation (the as-published yoy uses
the as-published base). It also asserts the *leak* in the calendar rule, rather than
pretending the old rule was safe.

`point_in_time` is opt-in. Switching it silently would move the factual basis under every
existing historical diagnostic; `calendar_asof` remains the default.

### 6.4 As-of normalisation

Every stored date in this engine is a naive calendar `DATE`. An as-of value that arrives
tz-aware — trivially produced by `datetime.now(UTC)` — cannot be compared with those
columns at all. `evaluation/asof.py::normalize_asof` normalises once at the boundary so a
tz-aware value reaching a comparison is impossible rather than unlikely.

---

## 7. Deliberate non-changes

* **`outputs/current_regime.json` carries a synthetic future date** (observed
  `2031-08-01`). The anchors date themselves from the stored observation history
  precisely to avoid it, but the defect itself is **recorded, not fixed**. Scope
  discipline: it is a separate finding, listed in `rs2_repair_package.json`
  (`known_findings`).
* **No features were added for the new series.** The spec suggested "corresponding
  features". Features exist to feed dimensions; the anchor layer consumes raw **levels**,
  so z-scored features are the wrong instrument — and adding unreferenced features would
  perturb stored feature/health outputs for no consumer. The seven new sources are
  `required: false`, referenced by no dimension and no regime, so they cannot move the
  regime classification.
* **No MRI constant was changed.** `SECTOR_WACC` and friends are the consumer's; editing
  them here would violate the ownership boundary.

---

## 8. Regression tests

| File | What it pins |
| --- | --- |
| `tests/test_anchor_cost_of_capital.py` | decomposition, units, the breakeven identity, solve convergence, domain clipping, withheld-ERP statuses, the loading provenance gate, beta correctness |
| `tests/test_anchor_growth.py` | component arithmetic, trend recovery, endpoint-noise resistance, the trailing-12-month smoothing, the dead-band + confirmation rung state machine (specifications C and E), clamps, delta disclosure, null-not-constant degradation, the v0.3 deprecated-field values |
| `tests/test_anchor_service.py` | end-to-end: flipping the stored regime label through `build_anchors` leaves every growth-anchor numeric field byte-identical (P0_0 §1.2 rule 5) |
| `tests/test_anchor_multiples.py` | state derivation, as-of state lookup, conditioning actually conditioning, ladder rung reporting, floor enforcement, Gordon consistency and refusal, the P0.3 `regime_leg` staleness disclosure |
| `tests/test_pit_no_lookahead.py` | as-of D cannot see a later vintage, on two real revision patterns; loud failure without vintages; the calendar rule unchanged; the smoothed growth leg never sees a month after as-of |
| `tests/test_dashboard_export_optional.py` | `data_status` is structurally immune to the anchors; the publish guard; regime_status tolerance |

Three defects were found by these tests during implementation and fixed at the source: the
implied-rate solver searched a domain where the Gordon model is undefined; the unconditional
rung of the conditioning ladder bypassed the minimum-observations floor; and beta mixed
`ddof=1` covariance with `ddof=0` variance.

---

## 9. Non-breaking guarantee

`dashboard_export.py` gained `OPTIONAL_OUTPUT_FILES` — copied to the dashboard data dir and
reported in the manifest, but **excluded from `_data_status()`**. Any missing entry in
`DASHBOARD_OUTPUT_FILES` flips `data_status` to `"partial"`, and the documented downstream
contract treats anything but `"complete"` as an error, so the new artifacts are kept out of
that list by construction. `regime_status.py` exposes the anchors and already tolerates
missing files.

The daily pipeline runs the anchor step as non-fatal (`anchors.required: false`): a failure
records `failed_optional`, which downgrades the run to `success_with_warnings` and never
takes the diagnostic down.

**Publish guard.** A degraded build never overwrites a non-degraded artifact. These files
feed another system, so a run against a thin, empty or broken database would otherwise
publish a set of nulls straight over good evidence — a hazard demonstrated in this working
copy when a test run against a temporary database overwrote the real anchors. A
stale-but-real anchor is strictly better than a fresh-but-empty one, and staleness is
already bounded by the consumer's age gate. The refusal is recorded in
`degradation_reasons`, and a non-degraded build always replaces so the anchor is never
frozen.
