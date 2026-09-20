# Capital-Market Anchors — Fix Plan

**Created:** 2026-09-20
**Covers:** MRI (`Stock Screener/Macro Regime Indicator`) and RS2 Local
**Scope:** everything outstanding after the v0.2 anchor implementation — blockers, silent-wrong-answer
hazards, verification debt, and the decisions only the operator can make.

---

# EXECUTION STATUS (2026-09-20, same day)

Network became available mid-session, which unblocked most of P0 and P2. Executed:

| # | Item | Status | Evidence |
| --- | --- | --- | --- |
| P0-1 | RS2 ↔ MRI sector vocabulary | **FIXED** | `MRI_SECTOR_IDS` bridge; `JPM`/`XOM`/`A` resolve a band; test asserts totality over the real `stocks.csv` vocabulary |
| P0-2 | Seven FRED series un-ingested | **DONE** | series ingest succeeded; growth anchor now `degraded: false` |
| P0-3 | Term-premium IDs unverified | **RESOLVED BY MEASUREMENT** | `ACMTP10` **does not exist on FRED** (HTTP 400; searched "term premium", "ACM term premium", "Adrian Crump Moench" — zero hits). Removed. `THREEFYTP10` exists but is **daily**, not monthly as configured. Corrected. |
| P0-4 | No equity aggregate | **DONE** | `scripts/build_equity_aggregate.py`: 1,676 constituents, **76.9%** coverage, USD reporters only (496 excluded on correctness grounds), share classes collapsed by CIK (236, incl. the GOOG/GOOGL $4T double-count). Implied ERP **4.24%** → cost of equity **9.18%**; published as `implied_*` with `erp_basis: "universe"`, `market_implied_*` reserved and null |
| P0-5 | No ERP history | **DONE** | `scripts/fetch_erp_history.py` → Damodaran implied ERP (FCFE), 65 annual obs 1961–2025. `erp_source` is now `regime_estimate` |
| P0-6 | No valuation panel | **DONE** | panel built from the screener corpus: 325,279 ticker-months, 3,492 names, 11 sectors, 150 months (2014-01…2026-06). Real conditional bands published; `measure` labels it **trailing** |
| P0-7 | Synthetic price panel | **DONE** | stooq is now bot-blocked (all endpoints return a JS challenge); added `scripts/fetch_sector_proxy_prices.py` (Yahoo, real data). Panel verified: SPY vol **19.2%**, drawdown **−56.5%**. Loadings enabled: 17 sectors |
| P1-1 | `sector_validation` published synthetic stats | **FIXED** | falsification gate; real data → `valid: true`, mock panel → `unpublished`. Regenerated: rank IC 0.026 / 0.040 |
| P1-2 | `current_regime.json` future date | **RESOLVED** | the DB has **no 2031 dates** — it was a **stale artifact from an earlier synthetic run**, not a producer bug. Regenerated (`2026-05-01`); `historical_diagnostic.json` (end_date 2031) and `automation_run_summary.json` were stale the same way. Added a `daily_health` guard that flags any artifact dated after the newest stored observation |
| P1-3 | Stale generated artifacts | **DONE** | anchors, `regime_status.json`, `sector_validation.json` all regenerated |
| P1-4 | `anchor_max_age_days: 75` | **DONE** | set to 45, matching `regime_max_age_days` |
| P1-5 | Financial/utility paths get no band | **FIXED** | `_anchor_band_only()`; `JPM` carries `anchor_ntm_pe` and correctly **no** `ev_ebit` |
| P2-1 | `c2_anchor_impact.py` never ran a real scenario | **DONE** | `anchor_g` executed over 165 names: terminal g 2.5%→3.5% moves median MoS **+3.5 pts**, ρ(mos) = **1.0**, 1 tier flip. Plus a wiring test |
| P2-2 | PIT never ran against live ALFRED | **DONE** | 204k+ vintage rows backfilled; PIT validation below |
| P2-3 | `failed_optional` untested | **DONE** | end-to-end test of the non-fatal transition |
| P2-4 | Loadings path untested on real data | **DONE** | enabled + asserted (P0-7) |
| P2-5 | Daily output-dir coupling | **DONE** | test asserts recorded paths == written paths |
| P2-6 | Publish guard on mixed degradation | **DONE** | per-file test |
| P3-1 | Missing declared deps, vendored | **DONE** | `pip install -e ".[dev]"`; `pip check` → **"No broken requirements found"** |
| P3-2 | RS2 test interpreter undocumented | **DONE** | `RS2 Local/CLAUDE.md` §5 |
| P3-3 / P4-5 | No lockfile | **DONE** | `requirements.lock`, 48 pinned packages, resolves cleanly |
| P4-1 | Equity-side data acquisition | **OPEN — operator decision** | the only substantive item left |
| P4-2 | `point_in_time` cutover | **READY, not enabled** | mechanism proven; flipping it is a config line and a judgement call |
| P4-3 | Allow synthetic-price validation output | **DECIDED: gate it** | implemented |
| P4-4 | Fix the future-dated snapshot | **FLAGGED ONLY** | as recommended |

