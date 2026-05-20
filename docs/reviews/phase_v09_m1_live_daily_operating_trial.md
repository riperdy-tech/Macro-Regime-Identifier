# v0.9-M1 Live Daily Operating Trial

## Decision

v0.9-M1 passes as a single-day operating-trial checkpoint.

This was not a multi-day live-AI evidence run. The workflow used the default
mock-safe daily pipeline config, generated a fresh archive, exported dashboard
data, and refreshed the dashboard history index. The result is useful for
operating-loop verification, but it does not change validation readiness.

## Operating Flow Tested

Commands run:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --archive
python -m macro_engine.cli export-dashboard-data
```

The default `config/daily_pipeline.yaml` keeps live AI disabled and uses mock
classification behavior unless a separate live config or explicit live flag is
selected.

## Run Summary

| Field | Value |
| --- | --- |
| Run id | `20260520T174835Z-6ad02eda` |
| Run date | `2026-05-20` |
| Status | `success` |
| Archive path | `outputs\archive\2026-05-20\20260520T174835Z-6ad02eda` |
| Warning count | `0` |
| Error count | `0` |
| Guardrail status | `passed` |
| Operating mode | mock-safe default |

Step statuses were successful for macro, sector, news ingestion,
classification, news scoring, combined diagnostics, monitoring, and report
generation.

## Dashboard Export

Dashboard export completed and wrote a complete manifest.

| Field | Value |
| --- | --- |
| Data status | `complete` |
| Missing files | none |
| Latest run date | `2026-05-20` |
| Latest macro date | `2026-05-01` |
| Latest news score date | `2026-05-16` |
| History status | `available` |
| History runs | `11` |

The history index now includes archived runs dated `2026-05-18`,
`2026-05-19`, and `2026-05-20`.

## Latest Diagnostic Snapshot

| Area | Snapshot |
| --- | --- |
| Macro regime | `reflation` |
| Macro confidence | `10.69%` |
| Top sector diagnostics | `energy`, `materials`, `industrials`, `financials`, `consumer_staples` |
| Top news themes | `monetary_tightening`, `commodity_pressure`, `growth_slowdown` |
| Combined top sectors | `energy`, `materials`, `industrials`, `financials`, `consumer_staples` |
| Overlay max rank change | `1` |

The combined diagnostic remained bounded. The latest overlay moved only
`real_estate` and `utilities` by one rank in the monitoring snapshot.

## Source Coverage and Data Quality

The operating loop works, but source coverage remains the main practical issue.

Source coverage report:

| Field | Value |
| --- | --- |
| Configured source groups | `12` |
| Enabled source count | `13` |
| Stored item count | `2` |
| Source group count with stored items | `1` |
| Unmapped item percentage | `0.0%` |
| Active stored group | `macro_general` |

Missing stored-data groups:

```text
consumer
credit_financial_conditions
defensive_sectors
energy_commodities
geopolitical
healthcare
inflation_rates
labor
manufacturing_industrials
real_estate
technology_ai
```

Monitoring input quality still reflects the synthetic sample profile:

| Field | Value |
| --- | --- |
| Profile | `synthetic_sample` |
| Raw items | `6` |
| Source count | `1` |
| Source group count | `0` |
| Unmapped share | `100%` |
| Short body count | `6` |
| Input quality | `warning` |

This is not a release blocker for the dashboard operating loop, but it is a
clear signal that real daily operation should use mapped, balanced source
profiles.

## Dashboard Usefulness

The dashboard is useful for daily review because it exposes:

- latest run status and archive path
- macro and sector diagnostic state
- news themes and sector impacts
- combined diagnostic ranking
- monitoring warnings
- history of recent archived runs

The most important daily-use friction found is not a rendering bug. It is mode
clarity: users need to distinguish mock-safe/synthetic runs from live AI and
real-news runs when logging operating evidence.

## Issues Found

| Issue | Severity | Action |
| --- | --- | --- |
| README opening still referenced an older v0.5 status | low | Fix in M2 |
| Daily checklist did not explicitly record mock versus live mode | medium | Fix in M2 |
| Issue log did not capture source profile or live-AI mode | low | Fix in M2 |
| Current source coverage is thin | medium | Defer to repeated real daily runs |

## M2 Decision

Proceed to v0.9-M2 for small operational documentation fixes only. No scoring
or model changes are justified by this checkpoint.

## Limitations

- This review covers one fresh mock-safe daily run, not five real daily cycles.
- The dashboard history is operating context only.
- The current accumulated history is still not enough for predictive validation.
- Source coverage remains incomplete until balanced real-news profiles are used
  repeatedly.

This is not investment advice, a trading system, an allocation system, or a
performance backtest.
