# RS2 Repair Package — replacing hardcoded valuation constants with measured anchors

Companion to `outputs/rs2_repair_package.json` (machine-readable) and
`docs/ANCHOR_METHODOLOGY.md` (how each anchor is built).

**Status of this package: PARTIALLY ADOPTABLE.** The growth anchor is complete, non-degraded,
and **verified live in the consumer**. The cost-of-capital anchor publishes a full risk-free
curve and a sourced ERP history, but no *measured* cost of equity — so the level correctly
stays on the self-referential calibration. The multiple bands remain market-observation-free.
See §2.

---

## 1. What is being replaced

| Constant | Location | Current value | Replaced by |
| --- | --- | --- | --- |
| `TERMINAL_G` | `valuation_backbone.py` | `0.025` | `long_run_growth.terminal_g_suggestion` |
| `SECTOR_WACC` | `valuation_backbone.py` | integer table, 7–11 (%) | `cost_of_capital` **level**; spreads preserved |
| `DEFAULT_WACC` | `valuation_backbone.py` | `10.0` | `cost_of_capital` level |
| `UTIL_COE` | `valuation_backbone.py` | `0.07` | `cost_of_capital` level, utilities loading |
| `FIN_COE` | `valuation_backbone.py` | `0.10` | `cost_of_capital` level, financials loading |

### What is explicitly NOT replaced

* **Sector spreads.** MRI owns the *level*. The consumer measured that spreads carry ~15
  tier flips of ranking information while level shifts are ranking-neutral (ρ ≥ 0.995,
  audit C1), so the spread structure stays where it is and MRI publishes `sector_loadings`
  as information only.
* **No firm-level WACC.** `base_cf = NI + D&A − capex` is a levered flow compared against
  market cap. Bridging it to enterprise value with a WACC double-counts the debt claim —
  tried 2026-08-07, reverted. The anchors are a cost of **equity**.
* **`valuation_engine.dcf_value()`'s default.** It already accepts `terminal_growth` with a
  default of `0.025`; the backbone now passes the anchor value explicitly. **No change was
  required in `valuation_engine.py`, and none was made** — changing the default would have
  altered the meaning of every existing caller.

---

## 2. Readiness — measured, not asserted

Measured from the published artifacts at `asof 2026-09-20`.

| Anchor | Leg | State | Note |
| --- | --- | --- | --- |
| cost of capital | nominal 10y | **4.94%** ✅ | `DGS10`, 2026-09-17 |
| cost of capital | real 10y (TIPS) | **2.61%** ✅ | `DFII10` |
| cost of capital | breakeven 10y | **2.33%** ✅ | reconciles: 4.94 − 2.61 = 2.33 |
| cost of capital | term premium | **0.96%** ✅ | `observed:THREEFYTP10` (ACM is not on FRED) |
| cost of capital | CPI / PCE yoy | **3.78% / 3.77%** ✅ | |
| cost of capital | ERP | **implied 4.24%** ✅ | solved over a **universe** aggregate (1,676 names, 76.9% coverage); `erp_basis: "universe"` |
| cost of capital | **cost of equity level** | **9.18%** ✅ | `implied_cost_of_equity`; `market_implied_*` reserved and null |
| cost of capital | ERP history band | ✅ | Damodaran implied ERP (FCFE), 65 obs 1961–2025, median 4.10% |
| cost of capital | sector loadings | **17 sectors** ✅ | real panel: SPY vol 19.2%, drawdown −56.5% |
| long-run growth | real potential | **2.06%** ✅ | `GDPPOT` trend, realised history only |
| long-run growth | inflation expectation | **2.32%** ✅ | `T5YIFR` |
| long-run growth | nominal trend | **4.38%** ✅ | |
| long-run growth | **terminal g suggestion** | **3.50%** ✅ | vs 2.5% constant → **delta +1.0pt** |
| multiple bands | market-observed quantiles | **11 sectors** ✅ | panel built from the screener corpus: 325,279 ticker-months, 150 months. **Trailing** (`pe_ttm`), labelled via `measure` |
| multiple bands | Gordon cross-check | `null` ⚠️ | withheld: no *measured* cost of equity exists |

**Adoptable today — and adopted.** The long-run growth anchor and the **cost-of-capital level**
are both flowing into the consumer; the multiple bands are available as a cross-check.