### New defects found *while executing* this plan

| Defect | Why it mattered | Fix |
| --- | --- | --- |
| `upsert_sector_proxy_prices` deleted on `(ticker, date)`, so a refresh left orphan rows from the previous panel | Refreshing the synthetic panel with real prices stitched OLD values onto market holidays — SPY printed **+83% on Juneteenth 2022** and 62% annualised vol against a true 19%. A series that is not any real instrument | delete scoped by **ticker**; refresh is authoritative. Regression test |
| A partial ingest left `DGS10` four months stale while TIPS/breakeven were current | The published curve mixed vintages: nominal − real = 1.84% against a 2.33% breakeven. Not a curve that ever existed | risk-free **consistency guard**: leg date-spread check + breakeven identity. It now reconciles: 4.94% − 2.61% = 2.33% |
| `GDPPOT` carries CBO **projections to 2036** | `asof` resolved to 2036 and the growth trend was fitted to *forecast* data while presenting itself as an observation. Terminal g read 3.0% off projections | as-of capped at today; recomputed from realised history → **3.5%** |
| The risk-free rate was being fed to the Gordon cross-check as the cost of equity | `0.60 / (0.0445 − 0.035)` = **63.2x**, caught by the sanity band. An "arithmetic check" that checks nothing | the measured CoE only; **withheld** when absent |
| ALFRED clips `realtime_end` to the query's realtime_end | Single-day vintages have `realtime_start == realtime_end`, so the bracket test matched only exact backfilled dates and resolved to **nothing** for every other day | resolve as "newest vintage at or before D" |
| `backfill_vintages.py` defaulted to anchor series only | `point_in_time` gates the **feature** series (INPAYEMS, CPIAUCSL, …), which that list excludes — the mode would have reported `pit_vintage_missing` everywhere | default = all enabled sources; `--anchor-only` narrows it |
| Each vintage fetch pulled the whole history (~16k rows) | The first backfill ran 50+ minutes without storing a row | bound the observation window; 203 requests in 409s |

| B1 | `current_regime.json` carries a synthetic future date | **RESOLVED — stale artifact, not a producer bug.** Also affected `historical_diagnostic.json` (end_date 2031) and `automation_run_summary.json` | regenerated; `daily_health` now flags any artifact dated after the newest stored observation |
| B2 | **stooq is JavaScript-bot-challenged** on every endpoint | `ingest-sector-proxy-prices` fails loudly in CI rather than degrading — correct, but CI needs a source | added **`provider: yahoo`** as a first-class provider (tested, key-free). Switching is a one-line config change; the shipped default stays `stooq` until an operator decides |
| B3 | Share-class double-counting in the screener corpus (`GOOG` **and** `GOOGL`, ~$4T) | Would inflate any corpus-derived equity aggregate by ~3.7% | **FIXED** — collapsed by CIK via `cik_map.json`; corpus $90.79T → $85.29T, GOOGL kept |
| B4 | Non-US issuers in the corpus (`TSM`, `SKHY`) | A rate solved over them is not a US market-implied rate — and worse, their fundamental cash flows may not be in USD at all | **FIXED, on stronger grounds than hygiene:** `FX` marks the statement currency, and 496 non-USD reporters are excluded because summing a TWD or EUR cash flow into a USD aggregate is meaningless. Some are `unreviewed_currency_mismatch` with conversion explicitly unproven |

