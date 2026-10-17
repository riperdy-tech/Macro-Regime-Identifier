# MRI as the macro sensing window — the event register (MRI-15)

Drafted 2026-10-16 for operator approval. Owner: the MRI session. Status: plan only, nothing built.

## 1. Why this plan exists

The operator's request (2026-10-15):

> MRI, being the macroeconomic capturing window, needs to take on a broader role to identify
> quickly the shift in financial environment. These do not only come from one-dimensional oil
> data, but also from news — CoreWeave's bond yields rising, the Fed chair talking about rate
> hikes, a Houthi missile hitting a Saudi airport.

It should respond fast. It should also not wobble, meaning it must not re-upgrade a name it
downgraded a few days earlier.

The work done on 2026-10-15 (MRI `a54baa4`: daily oil tail, shock-episode memory, per-source
freshness) fixed the market-data alarms. It did not touch news. This plan covers the rest.

**What a good outcome looks like.** By the morning after a material event, the system holds a
dated, corroborated record of it, which says:

- what happened;
- which macro themes it touches;
- whether markets confirmed it;
- which industries and tickers are exposed.

That record shows on the stock pages. Later, if forward evidence supports it, it changes position
sizes or values. It switches on fast and switches off slowly.

## 2. What exists today (measured 2026-10-15/16)

**The raw material is already collected; nothing turns it into events.** The cloud MRI job's news
feed (`run-history` branch) contains all three of the operator's examples, within hours:

| Example | In the feed | Classified by the model |
|---|---|---|
| CoreWeave | 10-14 20:46 UTC "CoreWeave shares tumble as bond yields near 15%…", 10-15 "credit default swaps hit distress levels" — 7 items | 3 of 7 |
| Houthi / Saudi | 10-14 21:12 UTC "Houthis vow more attacks on Saudi airports, oil facilities after Aramco HQ hit" — 35 items | 8 of 35 |
| Fed / hikes | 10-14 21:45 UTC "US inflation heats up in September; Fed rate hike this month back on table" — 6 items | 2 of 6 |

| Piece | State |
|---|---|
| News intake | Cloud only. About 100 items/day from about 20 RSS feeds and Finnhub. The GDELT daily feed is effectively dead (rate-limited, circuit open). |
| Classification | DeepSeek `deepseek-v4-flash`, capped at 60/day. October coverage was 985 of 1,721 items (57%). Output per item: 18 macro themes, sector impacts, entities (free-text names, no tickers), severity, confidence. |
| Local store | News stops at 2026-05-30. `import-news-history` / `scripts/pull_news_history.*` exists but has never been run. `daily_pipeline_local.yaml` sets `news.enabled: false`. |
| Events | None. News becomes per-theme daily scores (`news/scoring.py`) and a read-only narrative on the shocks (`shocks/narrative.py`). Locally that narrative is empty, because there is no news. |
| Market data for confirmation | FRED daily series in MRI. Yahoo oil in `market_observations`. MRI `sector_proxy_prices` (18 ETFs plus SPY) is stale locally since 09-18. Screener `daily_closes.json` covers 391 tickers and 18 ETFs. The v4 shadow holds daily prices for about 9,700 tickers, locally only. |
| Ticker taxonomy | The screener owns it: `stocks.json` `industry`/`sector`, `scripts/industry_taxonomy.py` `get_taxonomy_profile` (cluster), and `GICS_TO_MACRO_ID`. |
| Consumers | v4 reads two MRI inputs only: DGS10 and the `elevated_volatility_ahead` flag (AK.78). The screener site shows the regime and active shocks only. `mri_sync.py` does not copy `shock_register.json`. |
| Real money | KIS is halted. `rn_depth` holds nothing. v4 paper operation is planned for end of October. |

## 3. Rules this plan keeps

1. **News never alters a number** (P0.0 §3.5). It stays in force through Stages 1–2. Only Stage 3
   reopens it, and only for an event type that passes a pre-registered test and gets an operator
   ruling.
2. **Contamination rule** (NEWS_PART1 §5). Only `origin=live` classifications made within 14 days
   of publication count as evidence. Backfilled history is used for tuning only.
3. **v4 inputs** (AK.78). Any new field v4 reads, annotations included, is registered with the
   coordinator and ruled on by the operator before it is built. Stage 1 does not touch v4 or the
   engine freeze (about 10-18/19).
4. **Single owner per field.** MRI owns events and their market checks. The screener owns the
   ticker taxonomy, so the expansion from industry to ticker happens on the screener side. Nothing
   imports across repositories; every handoff is a file.
5. **Annotate, never silently gate.** An event never removes a name from any list.