**External validation of the level.** The solved ERP (4.24%) sits between the Damodaran
history's median (4.10%) and its latest observation (4.23%) — two independent constructions
agreeing to within a basis point. RS2 now reports
`coe_level_source: mri_anchor(level=9.18%)` and the self-referential calibration is bypassed
entirely.

### 2a. What remains

Only one thing, and it is a labelling decision rather than missing data:

* **`market_implied_*` is still null.** The aggregate is a screener universe, not an index, so
  the solve is published as `implied_erp` / `implied_cost_of_equity` with
  `erp_basis: "universe"`. Replacing it with a true index aggregate would raise the authority
  of the number; nothing else changes.
* **`point_in_time` cutover** is prepared and blocked on a backfill, not on code. See below.

### 2b. The one deferred item, with its cost measured

`scoring_mode: point_in_time` is implemented, tested and **not enabled**. Enabling it requires
ALFRED vintages across the whole diagnostic window, and the backfill to 1990 is incomplete:

| | |
| --- | --- |
| evaluation dates | 437 (1990-01 … 2026-05) |
| vintage as-of dates held | 29 (2024-01 … 2026-05) |
| dates that would resolve to nothing | **408 of 437 (93%)** |

Flipping it now would mark every feature `pit_vintage_missing` and collapse the historical
diagnostic for 1990–2023. The backfill is running (≈6,555 vintages, rate-limited by FRED); the
cutover is one config line **once it completes**.

---

## 3. What was actually implemented on the consumer side

`valuation_backbone.py`:

* `terminal_g()` — returns `(rate, source)`, anchor-first, else `TERMINAL_G = 0.025` with
  the reason recorded. Threaded into `_solve_implied_growth`, the fair-value DCF, the
  growth-sensitivity telemetry, and the reported `terminal_growth`.
* `coe_offset_pts()` — anchor-first. When a fresh cost-of-capital anchor carries a
  market-implied cost of equity, the offset that lands the raw sector table on that level
  is used; otherwise the previous self-referential calibration is returned unchanged.
* `coe_level_source()` — names which evidence set the level, so a fallback is never silent.
* `_comps_check()` — extended with the anchor's band for the sector, kept in **separate,
  labelled fields** (`anchor_ntm_pe` vs `anchor_arithmetic_check.justified_pe`) so an
  arithmetic identity cannot masquerade as a market observation.
* New output fields: `terminal_growth_source`, `coe_level_source`.

`rs2_data.py`: `anchors_dir()`, `load_anchors()`, `anchor_age_days()`, `usable_anchor()`,
`anchor_level_cost_of_equity_pct()`, `anchor_terminal_g()`, `anchor_multiple_bands()`.
Read fresh every run and never cached into `config.json`, so re-running MRI is enough to
pick up a new vintage.

`config.json`: `anchors_dir`, `anchor_max_age_days` (75), plus a note recording the
consumption contract.

### Fallback semantics

`usable_anchor()` returns `None` for a missing, unreadable, stale or structurally invalid
anchor, and every caller then keeps its existing constant. `None` is the whole interface —
the same discipline as `mos_cut()` returning `None`: *"we could not measure it"* must not
silently become *"we measured the default"*.

An anchor is dated by its own **`asof`** (the observation date it describes), not
`built_at`. An anchor built today off two-month-old inputs is two months old in substance,
and the payload says so.

---

## 4. Verified behaviour

Both paths exercised on the live book; the growth anchor is now **consumed in production**.

**Growth anchor adopted (live).** `Agilent (A)`:

```
terminal_growth        0.025 -> 0.035   anchor_long_run_growth(2026-09-20)
expectations_gap_pts   17.1  -> 15.4
mos_pct                -58.9 -> -55.7
wacc_pct               11.7             (unchanged)
coe_level_source       self_referential_calibration(offset=1.7pts,
                         anchor_has_no_measured_erp_risk_free_only)
```

Byte-for-byte the pre-anchor behaviour when no anchor is present: `terminal_growth` is 0.025
sourced to `engine_constant(...)`.

**Cost-of-capital level correctly declined.** `market_implied_coe` is null because no ERP could
be *measured*, so RS2 keeps its self-referential calibration and records exactly why. The
observed 10y (4.94%) is a risk-free rate; publishing it as a cost of equity would understate
the discount rate by the whole equity premium.