### P4-1 — resolved

The equity aggregate is built and adopted as a **universe**-basis solve. The valuation panel
turned out not to need an acquisition either (built from `backtest_prices.json`). The only
remaining decision is whether to replace the universe aggregate with a licensed **index**
aggregate, which would let `market_implied_*` be populated; nothing else changes.

## Severity legend

| Level | Meaning |
| --- | --- |
| **P0** | Blocks anchor adoption. The anchors are structurally complete and functionally inert until these are closed. |
| **P1** | Silent-wrong-answer hazard. Produces a confident, wrong number rather than a null. Highest damage per hour if left. |
| **P2** | Verification debt. Code path exists but has never run against real inputs, so its correctness is asserted, not measured. |
| **P3** | Tooling / reproducibility. Does not affect results today; affects whether results can be reproduced tomorrow. |
| **P4** | Decision required. No correct default exists without operator input. |

---

# P0 — Blockers

## P0-1 · RS2 never resolved an anchored sector band — **FIXED 2026-09-20**

**Issue.** MRI publishes snake_case sector ids (`information_technology`, `health_care`,
`financials`); RS2 holds Yahoo/GICS names from `stocks.csv` (`Technology`, `Healthcare`,
`Financial Services`). The two sets share **no value**, so `anchor_multiple_bands(sector)` matched
nothing and the anchored band silently never appeared — a dead feature indistinguishable from a
data gap.

**Measured, not inferred.** Against a non-degraded probe payload: `Energy`, `Technology`,
`Healthcare`, `Utilities`, `Financials`, `Industrials` → all `no match`.

**Root cause class.** This is exactly the failure RS2/CLAUDE.md §0 records: the code *sounded*
rigorous and was never checked against the actual definition of the input.

**Fix applied.** `rs2_data.MRI_SECTOR_IDS` + `mri_sector_id()` bridge every value RS2 holds to its
MRI id, with `""`/`"Unknown"` → `None` so an unknown sector never inherits another's band.
Regression test `tests/test_anchor_consumption.py` asserts the bridge is **total over the real
`stocks.csv` vocabulary**, so a new sector value fails the suite instead of silently missing.

**Verified end-to-end:** `A` (Healthcare) and `XOM` (Energy) now resolve `anchor_ntm_pe.median`.

**Residual:** P1-5 — the financial/utility paths never call `_comps_check` at all.

---

## P0-2 · Seven declared FRED series are not in the store

**Evidence.**
```
declared but MISSING from raw_observations:
  ACMTP10, DFII10, DTB3, GDPPOT, T10YIE, T5YIFR, THREEFYTP10
```

**Impact.** Blocks the **entire** growth anchor (all legs null → `terminal_g_suggestion: null`) and
three of the four risk-free legs. `long_run_growth.degraded: true` makes RS2's adoption gate reject
it, so `TERMINAL_G = 0.025` stays in force.

**Solution.**
```bash
# MRI, with FRED_API_KEY set
python -m macro_engine.cli ingest-fred --series DFII10 --series T10YIE --series T5YIFR \
    --series DTB3 --series GDPPOT --series ACMTP10 --series THREEFYTP10
python -m macro_engine.cli build-anchors
```
The series are already declared in `config/phase_b_sources.yaml` with `required: false`, so this is
an ingest, not a config change.

**Acceptance.** `select distinct series_id from raw_observations` returns all seven;
`long_run_growth_anchor.json` reports `degraded: false` and a non-null `terminal_g_suggestion`.

**Blocker.** No network access in the current environment — this needs the operator's machine or CI.

---

## P0-3 · The two term-premium series IDs are unverified

**Issue.** `ACMTP10` and `THREEFYTP10` are the least standard IDs in the new set and could not be
checked against the live API offline. A wrong ID is not fatal — `term_premium_source` falls back to
the labelled `DGS10 − DTB3` proxy — but it weakens the decomposition silently.

