# MRI as the macro sensing window — the event register (MRI-15)

**Version 2, 2026-10-17.** Supersedes v1 (`7e60add`). Owner: the MRI session. Status: plan only,
nothing built. v2 answers an outside review (ChatGPT, 2026-10-17), whose disposition was
"conditional approval; revise E1–E3 before implementing". §10 maps each review finding to where
v2 answers it.

## 1. Why this plan exists

The operator's request (2026-10-15):

> MRI, being the macroeconomic capturing window, needs to take on a broader role to identify
> quickly the shift in financial environment. These do not only come from one-dimensional oil
> data, but also from news — CoreWeave's bond yields rising, the Fed chair talking about rate
> hikes, a Houthi missile hitting a Saudi airport.

It should respond fast. It should also not wobble, meaning it must not re-upgrade a name it
downgraded a few days earlier.

MRI `a54baa4` (2026-10-15) fixed the market-data alarms: daily oil tail, shock-episode memory, and
per-source freshness. This plan covers the news side.

**What each stage delivers.** These claims are kept separate on purpose:

| Stage | What it delivers | What it does NOT claim |
|---|---|---|
| 1 | A persistent, attributable record of each material event, shown on the stock pages the next morning: what was reported, whether it is corroborated, what the market did, and who is exposed. | It does not change any verdict, and it does not solve the wobble problem. |
| 2 | Measures whether exposed names behave differently, and whether verdict reversals cluster around events. | It changes nothing. |
| 3 | A separately approved decision rule, including when reduced risk may be restored. | Only this stage can solve the no-wobble requirement, and only after it passes its own tests. |

## 2. What exists today (measured 2026-10-15/16)

The cloud MRI news feed (`run-history` branch) already holds all three examples:

| Example | In the feed | Classified |
|---|---|---|
| CoreWeave | 10-14 20:46 UTC "CoreWeave shares tumble as bond yields near 15%…"; 10-15 "credit default swaps hit distress levels" — 7 items | 3 of 7 |
| Houthi / Saudi | 10-14 21:12 UTC "Houthis vow more attacks on Saudi airports, oil facilities after Aramco HQ hit" — 35 items, mostly Reuters | 8 of 35 |
| Fed / hikes | 10-14 21:45 UTC "US inflation heats up in September; Fed rate hike this month back on table" — 6 items | 2 of 6 |

| Piece | State |
|---|---|
| News intake | Cloud only. About 100 items/day from about 20 RSS feeds and Finnhub. GDELT daily is effectively dead (rate-limited, circuit open). `source_group` is a **topic** label (`macro_general`, `energy_commodities`, …), **not a publisher**. |
| Classification | DeepSeek `deepseek-v4-flash`, capped at 60/day; October coverage 985 of 1,721 (57%). Each item gets 18 macro themes with direction, sector impacts, entities (free-text names, no tickers), severity and confidence. |
| Local store | News ends 2026-05-30. `import-news-history` exists but has never been run locally. |
| Grouping code | `news/selection.py` `assign_event_ids` / `_jaccard` is a token-set near-duplicate filter that rations classification. It is **not** an event builder. Only its tokeniser and Jaccard function are reused. |
| Market data | FRED daily series in MRI (about a 1-day lag). Yahoo oil in `market_observations`. MRI `sector_proxy_prices` is stale locally since 09-18. |
| Taxonomy | The screener owns it: `stocks.json` industry/sector, `industry_taxonomy.get_taxonomy_profile`, `GICS_TO_MACRO_ID`. |
| Consumers | v4 reads only DGS10 and `elevated_volatility_ahead` (AK.78). The v4 nightly shadow already keeps a reversal log listing active MRI flags. |

## 3. Rules this plan keeps

1. **News never alters a number** (P0.0 §3.5) through Stages 1–2. Stage 3 needs a new operator
   ruling for each event type.
2. **Contamination rule** (NEWS_PART1 §5; `news/provenance.py`). Only `origin=live`
   classifications made within 14 days of publication count as evidence.