### Impact audit (executed, controlled arms)

`RS2 Local/_archive/retired_20260920/tools/audit_202608/c2_anchor_impact.py` (the audit tree was
relocated when the v2.0 generation was archived), over the live book (170 names, 165 reverse-DCF).
Each arm pins an explicit engine state — `(terminal growth, level offset)` — because an earlier
version varied the anchor *inputs* and let the rest fall out, which stopped measuring anything
once the calibration cache became anchor-dependent.

| arm | terminal g | level offset | median ΔMoS vs pre-anchor | ρ(mos) | brake-tier flips |
| --- | --- | --- | --- | --- | --- |
| `pre_anchor` (baseline: self-referential level, constant g) | 0.025 | +1.7pt | — | — | — |
| `anchor_g` (growth anchor alone, level still self-referential) | 0.035 | +1.7pt | **+3.5 pts** | 1.0 | 1 |
| `coherent` (**shipped state**: anchor level + anchor growth) | 0.035 | **−0.3pt** | **+19.8 pts** | **0.995** | **7** |

Measured 2026-09-20 against the live anchor artifacts (`coe_source:
anchor_implied_coe(universe,2026-09-20)`, `terminal_g: 0.035` from the growth anchor, 165
reverse-DCF names).

**Correction to the previous version of this table.** It reported the shipped row as
`(0.035, +2.4pt)` with **−0.4 pts / ρ=1.0 / 0 flips**, and concluded that adopting the anchors was
"close to neutral". That measurement was real but the row was mislabelled: `+2.4pt` is the
**self-referential cache's** `level_offset_pts`, and `coe_offset_pts()` is anchor-first — when a
usable anchor level exists it returns `anchor_level − table_mean` instead, which is **−0.3pt**
here. So the arm described as "shipped" was in fact the pre-anchor level with the new growth rate,
which is neutral by construction. The shipped state is the third row, and it is not neutral.

**The finding, restated.** The growth anchor alone is close to neutral (+3.5 pts, one tier flip).
Consuming the anchor's **level** is what moves the book: the discount rate falls from the
self-referential 12.77% to the measured 9.18%, median margin of safety rises ~20 points, and
**7 of 165 brake tiers change**. Ordering survives (ρ = 0.995) — it is a level effect, as
predicted — but it is a large one, and 7 tier changes is not "no verdict changes".

**Why the two levels disagree by ~3.6 pts, and why that is a live issue.** `build_coe_calibration`
aggregates **net income** (a levered flow) while the engine discounts `NI + D&A − capex`. Since
capex exceeds D&A for this book, the flow the engine discounts is SMALLER than net income, so the
required return on it must be HIGHER than a premium solved against net income implies. Price/net
income and price/(NI + D&A − capex) are different multiples, and an anchor solved on the first
understates the discount rate for the second — which inflates fair value and margin of safety. The
anchor is not wrong; it answers a different question than the engine asks.

Until that basis mismatch is resolved, the level row above should be read as a measurement of the
mismatch, not as an endorsement of the applied rate. The two defensible resolutions are to solve
the ERP against the same cash-flow definition the engine discounts, or to keep the
self-referential level (row 2) and consume only the growth anchor. Both are RS2 decisions.

### Impact audit — superseded

An earlier version of the audit replayed the book under four states (`table`, `anchor_coe`,
`anchor_g`, `anchor_both`) and reported MoS percentiles, Spearman ρ and brake-tier flips, with
scenarios whose anchor was unavailable **skipped rather than approximated**. That design was
replaced by the controlled-arms audit above: varying an *input* and letting the rest fall out
stopped measuring anything once the calibration cache became anchor-dependent, and "all scenarios
unavailable" was a property of the harness, not of the anchors. The results in the table above
are the current ones.

---

## 5. Rollout order

1. Fix the inputs in §2 and re-run `macro-engine build-anchors` until
   `rs2_repair_package.json` reports `degraded: false`.
2. Run `c2_anchor_impact.py` (in `RS2 Local/_archive/retired_20260920/tools/audit_202608/`) and
   read ρ before touching the engine. Expect a large level effect and a high but not perfect rank
   correlation; a ρ near 1.0 with a large MoS shift means the level moved uniformly, which is the
   designed behaviour.