**Solution.** Include them in the P0-2 ingest and inspect the result:
```bash
python -m macro_engine.cli ingest-fred --series ACMTP10 --series THREEFYTP10
python -c "import json;print(json.load(open('outputs/cost_of_capital_anchor.json'))['term_premium_source'])"
```
`observed:ACMTP10` / `observed:THREEFYTP10` means confirmed. `proxy:...` plus a
`degradation_reasons` entry means neither resolved — then correct or drop them in
`config/anchors.yaml`.

**Hardening (optional, recommended).** `validate-config` could assert that every series referenced
by `config/anchors.yaml` is declared in `config/phase_b_sources.yaml`. It would not catch a wrong
ID, only a missing declaration — a cheap guard against config drift between the two files.

---

## P0-4 · No equity aggregate → the implied ERP and the cost-of-equity level are inert

**Issue.** MRI holds no equity data by design. Without an aggregate, `erp_source: "unavailable"` and
`market_implied_coe: null`. RS2 then deliberately returns `None` from
`anchor_level_cost_of_equity_pct()`, because publishing the observed 10y as a "cost of equity" would
understate the discount rate by the whole equity premium. **This is correct behaviour, and it means
the cost-of-capital anchor contributes nothing until an aggregate exists.**

**Solution.** Supply `cost_of_capital.erp.equity_aggregate_path` → JSON, schema in
`docs/ANCHOR_METHODOLOGY.md` §3.3:
```json
{"asof": "2026-04-30", "source": "<citation>", "growth": 0.045, "coverage_share": 0.85,
 "constituents": [{"ticker": "AAA", "cash_flow": 1.2e9, "market_cap": 3.0e10, "growth": 0.06}]}
```
Needs, per constituent: levered cash flow (`NI + D&A − capex`) and market cap. Requires ≥30
constituents and ≥60% index-cap coverage to be accepted.

**Do NOT** derive the aggregate from RS2's own book. That reintroduces exactly the self-reference
this anchor exists to replace — the level would again be reverse-solved from the market it is
supposed to inform.

**Acceptance.** `erp_source: "implied"` and a non-null `market_implied_coe`.

---

## P0-5 · No ERP history file → no percentile context

**Solution.** Operator supplies `data/anchors/erp_history.csv` (`date,erp`), long format, ≥60 points
inside a 30-year lookback, with a citable source recorded in
`cost_of_capital.erp.regime_estimate.source_label`.

**Do NOT** synthesize or recall a series from memory. No file is shipped precisely because an
invented one would be the fabrication Rule 1 exists to prevent. Without it,
`erp_percentile_vs_history` stays `null` — which is an acceptable, documented outcome.

---

## P0-6 · No valuation panel → the market-observed multiple bands are inert

**Issue.** The bands need `date, sector, forward multiple`. Nothing in either repository holds
forward earnings by sector. MRI's local sector ETF file is `ticker,date,close` — **a price-only
series cannot produce a P/E.**

**Solution.** Supply `multiple_bands.panel` (`source: external_valuation_panel`, `path: ...`), a
CSV/Parquet with `date` + `pe_ntm` (or `eps_ntm` + `close`) + `sector_id` (or `ticker`). This is an
equity-side data acquisition, not a code change — the provider, conditioning ladder, fallback
ladder and tests are complete and exercised with fixtures.

**If the data cannot be acquired:** see P4-1.

---

## P0-7 · The local price panel is synthetic → sector loadings withheld

**Evidence (measured).**

| Check | Observed | Real SPY |
| --- | --- | --- |
| Annualised volatility | **5.1%** | 16–20% |
| Mar 2020 crash (2020-03-16 → 03-23) | 363 → 369 (**rising**) | ~298 → ~222 |
| XLE/SPY return correlation | **−0.12** | ≈ +0.6 |

`data/sector_proxy_prices.csv` is gitignored and byte-identical to `..._sample.csv`. CI runs
`ingest-sector-proxy-prices` against live stooq, so this is a local-only artifact.

**Solution.**
```bash
python -m macro_engine.cli ingest-sector-proxy-prices   # live stooq
# then, in config/anchors.yaml:
#   cost_of_capital.sector_loadings.price_panel_source: stored_sector_proxy_prices
python -m macro_engine.cli build-anchors
```
**Acceptance / guard.** Before enabling, confirm the panel is real: SPY realised vol in 12–25%,
a visible 2020 drawdown, and sector/SPY correlations in 0.4–0.9. `loading_source` must read
`beta_vs_SPY_5y` and `sector_loadings` must be non-empty.

