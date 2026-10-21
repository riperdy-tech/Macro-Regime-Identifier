# MRI-15 Stage 2 — measurement contract (pre-registration)

**Version 1, 2026-10-22.** Required by `MRI_EVENTS_PLAN.md` v2 §11 before any forward outcome is read. It is frozen by the
hash of the commit that adds it; any later change is a new version with its reason, made before the data it would affect is
read. Status at writing: **no forward outcome has been computed**. The register started producing on 2026-10-22.

Stage 2 changes nothing. No number, score, rank, verdict or size depends on it (D1). It decides only whether an event type
may be proposed for Stage 3, and even then a pass does not authorise Stage 3 by itself.

## 1. What is measured

**Unit.** An *exposure*: a (ticker, candidate) pair where all of the following hold:

- the candidate in `event_register.json` names the ticker (`entities_resolved[].tickers[0]`, tier `named`);
- the candidate is classified, with `origin=live` and the classification made within 14 days of publication
  (contamination rule);
- it carries a primary theme from §2 with direction `positive`.

Peer-tier exposures (same cluster, the screener's `build_macro_events.py`) are a secondary sample (§4). Sector-tier
exposures are not tested in v1.

**Event time.** `t0` is the first US session close strictly after the candidate's `first_available_at`: the earliest close
at which the system could have known. Nothing before `t0` is an outcome.

**De-duplication.** A ticker counts **once per 10-session window**. The first qualifying candidate opens the window, and
later candidates for the same ticker inside it are recorded and not counted. Fragments of one story therefore never count
twice, which matters because the register runs in `syndication` mode (one report per candidate).

## 2. Hypothesis H1 (primary)

**Primary themes** (risk-off, company-level): `credit_stress`, `financial_stability_risk`.

**Primary outcome.** The 10-session cumulative abnormal log return from the `t0` close to the `t0+10` close:
100 × [ln(P_stock(t0+10) / P_stock(t0)) − ln(P_etf(t0+10) / P_etf(t0))]. The ETF is the stock's sector ETF
(`config/event_company_sectors.yaml`, from `stocks.json` sector). Prices are Yahoo adjusted closes pulled at analysis
time; they are past prices on the analysis date, so there is no look-ahead.

**Controls.** For each exposure, up to 5 control stocks:

- in the same `stocks.json` sector;
- in the same market-cap decile and the same 60-session momentum tercile at `t0`;
- not named or peer-exposed by any candidate in the 10 sessions either side of `t0`.

Controls are drawn by a fixed seed (the exposure's candidate id hashed with sha256) and measured over the same window.

**Statistic.** The mean of (exposure outcome − mean of its controls' outcomes). Expected sign: **negative**. Test: one-sided
t, with standard errors clustered two ways, by `t0` date and by ticker (Cameron–Gelbach–Miller).

**Gates.**

| Read | When | Alpha (one-sided) |
|---|---|---|
| First | at least 40 de-duplicated named exposures **and** at least 12 weeks since 2026-10-22 | 0.005 |
| Second | only if the first is inconclusive, at twice the first read's sample | 0.048 |

Pass requires, at the read that passes:

- the alpha;
- the same sign in both halves of the sample (split by `t0` date);
- a mean difference of at least −1.0 percentage point (economically meaningful over 10 sessions).

**Under-sampled.** If neither gate is reached by 2027-06-30, H1 is reported as **unvalidated**, never as "no effect".

## 3. Secondary outcomes (declared order; Holm correction across them, only if H1 passes)

1. Realised volatility over sessions `t0+1..t0+20`, as the log ratio to the control mean.
2. Maximum drawdown over sessions `t0+1..t0+20`, exposure minus control mean.

## 4. Exploratory (reported, never a gate)

- Peer-tier exposures, with the H1 statistic.
- Per-theme estimates, including `geopolitical_risk`, `energy_supply_shock`, `monetary_tightening`, `inflation_pressure`.
- Whether `market_response.result == aligned` at the time predicts the outcome. This is the closest test of the E2 check's
  usefulness, and it is exploratory because the aligned subset is small.
- Corroboration category (`officially_sourced` / `independently_corroborated` / `single_source`) as a moderator.

## 5. Hypothesis H2 (verdict reversals)

**Definition, fixed now.** On a nightly verdict ledger:

- a *reversal* is a direction change on a ticker within 10 sessions of an earlier opposite change;
- the H2 rate is reversals per exposed stock-night, over the 10 sessions after `t0`, against the same rate on matched
  unexposed stock-nights over the same dates (the §2 controls);
- the test is a one-sided difference of rates, clustered as in §2, at alpha 0.01, read once at 12 weeks of ledger.

**Status: inactive.** No nightly verdict ledger runs now; the Shadow V4 task was removed on 2026-10-20. H2 starts on the
first date such a ledger runs, through the rev23 coordinator, and its 12 weeks count from that date. A rate, never a count.

## 6. What Stage 3 would need (stated, not authorised)

A Stage 3 proposal for an event type needs all of the following:

- an H1 pass for that type at its own gate;
- the re-risk rule of plan v2 §11.3: what new evidence allows restoring a reduced position — the candidate closes, the
  market response reverses, or a fresh verdict arrives on new filing evidence;
- registration with the coordinator (AK.78), if v4 reads anything new;
- an operator ruling.

## 7. Invalidations

The read is void and restarts its clock if any of these happens without a new version of this file committed first:

- the register's grouping mode changes;
- the E2 or exposure parameters in `config/events.yaml` change;
- the classifier `prompt_version` changes;
- the de-duplication rule changes.

A linker (arm A or B) passing the v2.3 blind holdout and replacing `syndication` mode is such a change. It needs v2 of this
plan.