## 4. Design

The new process **MRI-15, the event register**, sits beside the shock register (MRI-12) and is
published separately.

```
cloud news (run-history) --hydrate--> local store --> [E1] cluster items into events
                                                        |
FRED + Yahoo daily closes -----------------------> [E2] market check per event
                                                        |
                                              [E3] lifecycle: on fast, off slow
                                                        |
                                    outputs/event_register.json (MRI-15)
                                                        |
               screener: [E4] industry -> tickers, [E5] stock-page panel, macro page
               later (Stage 2, via coordinator): v4 row annotation + reversal log
```

- **E1 — events from news.** Code, not the model, groups items into one event. It uses the
  existing near-duplicate title logic (`news/selection.py`), shared themes, and shared entities
  within 48 hours.
  - Unclassified items with matching titles still count towards corroboration. This makes the
    60/day classification cap less binding.
  - An event needs at least one classified item and either two distinct source groups or one
    official source (the Fed RSS feeds).
  - It records: first seen, themes and direction, named entities (resolved to tickers where
    possible), sector impacts, source count, severity, and the headline as its title (no new
    model call).
- **E2 — market check.** Each theme has a fixed basket of instruments:
  - monetary tightening → 2-year and 10-year yields;
  - energy supply / geopolitical → WTI, XLE, VIX;
  - credit stress → high-yield spread, BAA10Y, the named company's stock against its sector ETF;
  - financial stability → VIX, KBE;
  - inflation → 10-year breakeven.

  An event counts as **confirmed** when a basket instrument moves, over 1–3 trading days from
  first sighting and in the theme's direction, by at least 2 standard deviations of its own daily
  moves over the past year. It counts as **contradicted** when the move goes the other way, and
  **unconfirmed** otherwise. Every number comes from market data, never from the model. Corporate
  bond prices are not available. For CoreWeave, the check uses its stock and the high-yield spread
  and records "bond data unavailable".
- **E3 — lifecycle.** Same principle as the shock state machine:
  - an event switches on the day it is corroborated;
  - it is raised to *confirmed* when the market check passes;
  - it fades only after 5 trading days with no new coverage and no live market confirmation;
  - it has a 60-day sunset.

  This is the no-wobble mechanism. Quiet headlines on the next day do not end an event. The
  parameters are fixed in `config/events.yaml` before any forward data is read.
- **E4 — exposure.** MRI publishes, per event: named tickers, sectors (classifier sector impacts at
  confidence ≥ 0.6, checked against `sector_exposure_guardrails.yaml`), and themes. A screener
  script expands that to tickers, using its own taxonomy:
  - *named* — the company itself;
  - *peer* — same cluster;
  - *sector* — same sector.

  It writes `public/data/macro_events_by_ticker.json`. Held names and the live book are listed
  first.
- **E5 — display.** A stock-page panel "Macro events affecting this stock", built on the
  `GeminiPanel` pattern (`lib/data-service.ts`, `lib/desk/useDeskData.ts`, `StockDetail.tsx`, a new
  `EventsPanel.tsx`, `lib/i18n.ts`). The macro page gets an event list. Telegram alerts are
  optional (decision D4).

## 5. Work plan

Each step lands with its own tests. Steps E0.1–E5 are MRI and screener only.

**Stage 0 — foundations**

| Step | Work | Verified by |
|---|---|---|
| E0.1 | Run the existing news hydration locally each day before shocks and events. Schedule an "events" job after the cloud news commit (about 08:30 KST, see D3). | The local store holds the 10-14/15 items above. The shock narratives are no longer empty. |
| E0.2 | Coverage. Raise classification 60 → about 150/day after checking the DeepSeek bill (D2). Fix GDELT pacing so the circuit stops opening daily. | The classified share of October-like volume is ≥ 85%. Telemetry shows GDELT `ok`. |
| E0.3 | Entity → ticker resolver. Deterministic name match against `stocks.json` names and SEC `company_tickers_exchange.json`, with match confidence. Unmatched names are kept as names. | Unit tests: CoreWeave → CRWV, Dell Technologies → DELL, Aramco → unmatched name (not in the universe). |
| E0.4 | Confirmation prices. A local daily Yahoo ingest for the basket ETFs and SPY (standing approval O5), plus named tickers on demand, stored in `market_observations`. | Freshness per instrument stays within its lag limit. |

**Stage 1 — the register and its display**