---

# P1 — Silent-wrong-answer hazards

These pre-date the anchor work. They are listed because they are the same class of defect the
anchors were built to eliminate: a confident number with no verifiable provenance. **They were
deliberately not fixed**, per the scope discipline in the implementation notes.

## P1-1 · `sector_validation.json` publishes validation statistics computed from synthetic prices

**Evidence.** `outputs/sector_validation.json` reports `price_start_date: 2020-01-02`,
`price_end_date: 2026-05-14` — an exact match for the synthetic CSV — with `valid: true` and
published rank ICs (`rank_ic_spearman: -0.045`, `hit_rate_top_positive: 0.493`, n=836).

**Impact.** Fabricated validation evidence presented as measured, in a **required**
`DASHBOARD_OUTPUT_FILES` entry, so `data_status` reads `complete`. CI overwrites it with real
stooq data, so the exposure is local exports and any CI run that falls back to the committed CSV.
This is arguably worse than the anchor gaps: the anchors degrade loudly, this does not.

**Solution — provenance gate, mirroring the anchors' discipline.**
1. Persist the provider that produced the stored prices (the `source` column already carries
   `csv` / `stooq`).
2. In `run_stored_sector_validation`, attach `price_provenance` to the summary and set
   `valid: false` with `reason: unverified_price_provenance` when the prices came from the `csv`
   provider **and** fail a basic realism check (realised vol outside 10–30%, or no drawdown in the
   window).
3. Non-breaking either way: the file still exists, so `data_status` is unaffected; the payload
   stops asserting an unverifiable result.

**Acceptance.** A local run against the synthetic CSV yields `valid: false` +
`unverified_price_provenance`; a CI run against stooq yields `valid: true`.

---

## P1-2 · `current_regime.json` carries a synthetic future date (`2031-08-01`)

**Evidence.** `outputs/current_regime.json` → `date: 2031-08-01`, `reported_regime: tightening`.

**Impact.**
* RS2 is **already safe**: `_fresh()` rejects future dates (`age < 0`), so it falls back to
  `macro_state.json`. This is why the defect has not bitten.
* `regime_status.json` and the dashboard have no such gate and will show a 2031 regime.
* The anchors avoid it by dating themselves from the stored observation history, and
  `current_regime_label()` reads the stored timeline rather than the snapshot.

**Solution (two parts, different sizes).**
1. **Cheap, non-breaking:** add `current_regime_date`, `current_regime_stale` and
   `current_regime_future_dated` to `regime_status.build_regime_status` so every consumer can see
   the problem. ~15 lines.
2. **Root cause:** find what writes a 2031 date and stop it. Likely the synthetic-sample path or
   replay. Unknown effort — needs a trace of the producer.

**Decision:** P4-4.

---

## P1-3 · Generated artifacts can sit stale relative to the code

**Evidence.** `outputs/regime_status.json` on disk has no `capital_market_anchors` key — it was
produced before the anchors existed. `outputs/current_regime.json` and `daily_diagnostic_summary.json`
are likewise older than the current tree.

**Impact.** A consumer reading the artifacts sees the previous contract; the anchors appear absent
even though the code publishes them.

**Solution.** Regenerate after any config or code change:
```bash
python -m macro_engine.cli build-anchors
python -m macro_engine.cli run-daily-diagnostic    # or the full daily pipeline
python -m macro_engine.cli export-dashboard-data
```
**Acceptance.** `regime_status.json` contains `capital_market_anchors` and the four anchor
source-file keys.

---

## P1-4 · `anchor_max_age_days: 75` is too loose for a monthly anchor

**Issue.** `config.json` allows 75 days before RS2 rejects an anchor. The anchors are monthly and
the comparable regime gate is `regime_max_age_days: 45`. At 75 days RS2 will silently keep consuming
an anchor covering a period up to ~2.5 months old — the failure mode the freshness gate exists to
prevent, just set loosely.

**Solution.** Set `anchor_max_age_days: 45` in `RS2 Local/config.json`, matching
`regime_max_age_days`. Both are monthly artifacts; one gate for both is easier to reason about than
two arbitrary numbers.

