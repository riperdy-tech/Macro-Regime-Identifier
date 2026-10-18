# Specification freeze record (MRI_EVENTS_PLAN v2 section 9 step 2)

Date: 2026-10-18. Branch `feat/mri-events`. No E1/E2/E3 code exists at this point; `src/` is unchanged by this phase.

## Freeze commits (each predates any E1 code)

| Commit | What |
|---|---|
| `a5895f3` | `config/events.yaml` (`frozen_at` in that file stays `PENDING`; a file cannot carry its own commit hash, so the freeze hash is this table) |
| `efe90f8` | `config/event_theme_map.yaml` |
| `a954fb6` | `scripts/validate_events_spec.py` + `tests/test_validate_events_spec.py` |
| `ad96231` | `tests/fixtures/events_reference/` (items, labels, LABELLING.md, selector) |

Validator: `python scripts/validate_events_spec.py --db data/macro_engine.duckdb` prints `events spec: OK`; the
live-store test runs under `MRI_STORE_CHECKS=1`. The firewall check (`events_firewall_hash.py --check`) was OK after
this phase.

## UNRATIFIED items for the operator

Everything below is in `config/events.yaml` under `unratified:` (or marked `status: UNRATIFIED`). None is used by
any main block. A PROPOSED value is a suggestion only.

1. **Official-source domains** (plan 5.6 names the Fed, BLS, DOL, EIA). Seen in the store: www.federalreserve.gov,
   www.bls.gov, www.dol.gov, www.eia.gov (feeds `federal_reserve_press`, `federal_reserve_speeches`, `bls_latest`,
   `dol_releases`, `eia_energy`). Ratify the list.
2. **Trading-day calendar source.** Plan says US trading days; no holiday table exists (E0.4 uses weekday lags with
   slack). **RULED 2026-10-18 (orchestrator): an NYSE closures table kept in the repository; E2 implements it.**
3. **Timezone that turns `published_at` (UTC) into the US onset date.** Proposed America/New_York.
4. **RULED 2026-10-18 (orchestrator): plain `<SYM>` close.** Was: price series for "log return", `<SYM>` close or `<SYM>#adjclose`. Not decided. No `#adjclose` series is
   stored yet for any instrument (the live pulls predate E0.4 storing it); choosing adjclose needs a re-pull first.
5. **Theme direction scoring.** The map is keyed by theme, the classifier gives a direction per theme. Proposed:
   score a theme only when its direction is positive.
6. **Which of an event's themes are scored.** Proposed: every market-checkable theme the event carries.
7. **RESOLVED by Plan erratum v2.1 below.** Was: P_ref and after-close news (a plan inconsistency, needs a ruling). Plan 6 excludes the onset-date close
   because news arrives after the US close, "to avoid counting a move made before the news". For after-close news
   the onset-date close is already before the news; excluding it puts that day's pre-news move inside `move_k`.
   Transcribed as written; behaviour not chosen.
8. **Session counting for k** when stored observations have gaps. Proposed: k-th observed session after P_ref.
9. **MAD = 0.** Proposed: `data_unavailable`, reason `mad_zero`.
10. **WTI series.** Plan: FRED + Yahoo tail. The tail chains CL=F returns onto the last DCOILWTICO level; CL=F holds
    about 67 rows (< 250). Proposed: baseline on DCOILWTICO, tail sessions flagged `provisional`.
11. **Per-series staleness limits for FRED series.** Proposed default 3 weekdays (as E0.4). DCOILWTICO was 2 days
    behind the others at freeze.
12. **Publisher-suffix parsing rule** and 13. **canonical-title normalisation** (HTML entities, curly quotes).
14. **Entity relevance source** (classifier field) and that entities without one are not linkable.
15. **E1 acceptance protocol** (pairwise metrics, daily replay, zero-tolerance membership for the three examples and
    Aden): defined in `tests/fixtures/events_reference/LABELLING.md`, reference set v1.1 (rates over the non-example
    events, core and peripheral members, run (a) as-stored and run (b) as-if-all-classified). Ratify, or change before
    E1 results exist. Run (b) needs the operator's approval to classify 158 items (see LABELLING.md).

## Plan items left open (not parameters)