3. **v4 inputs** (AK.78). Anything v4 reads, annotations included, is registered with the
   coordinator first. Stage 1 does not touch v4.
4. **Single owner per field.** MRI owns events and market checks. The screener owns the ticker
   taxonomy and the UI. Handoffs are files only.
5. **Annotate, never silently gate.** Warning flags stay flags. A check that produces flags is
   never presented as validation.

## 4. Event model — four independent fields

| Field | Values | Question it answers |
|---|---|---|
| `event_status` | `detected`, `corroborated`, `rejected_uncorroborated` | Is the event independently reported? |
| `lifecycle_status` | `active`, `decaying`, `closed` (with `close_reason`: `quiet`, `sunset`) | Is the event still live? |
| `market_response` | `aligned`, `opposed`, `mixed`, `no_signal`, `pending`, `data_unavailable`, `display_only` — one per theme, plus per-instrument detail | What did the market do in the window? |
| `exposure` | per ticker: `named`, `peer`, `sector` | Who is exposed? |

**A corroborated event stays a valid event whatever the market does.** The UI may say "the market
has not shown the expected reaction". It never says or implies that the event did not happen.

## 5. E1 — event builder (a new, deterministic module)

All parameters live in `config/events.yaml` and are frozen before any forward data is read.

1. **Pool.** Live items first seen in the last 7 days.
2. **Publisher.** The `source_url` domain. For aggregator headlines, the trailing "– Publisher"
   suffix of the title instead.
3. **Syndication collapse.** Items that share a canonicalised title (lower case, publisher suffix
   stripped), or have a title Jaccard ≥ 0.6, are one **report**. A wire story republished by many
   outlets counts once. The canonical item is the earliest-seen classified item, otherwise the
   earliest-seen item.
4. **Linking a report to an open event.** All three must hold:
   - it shares at least one resolved entity (relevance ≥ 0.5; generic entities such as "United
     States government" excluded through a stop list);
   - it shares at least one macro theme with the same direction, **or** its title Jaccard with an
     event report is ≥ 0.3;
   - it arrived within 72 h of the event's latest report.

   An unclassified report links only through title Jaccard ≥ 0.4 to a report already in the event.
5. **A new development is a new event.** A report that shares an entity but has no shared theme
   and a title Jaccard < 0.2 starts a new event, linked through `related_event_ids`. For example,
   the Houthi strike on the Aden palace is separate from the strikes on Saudi airports.
6. **Corroboration.** An event becomes `corroborated` when either:
   - two or more **distinct publishers** carry it after syndication collapse; or
   - one official source carries it: the Fed, BLS, DOL or EIA feeds.

   If neither happens within 72 h, it becomes `rejected_uncorroborated`. It is kept for audit and
   hidden by default.
7. **Event record.** Event id; onset (the first `published_at`); `first_available_at` (the first
   `first_seen_at`); themes with direction; resolved and unresolved entities; sector impacts;
   report and publisher counts; maximum and mean severity; the canonical headline as title (no
   new model call); `related_event_ids`.

**Acceptance (E1).** A hand-labelled reference set built from the 10-08..10-16 feed (about 300
items, labelled before the builder runs) must reach:

- **false-merge rate ≤ 5%** — two labelled events grouped as one;
- **false-split rate ≤ 10%** — one labelled event split into several;
- each of the three examples as exactly one corroborated event, and the Aden palace story as a
  separate, related one.

## 6. E2 — market response

**Theme map.** Each of the 18 themes is either market-checkable, with an expected sign per
instrument, or `display_only`. Families are independent market dimensions; instruments in the same
family count once.

| Theme (direction) | Primary family: instrument, expected sign | Secondary family |
|---|---|---|
| monetary_tightening | rates: DGS2 ↑ | equity: SPY ↓ |
| monetary_easing | rates: DGS2 ↓ | equity: SPY ↑ |
| inflation_pressure | breakeven: T10YIE ↑ | rates: DGS2 ↑ |
| disinflation | breakeven: T10YIE ↓ | rates: DGS2 ↓ |
| yield_curve_steepening | curve: T10Y2Y ↑ | — |
| yield_curve_flattening | curve: T10Y2Y ↓ | — |
| credit_stress (market-wide) | credit: BAMLH0A0HYM2 ↑ (BAA10Y same family) | vol: VIXCLS ↑ |
| credit_easing | credit: BAMLH0A0HYM2 ↓ | — |
| energy_supply_shock | oil: WTI (FRED + Yahoo tail) ↑ | energy equity: XLE ↑ |
| geopolitical_risk | vol: VIXCLS ↑ | oil: WTI ↑ only when co-tagged energy_supply_shock (scored under that theme, never twice) |
| financial_stability_risk | vol: VIXCLS ↑ | banks: KBE ↓ |
| growth_acceleration / growth_slowdown, labor_strength / labor_weakness, fiscal_expansion / fiscal_drag, commodity_pressure | `display_only` in v2 — no reaction specific enough to test; revisited only with Stage 2 evidence | — |

**Company-specific events.** These are events with a resolved named entity, such as CoreWeave.
- The check is the named stock's 2-session return minus its sector ETF's (abnormal return),
  expected sign set by the theme.
- It is recorded as `equity_reaction_only`.
- It never claims the company's bonds or CDS moved. `bond_data: unavailable` is stated on the
  record.
- A market-wide credit move is reported separately and never stands in as evidence about the
  company.

**Calculation, per instrument:**

- **Transform.** Yields, spreads, breakevens and the curve: change in basis points. Prices, ETFs and
  WTI: log return in %. VIX: log change in %.
- **Reference.** `P_ref` is the last daily observation dated **before** the event's onset date (US
  calendar). Most items arrive at 20:00–22:00 UTC, after the US close, so the onset-day close is
  excluded. That avoids counting a move made before the news.