**Trade-off.** Tighter means more fallback time when MRI has not run. If MRI's cadence is monthly,
45 days leaves a ~2-week window of fallback per cycle. If that is unacceptable, fix the cadence
rather than widening the gate.

---

## P1-5 · The financial and regulated-utility paths never receive the anchored band

**Evidence.** `_comps_check(t, sector)` is called exactly once, in the reverse-DCF return. Neither
`_financial_backbone` (banks, insurers, mortgage) nor the regulated-utility route calls it or adds
its fields.

**Impact.** Banks, insurers and regulated utilities — the archetypes most sensitive to the discount
rate — get no anchored band and no comps signal at all. RS2/AGENTS.md §2 explicitly warns against
excluding whole archetypes from holistic treatment.

**Solution.** Have `_financial_backbone` attach the same `**_comps_check(t, sector)` fields it can
support, plus the anchor fields (`anchor_ntm_pe`, `anchor_arithmetic_check`) which are
sector-level and therefore valid for any route. Do **not** attach `ev_ebit` to a
balance-sheet business — an EV/EBIT multiple is meaningless for a bank. Keep the EV/EBIT leg
reverse-DCF-only and label the anchor legs as sector-level.

**Acceptance.** A bank and a regulated utility each carry `anchor_ntm_pe` when an anchor exists, and
still carry no `ev_ebit`.

---

# P2 — Verification debt

| # | Path | Why it is unverified | Concrete step |
| --- | --- | --- | --- |
| P2-1 | `tools/audit_202608/c2_anchor_impact.py` | Every scenario lands in `unavailable_scenarios` because all anchors are degraded, so the Spearman / tier-flip machinery has never produced a real number | Run after P0-2 + P0-4. Add a unit test with a stubbed non-degraded anchor asserting the scenarios populate and `unavailable_scenarios` is empty |
| P2-2 | ALFRED point-in-time ingestion | `backfill_vintages.py --apply` and `ingest-fred-vintages` have never hit the live API; only synthetic vintages are covered | After P0-2: `python scripts/backfill_vintages.py --apply --start 2020-01-01`, then set `scoring_mode: point_in_time` in a scratch config and diff the historical diagnostic against `calendar_asof`. Expect small, explainable differences on revision-heavy series (PAYEMS, CPIAUCSL) |
| P2-3 | `failed_optional` → `success_with_warnings` | I disabled `anchors` in the v05 daily fixture to keep tests hermetic, so the non-fatal transition has no direct test | Inject a raising `build_anchors` into the `services` dict and assert `status == "success_with_warnings"` and `anchors_status == "failed_optional"`. ~15 lines |
| P2-4 | `sector_loadings` on a real panel | Only exercised with synthetic fixtures | Covered by P0-7 acceptance checks; add an assertion on panel realism |
| P2-5 | Daily-run output-dir coupling | `_run_anchors` takes its output dir from `config/anchors.yaml` while the rest of the daily run writes cwd-relative `outputs`. If they ever diverge, archiving and the manifest miss the anchors | Confirm on the first real daily run that the four anchor files appear in `daily_diagnostic_summary.generated_artifacts`; if not, thread an explicit output dir |
| P2-6 | Publish guard under partial degradation | The guard is tested for all-degraded vs all-clean, not for a mixed build (e.g. growth degraded, cost-of-capital clean) | Add a case asserting per-file decisions are independent |

---

# P3 — Tooling & reproducibility

## P3-1 · The MRI venv was missing declared dependencies, and they were vendored by hand

**Issue.** `.venv` lacked `typer` and `trafilatura`, both declared in `pyproject.toml`. PyPI is
unreachable from this environment, so they — plus 11 transitive packages (`babel`, `tld`,
`tldextract`, `dateparser`, `pytz`, `regex`, `tzlocal`, `courlan`, `htmldate`, `justext`, `lxml`) —
were copied from `RS2 Local/research-venv`. Tests could not run at all before this.

**Risk.** The vendored set is **unpinned and unreproducible**: `typer 0.25.1`, `click 8.4.2`,
`trafilatura 2.0.0`, versions chosen by whatever the donor venv happened to hold. `typer>=0.12.0`
is satisfied, but nothing guarantees the next machine matches.