- Plan 5 says "exactly one corroborated event" per example; ratify the interpretation in item 15.
- Plan whole-feed counts (Houthi 35, CoreWeave 7, Fed 6) differ from the reference set (v1.1 core members 27 / 4 / 3,
  with peripheral 41 / 5 / 5, under a campaign reading of "event"); see LABELLING.md.
- **The plan's "Fed / hikes" example is labelled as the single CPI release event `E_CPI_SEP`.** Its Reuters item carries the
  rate-hike odds ("Fed rate hike this month back on table"); no separate Fed-speech event exists in the reference set.
  The FOMC minutes (10-07) and the Cook panel are their own events.
- Theme names: all 18 names in `config/news_themes.yaml` `macro_themes` match the plan's table exactly. No mismatch.

## Entity resolver change from Phase 1 (deliberate)

Phase 1 fix 2 judged a 5-letter alphabetic ticker by the Nasdaq fifth-letter convention only when the exchange was
Nasdaq or OTC. Phase 1 fix 3 (`70a5f0e`) extended that to an **unknown exchange** (stocks.json-only lines, which
carry no exchange), so a 5-letter ticker with unknown exchange is now judged by its fifth letter instead of being
taken as common. Effect: such lines ending in P/W/U/... are demoted from the named set; e.g. SPAC units and rights
stay out. This is intended and tested (`tests/test_event_entities.py`); E1 should not assume the earlier behaviour.

## Notes for the E1 brief

1. **Count one company once, not once per ticker.** The resolver returns a company with several lines (class A/B,
   ADR). Linking, entity relevance and "shared entity" in plan 5.4 must work on the company, not on each ticker, or a
   two-line company counts twice.
2. **E1 must use `tickers`** from the resolver (the common-line decision), not `other_lines`, and not the raw
   `named` set. Lines carrying `listing_unconfirmed`, `market_cap_unknown`, `common_lines_unverified` or
   `name_conflict` arrive as flags; annotate them on the event, do not drop the entity.
3. **Stale-ticker lines must not turn the confirmation-price report `ok=False`.** A renamed or delisted ticker in a
   named list (stocks.json lines absent from SEC) should be reported as `stale`/`missing` for that symbol; only a
   failed pull or a failed restatement re-pull sets `ok=False` (Phase 1 fix 3 B). Do not feed such tickers in as hard
   requirements.
4. E1 reads the frozen `config/events.yaml` values only, and the acceptance run is the one defined in LABELLING.md,
   run once; labels are not edited after it.

## Plan erratum v2.1 (P_ref)

Orchestrator-ruled 2026-10-18. This is an **erratum**: it corrects a misstated mechanism in plan section 6, it does not
tune a parameter. Section 6 says `P_ref` is the last daily observation dated before the event's onset date and gives the
reason that most items arrive after the US close, so the onset-day close "is excluded" to avoid counting a move made
before the news. For after-close news that reasoning is backwards: the onset-day close already precedes the news, and
excluding it puts the pre-news onset-day move inside `move_k` (this was UNRATIFIED item 7).

**v2.1 rule.** `P_ref` = the last session close **at or before the onset instant**, where the close is **16:00
America/New_York**. After-close news therefore uses the onset-day close; in-session news uses the prior session close.
The onset instant is the event's onset (first `published_at`, UTC) as an instant, not a date.

`config/events.yaml` is bumped to `version: 2`: `e2.p_ref_rule` is `last_session_close_at_or_before_onset_instant`, with
new sibling fields `e2.p_ref_close_time_local` (16:00) and `e2.p_ref_timezone` (America/New_York), all with
`plan_ref: "§6, erratum v2.1"`. `scripts/validate_events_spec.py` pins the version and the three values. v1 (the plan
wording as transcribed) is in git history at `a5895f3`. The plan document itself was not edited. UNRATIFIED item 3 (the
timezone for the onset date) is superseded for P_ref by this rule.

Also recorded 2026-10-18:

- **Item 2, trading calendar:** an NYSE full-day closures table kept in the repository; E2 implements it.
- **Item 4, price series:** plain `<SYM>` close for log returns (not `#adjclose`).
- **Generic-entity stop list (plan 5.4):** referenced from `config/events.yaml` (`e1.link.generic_entity_stop_list`
  points at `config/entity_aliases.yaml#generic_entities`); the values live only in that file and the validator checks the
  reference resolves.
