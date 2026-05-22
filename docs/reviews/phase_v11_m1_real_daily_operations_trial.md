# v1.1-M1 Real Daily Operations Trial

Verdict: pass as an initial post-v1.0 real daily operations trial.

This milestone ran the frozen `v1.0-rc1` platform against local mapped real-news
data and confirmed that the daily workflow, dashboard export, history updates,
archives, and guardrails still work after the release candidate.

## Operating Scope

Preferred target:

```text
5 real daily runs across separate calendar/trading days
```

Actual available run scope:

```text
3 mapped real-news daily runs on the same operating date
```

The runs did not occur across separate dates. Longer real daily accumulation is
still pending and should continue before any validation-readiness claim.

## Run Summary

| Run | Source profile | Mode | Selected items | Successful | Failed | Retry count | Repair count | Status | Archive |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| `20260522T160308Z-94413a8c` | `pilot_balanced_local_csv` | live AI resume | 0 | 0 | 0 | 0 | 0 | success | `outputs\archive\2026-05-22\20260522T160308Z-94413a8c` |
| `20260522T160646Z-c6fd30d6` | `last_30_days_local_csv` | live AI bounded | 25 | 25 | 0 | 0 | 0 | success | `outputs\archive\2026-05-22\20260522T160646Z-c6fd30d6` |
| `20260522T162258Z-c2bc0817` | `last_30_days_local_csv` | live AI resume | 0 | 0 | 0 | 0 | 0 | success | `outputs\archive\2026-05-22\20260522T162258Z-c2bc0817` |

The first run was a clean resume check: the balanced items were already
classified, so `only_unclassified` selected no new rows. The second run used the
new mapped last-30-days profile and classified 25 new unclassified rows. The
third run refreshed the final dashboard export after validation and again
selected no new rows, confirming resumability.

## Classification Reliability

Latest live DeepSeek classification quality run:

```text
provider/model: deepseek / deepseek-v4-flash
total classified items in stored quality summary: 166
success_count: 166
failure_count: 0
success_rate: 100%
retry_rate: 0%
repair_rate: 0%
```

The bounded daily run itself selected 25 items and completed 25 successful live
classifications with item-by-item progress output.

## Source Coverage

Source coverage after the live mapped run:

```text
stored_item_count: 270
source_group_count: 12
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 17
old_item_pct: 6.3%
```

Groups represented:

```text
consumer
credit_financial_conditions
defensive_sectors
energy_commodities
geopolitical
healthcare
inflation_rates
labor
macro_general
manufacturing_industrials
real_estate
technology_ai
```

Remaining source-quality warnings:

```text
some source groups have stale stored items
latest stored items are concentrated in a small number of source groups
```

Groups needing fresher coverage:

```text
consumer
credit_financial_conditions
energy_commodities
labor
technology_ai
```

## Dashboard Freshness

Dashboard export result:

```text
data_status: complete
missing_files: none
latest_run_date: 2026-05-22
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-21
```

The dashboard data export completed after the live daily run and after the
accumulation/source coverage refresh.

## History Tab Behavior

The dashboard History export includes the newest daily run:

```text
run_id: 20260522T162258Z-c2bc0817
run_date: 2026-05-22
run_mode: daily
status: success
guardrail_status: passed
readiness_label: insufficient_history
top_combined_sectors: energy, materials, industrials, consumer_staples, financials
```

The most recent run was a resume/export refresh. The run with new live
classifications was `20260522T160646Z-c6fd30d6`. History is updating, but the
run history is still too thin for validation.

## Latest Diagnostic Snapshot

Macro:

```text
latest macro date: 2026-05-01
reported regime: reflation
raw dominant regime: reflation
confidence: 7.18%
```

Top sector macro diagnostics:

```text
energy
materials
industrials
consumer_staples
financials
```

Top news themes:

```text
monetary_tightening
inflation_pressure
commodity_pressure
growth_acceleration
financial_stability_risk
```

Top combined sectors:

```text
energy
materials
industrials
consumer_staples
financials
```

Latest max overlay rank change:

```text
1
```

## Accumulation Readiness

Latest accumulation summary:

```text
raw_item_count: 270
classified_items: 166
failed_items: 0
success_rate: 100%
source_count: 148
source_group_count: 12
readiness_label: insufficient_history
```

The classified item count now exceeds 100, but the number of real repeated daily
run dates remains too low. The readiness label should remain
`insufficient_history`.

## Issues Found

1. The preferred target of five separate real daily runs was not met in this
   single execution window.
2. The first mapped live run selected zero new items because the local database
   had already classified that profile's rows.
3. Source coverage is materially improved but some groups remain stale.
4. The standalone monitoring config still defaults to the synthetic profile, so
   explicit source-profile refresh is useful when producing real-data reports.

## M2 Decision

Proceed to M2 source coverage improvement.

The main post-M1 work is not scoring. It is source coverage, freshness, and
continued real daily operation.

## Boundary Statement

This review is not investment advice.

This system is not a trading system.

This review does not validate predictive performance.