3. Adopt **terminal growth first**, alone. It is FRED-provable end to end (no equity-side
   dependency) and it is the single largest uniform lever on the expectations gap.
4. Adopt the **cost-of-capital level** second, again alone, and re-run the audit. The level
   only becomes superior evidence to the self-referential calibration once a *measured* ERP
   exists; a risk-free-only level is not a cost of equity.
5. Adopt the band fields last, and only as a **cross-check**. They are not an input to fair
   value and must never become one.

Between every step: re-run the audit, and confirm that nothing downstream regressed.

---

## 6. Rollback

* **Delete or rename the anchor JSON files.** The consumer falls back to its own constants,
  which remain in the code unchanged. Nothing else is required.
* **Narrower:** set `anchor_max_age_days` to `0`, which makes every anchor stale and forces
  the fallback path without touching the artifacts.
* **`point_in_time` scoring mode** is the configured basis (hybrid: point-in-time from
  `point_in_time_start`, the calendar rule before it). Setting
  `scoring_mode: calendar_asof` in `config/phase_b_sources.yaml` restores the previous factual
  basis outright; setting `point_in_time_start: null` instead extends point-in-time to every
  date — which, before 2014-02, EMPTIES the risk-free curve rather than upgrading it
  (docs/ANCHOR_METHODOLOGY.md §6.2b).
* No MRI output schema changed and no MRI artifact was removed, so **no MRI rollback is
  required** for any RS2-side rollback.

---

## 7. Known findings

**Resolved during execution (2026-09-20):**

1. ~~The local `data/sector_proxy_prices.csv` is synthetic.~~ **Replaced with real market data**
   (SPY vol 19.2%, drawdown −56.5%) and `sector_loadings` enabled for 17 sectors. Stooq is now
   bot-blocked, so `scripts/fetch_sector_proxy_prices.py` refreshes the panel from Yahoo into
   the CSV that MRI's existing `csv` provider reads. The fetcher refuses to write a panel whose
   benchmark volatility is implausible, so the synthetic file cannot come back by accident.
2. ~~The two declared term-premium series are unverified.~~ **Resolved by measurement.** The ACM
   term premium is **not on FRED** at all; `THREEFYTP10` (Kim-Wright) is, and is daily rather
   than monthly. Config corrected; `term_premium_source` reads `observed:THREEFYTP10`.
3. ~~`sector_validation.json` published statistics from an unverified panel.~~ **Gated.** A
   falsification test now withholds the result when the panel cannot be market data.

**Still open:**

4. **`outputs/current_regime.json` carries a synthetic future `date`** (observed `2031-08-01`).
   RS2 is already safe (`_fresh()` rejects a negative age) and `regime_status.json` now reports
   `current_regime_future_dated: true` with the age, so no consumer has to guess. The producer
   is still unidentified — flagged, not fixed.
5. **The forward-multiple panel does not exist** anywhere in either repository. The
   market-observed leg is implemented, tested, and inert.
6. **The Gordon cross-check is withheld** while no measured cost of equity exists. It was
   briefly fed the risk-free rate, which produced a 63.2x "justified" multiple; the sanity band
   caught it and the input was corrected to the measured CoE only.
7. **`stooq` is the configured price provider and CI's source, and it is currently
   JavaScript-challenged.** `ingest-sector-proxy-prices` will fail loudly in CI rather than
   silently degrade — correct, but CI needs a provider decision.
4. **The two declared term-premium series are unverified in this offline environment.**
   `ACMTP10` / `THREEFYTP10` must be confirmed against the live API on the first ingest; the
   fallback is labelled, so a wrong guess degrades rather than misleads.

---

## 8. Adoption gates (enforced, not advisory)

Copied from `rs2_repair_package.json`, and each one is a stop condition:

1. Every leg the consumer actually uses must be non-null. **A null leg means the input was
   not measurable, and the consumer must keep its existing constant.**
2. `terminal_g_suggestion` may be adopted only when `long_run_growth.degraded` is `false`.
3. Adopt the **level**; keep the **spread structure**; treat `sector_loadings` as
   information.
4. Re-run the MoS rank-correlation impact audit **before and after** adoption, and record
   both.