- **Primary window.** k = 2 sessions after `P_ref`. Moves over k = 1 and 3 are recorded for
  information and never set the status. Only one window is tested, so there are no multiple looks.
- **Standardisation.** z = move_k ÷ robust SD (MAD × 1.4826) of all overlapping k-session moves
  over the 504 sessions ending at `P_ref`. Data after `P_ref` is never used. At least 250
  observations are required, otherwise `data_unavailable`.
- **Instrument result.** `aligned` if z ≥ +2 in the expected direction; `opposed` if z ≥ +2 in the
  opposite direction; otherwise `no_signal`. Missing data, or data past the instrument's lag limit,
  gives `data_unavailable`. A k-th session not yet observed gives `pending`.
- **Theme result.**
  - `aligned` if the primary family is aligned, or two independent families are aligned and none
    is opposed;
  - `opposed` if the primary family is opposed and no family is aligned;
  - `mixed` if families are both aligned and opposed;
  - `no_signal` otherwise;
  - `data_unavailable` if the primary family is missing.

  A VIX spike alone can never mark an energy event aligned, because oil is the primary family for
  energy.
- **Provisional data.** A result built on provisional inputs (the oil tail) is flagged
  `provisional` and recomputed when FRED publishes.
- **Validity.** The window result is a fixed historical fact, `response_at_onset`, and is never
  silently extended. A separate daily field, `response_current`, is true while the primary
  instrument's cumulative move since `P_ref`, in the expected direction, is still ≥ 50% of its
  window move. It feeds the lifecycle only.

## 7. E3 — lifecycle (trading days, US calendar)

| From | To | Rule |
|---|---|---|
| — | `detected` | The first classified report forms the event. |
| `detected` | `corroborated` + `active` | The corroboration rule passes (§5.6). This can happen the same day. |
| `detected` | `rejected_uncorroborated` | No corroboration within 72 h. |
| `active` | `decaying` | 5 consecutive trading days with **no new linked report** and `response_current` false. |
| `decaying` | `active` | A new linked report arrives, or `response_current` turns true. Same event id. |
| `decaying` | `closed` (`quiet`) | 10 consecutive trading days in `decaying` with no new report. |
| any open state | `closed` (`sunset`) | 60 calendar days since onset. |
| `closed` | — | Never reopens. Later reports start a new event linked through `related_event_ids`. |