| Step | Work | Verified by |
|---|---|---|
| E1 | `src/macro_engine/events/` builder plus `config/events.yaml`. New DuckDB tables `events`, `event_items`, `event_market_checks`. | Synthetic-news tests. Replaying the real 10-13..10-16 feed gives one event each for CoreWeave credit, Houthi/Saudi, and Fed hikes. |
| E2 | Market check. | Synthetic prices give confirmed, contradicted and unconfirmed. On real data, the Houthi event is checked against WTI, XLE and VIX, and CoreWeave against CRWV versus XLK plus the high-yield spread. |
| E3 | Lifecycle. | Tests for onset on the same day, fade after 5 quiet days, and no flicker on a single quiet day. |
| E4 | `outputs/event_register.json` (MRI-15, schema 1). The screener-side expansion script. `mri_sync.py` copies `event_register.json` and `shock_register.json` (D5). | Schema test. CRWV and its cluster peers appear in `macro_events_by_ticker.json`. |
| E5 | Stock-page panel and macro-page list. | Browser check of `/t/CRWV` and an energy name. Full screener build. |

**Stage 2 — measurement (forward only, no numbers change)**

- Pre-register, before any forward data is read, in a new `MRI_EVENTS_TEST_PLAN.md`:
  - **H1.** Names exposed to confirmed events show different 5- and 20-day abnormal returns,
    volatility and drawdown from matched unexposed peers, by event type.
  - **H2.** v4 verdict reversals cluster during active events.
- Use the same bar as S4 T3: Newey–West t, Holm correction, the same sign in both halves, and an
  economically large effect. First read at ≥ 30 confirmed events and ≥ 8 weeks.
- Through the coordinator: add active event ids to the v4 nightly reversal log (it already lists
  active MRI flags) and a `macro_events` annotation per row.

**Stage 3 — effect on decisions (operator ruling per event type)**

- Only event types that pass Stage 2 qualify. Candidates:
  - a size cap for names exposed to a confirmed severe event, like the volatility flag;
  - a v4 input change.
- Each one includes an asymmetric re-risk rule, so a name is not re-upgraded while its event is
  still active unless the evidence has changed.

**Order:** E0.1 → E0.3 → E1 → E0.4 + E2 → E3 → E4 → E5 → Stage 2. E0.2 runs in parallel once
approved.

## 6. Decisions for the operator

- **D1.** Keep "news never alters a number" through Stages 1–2? Recommendation: **yes**.
- **D2.** Raise news classification to about 150/day, and fix GDELT? At ~$0.6/month for 60/day
  (unverified), 150/day is likely under $2/month. The bill is checked first.
- **D3.** Timing. The cloud news commit lands at about 07:40 KST, after the local 06:31 MRI run and
  the 07:30 v4 shadow. Recommendation: a separate local events job at about 08:30 KST. v4 then
  sees events the next night, which is acceptable for Stage 1 (display only).
- **D4.** Telegram alert when a new confirmed, high-severity event touches a held or candidate
  name? Recommendation: yes, from Stage 1.
- **D5.** Allow `mri_sync.py` to publish `shock_register.json` and `event_register.json` to the
  screener. This is the existing D2/CI approval item.

## 7. Coordination

- **Coordinator session (rev23):** owns v4 and the reversal log. Stage 1 needs nothing from it.
  Stage 2's annotation and Stage 3 go to it for registration (AK.78).
- **The rev23 D2 "MRI fixes" work** includes "refresh of MRI's local outputs and news store", and
  AK.11 says another session handles it. E0.1 overlaps with that, so the owner must be confirmed
  before building, to avoid duplicate work.
- **Gemini interim arm session:** reads world news through rs2 `world_news.py`. Unaffected.

## 8. Acceptance — when the operator's request counts as met

On a normal morning, for an event like any of the three examples:

1. it appears in `event_register.json` by about 08:30 KST the next day, with the right themes, a
   corroboration count, and its market-check result;
2. the exposed tickers' stock pages show it, named and peer names first;
3. it stays on through quiet days and fades only under the lifecycle rule;
4. nothing numeric has changed anywhere (byte-identity tests), until a Stage 3 ruling.

Before each step is declared done, the full MRI suite and `ruff` pass, and so do the screener
build and tests.

## 9. Risks

- **News volume.** About 100 items/day may miss events that only regional or wire sources carry.
  E0.2 and the corroboration rule mitigate this. The replay test measures it.
- **Entity noise.** The model writes names like "UB (UBS?)". The resolver records its confidence,
  and low-confidence matches never reach the named tier.
- **Confirmation lag.** Some events, such as a slow credit build-up, take longer than 3 days to
  show in prices. Those stay *unconfirmed* but are still displayed. Their window gets tuned only
  on Stage 2 evidence.
- **Overreach.** The temptation is to let events move values early. Stage 3 exists so that only
  measured effects ever do.