**Solution.** On a networked machine:
```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
```
then decide P4-5.

## P3-2 · RS2 has no complete test environment

`research-venv` has pandas/numpy but **no pytest**; the system Python 3.12 has pytest + pandas.
RS2 tests currently run under the system interpreter. `tests/test_sync_state.py` also needs
`git clone` of a local repo, which fails under a restrictive sandbox (named-pipe restriction) and
passes with full access.

**Solution.** Document the canonical RS2 test invocation (interpreter + command) in
`RS2 Local/CLAUDE.md`, or add a `dev` extra so one venv covers both.

## P3-3 · No lockfile in either repository

`pyproject.toml` uses lower bounds only. Combined with P3-1 this means two machines can produce
different numbers from the same commit.

**Solution.** See P4-5.

---

# P4 — Decisions required

| # | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| **P4-1** | The equity-side inputs (P0-4, P0-6) may not be obtainable at all. Accept a permanently partial package? | (a) acquire an equity aggregate + valuation panel feed; (b) publish the **growth anchor only** and drop the other two from the repair package; (c) keep all three, permanently degraded, relying on RS2's gates | **(a)** if a feed is affordable — the growth anchor alone leaves the discount-rate level self-referential. Otherwise **(b)**: it is honest about what is delivered and removes two artifacts nobody can consume. **(c)** is defensible but leaves dead weight in the daily pipeline |
| **P4-2** | When to cut `scoring_mode` over to `point_in_time`? | now / after a validation window / never | After P2-2 shows the diff is small and explainable. It moves the factual basis of **every** historical diagnostic, so it needs evidence, not confidence |
| **P4-3** | May `sector_validation.json` publish results computed from an unverified price panel? (P1-1) | gate it / leave it | **Gate it.** Fabricated validation evidence is the highest-damage defect in this list, and the fix is non-breaking |
| **P4-4** | Fix `current_regime.json`'s synthetic future date now, or keep it recorded-only? (P1-2) | fix now / flag only / leave | **Flag now** (cheap, ~15 lines), then trace the producer separately. RS2 is already protected, so this is not urgent |
| **P4-5** | Introduce a lockfile? | `requirements.lock` / `uv.lock` / pin in `pyproject.toml` / no | **Yes** for MRI at least — it is the repository whose outputs set another system's discount rates, and reproducibility there is a correctness property, not a convenience |

---

# Already fixed during this work (for the record)

| Defect | Where | How it surfaced |
| --- | --- | --- |
| RS2 ↔ MRI sector vocabulary mismatch → bands never resolved | `rs2_data.py` | Building this plan (P0-1) |
| Implied-rate solver bisected a domain where the Gordon terminal is undefined | `anchors/cost_of_capital.py` | New test |
| Conditioning ladder's **unconditional** rung bypassed the min-observations floor | `anchors/multiples.py` | New test |
| Beta mixed `ddof=1` covariance with `ddof=0` variance | `anchors/cost_of_capital.py` | New test |
| tz-aware as-of vs naive stored dates crashed the build | `evaluation/asof.py` | Empty-DB run |
| Percent-quoted FRED yields surfaced as a **445% "10-year yield"** | `anchors/config.py` units contract | Artifact review |
| **Replay published live anchors** from reconstituted history | `replay.py` | Test-suite artifact diffing |
| **A thin/empty DB overwrote good anchors with nulls** | `anchors/service.py` publish guard | Observed live during testing |

---

# Suggested order

1. **P0-2 + P0-3** — one ingest, unblocks the entire growth anchor. Cheapest, largest gain.
2. **P1-1** — the only item producing *confidently wrong* published numbers.
3. **P1-4, P1-3** — two one-line config/regeneration fixes.
4. **P4-1** — the decision that determines whether P0-4 and P0-6 are ever worth pursuing.
5. **P0-4 → P2-1 → P2-2** — supply the aggregate, then actually run the impact audit and the PIT backfill.
6. **P0-7, P1-5, P1-2, P2-3…P2-6** — remaining correctness and coverage items.
7. **P3-x / P4-5** — reproducibility, before the next hand-off.