**Tests.** Onset and corroboration on the same day; no flicker after one quiet day; fade only
after 5 quiet days; re-activation on new coverage; a response that has gone stale does not keep an
event alive; sunset closes the event. Plus concurrent events, mixed signals, repeated syndicated
reporting, and stale inputs.

## 8. E4–E5 — exposure and display

**Entity → ticker** (E0.3):
- Normalise the name by lower-casing, stripping punctuation, and removing legal suffixes (Inc,
  Corp, Corporation, Holdings, plc, Ltd, Co, Class A/B).
- Match it **exactly** against `stocks.json` names and SEC `company_tickers_exchange.json` titles,
  or through a curated `config/entity_aliases.yaml`.
- More than one candidate means `ambiguous`, and it is rejected.
- Only exact and alias matches enter the `named` tier. Unresolved names stay as names with no
  ticker.

**Exposure (screener side):**
- `named`: the resolved tickers.
- `peer`: the same cluster, through `get_taxonomy_profile`.
- `sector`: classifier sector impacts at confidence ≥ 0.6 and |impact| ≥ 0.3.

Contradictions found by `news/guardrails.py` are shown as `exposure_flags` on the event. They are
warnings, not validation.

**Artifacts.** `outputs/event_register.json` (MRI-15, schema 1, additive). The screener's
`public/data/macro_events_by_ticker.json`. A stock-page panel built on the `GeminiPanel` pattern.
An event list on the macro page. Telegram alerts are deferred (D4).

## 9. Work plan and order

| Order | Step | Done when |
|---|---|---|
| 0 | **Ownership and baseline.** Confirm who owns local news hydration (rev23 D2 / AK.11). Record sha256 of the protected artifacts (§9.1). | Owner named in the record; baseline hashes committed. |
| 1 | **E0.1 hydration.** Run daily, before the events job. | The local store holds the 10-14..16 items; shock narratives are non-empty. |
| 1 | **E0.2a classification cap 60 → 150/day.** Measure actual DeepSeek token cost per day. | 14-day rolling classified share ≥ 85%; measured cost recorded. |
| 1 | **E0.2b GDELT pacing repair.** Separate item. | 14-day rolling GDELT success rate ≥ 90% of scheduled pulls. |
| 1 | **E0.3 entity resolver** (§8). | Tests: CoreWeave → CRWV, Dell Technologies → DELL, Aramco → unresolved, an ambiguous name → rejected. |
| 1 | **E0.4 confirmation prices.** Yahoo daily for SPY, XLE, KBE, the 11 sector ETFs and named tickers, in `market_observations`, with lag limits. | Freshness tests. |
| 2 | **Specification freeze.** `config/events.yaml` and the theme map committed. The E1 reference set labelled. | Committed **before** E1 code. |
| 3 | **E1 → E2 → E3 → E4 → E5**, each with its tests. | §5–§8 acceptance criteria; protected artifacts unchanged. |
| 4 | **Shadow run, 2 weeks.** Daily register and display, with operator spot checks. | False merges/splits and state flips reviewed; then D4 is decided. |
| 5 | **Stage 2.** Commit `MRI_EVENTS_TEST_PLAN.md` before any forward outcome is read (§11). Through the coordinator, add event ids to v4's reversal log and a `macro_events` row annotation. | The test plan is versioned and its commit predates the data read. |
| 6 | **Stage 3.** Per event type that passes, with an operator ruling and the coordinator. Includes the re-risk rule (§11.3). | Its own tests. |

Steps marked 1 can run in parallel once step 0 is done. The specification freeze (step 2) can be
written while step 1 runs.

### 9.1 Protected artifacts (the firewall test)

The events job must leave these unchanged, compared after canonicalising `built_at`,
`source_run_id` and other run metadata:

- `current_regime.json`, `current_sector_ranking.json`;
- `cost_of_capital_anchor.json`, `long_run_growth_anchor.json`, `sector_multiple_bands.json`;
- the numeric fields of `shock_register.json`;
- the `raw_observations` rows for DGS10, DFII10 and T10YIE (v4's risk-free inputs).

## 10. Changes from v1 (review mapping)

| Review finding | v2 answer |
|---|---|
| P0-1: event status mixed with market response | §4, four separate fields. The "contradicted" wording is removed; a corroborated event stays valid. |
| P0-2: market test underspecified | §6: a theme map for all 18 themes, transforms, `P_ref` before onset, one primary window, a k-session robust z from pre-event data only, per-instrument results, a family aggregation rule, provisional handling, and response validity. |
| P0-3: clustering overstated | §2 and §5: a dedicated builder. Only the tokeniser and Jaccard are reused. Publishers counted, not topic groups (`source_group` turned out to be topical). Syndication collapse. Merge and split acceptance. |
| P0-4: lifecycle not a state table | §7 transition table; `response_current` stops an old confirmation from keeping an event alive. |
| P0-5: wobble not solved | §1 stage claims; §11.3 re-risk rule required before Stage 3. |
| Statistics (H1, H2, sample size) | §11. |
| P1: CoreWeave evidence | §6 `equity_reaction_only` and `bond_data: unavailable`. |
| P1: guardrails are flags | §3.5 and §8 `exposure_flags`. |
| P1: entity resolution | §8, exact and alias only; ambiguous names rejected. |
| P1: hydration overlap | §9 step 0. |
| P1: E0.2 bundled | §9, E0.2a and E0.2b with rolling-window criteria. |
| P1: Telegram too early | D4 deferred until after the shadow run. |
| P2: byte-identity scope | §9.1. |

## 11. Stage 2 — measurement contract (outline; full text in `MRI_EVENTS_TEST_PLAN.md`)

1. **H1, primary.** Ten-session cumulative abnormal return (stock minus its sector ETF) for
   `named` and `peer` names of corroborated events in risk-off themes. Measured from the first
   close after `first_available_at`, so no look-ahead is possible. Expected sign: negative.
   - **Controls.** Unexposed names in the same sector, matched on size decile and 60-day momentum.
   - **Inference.** Standard errors clustered two ways, by event date and by ticker. Overlapping
     events are handled through that clustering, and each ticker-event pair counts once.
   - **Pooled.** The primary test pools across risk-off types. Per-type estimates are exploratory.
   - **Secondary outcomes,** declared in order: 20-day realised volatility, then 20-day maximum
     drawdown.
2. **H2.** The reversal rate per exposed stock-night, against matched unexposed stock-nights over
   the same dates. A rate, never a count.
3. **Re-risk rule (Stage 3 prerequisite).** Define what new evidence allows a reduced position to
   be restored: the event closes; the event's market response reverses (`response_current` false
   and the primary instrument back within 1 SD of `P_ref`); or a fresh verdict on new filing
   evidence. A plain cooldown is not enough.
4. **Gates.**
   - **First read:** ≥ 40 corroborated events with at least one named or peer ticker, and
     ≥ 12 weeks.
   - **Second read:** only if the first is inconclusive, at twice the sample.
   - **Alpha:** split 0.005 / 0.048 across the two reads.
   - **Small samples:** event types below the sample are reported as **unvalidated**, never as
     "no effect".
   - **No automatic authorisation:** passing a gate does not authorise Stage 3 by itself.

## 12. Decisions

| # | Decision | Status |
|---|---|---|
| D1 | News never alters a number through Stages 1–2 | Recommended: approve |
| D2 | Classification to 150/day (E0.2a) and GDELT repair (E0.2b), as separate items, with measured cost | Recommended: approve, conditional on measurement |
| D3 | An events job at about 08:30 KST, triggered by a successful hydration, with freshness checks and a failure report. A morning update, not intraday detection. | Recommended: approve |
| D4 | Telegram alerts | **Deferred** until after the 2-week shadow run, then with deduplication, cooldown and expiry |
| D5 | Additive, versioned `event_register.json` and `shock_register.json` handed to the screener through `mri_sync.py` (the existing CI approval item) | Recommended: approve |
