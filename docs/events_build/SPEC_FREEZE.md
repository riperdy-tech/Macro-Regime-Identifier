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

## Plan erratum v2.2 — §5.4

Orchestrator-ruled 2026-10-18, after the Opus audit of E1 and **after** the reference-set run (a) (`E1_ACCEPTANCE.md`)
showed one hub event holding unrelated news. It is therefore a rule designed with the reference set in view: the reference
set can no longer test it. **It must be validated on a fresh, blind holdout, not on the reference set.** The runs recorded in
`E1_ACCEPTANCE.md` under v2_2 are information only. `config/events.yaml` is `version: 3`; the rule and its three
parameters sit in the `unratified:` block (`linking`, `entity_background`), flagged `unratified: true`, for the operator to
ratify or change. `linking: v2` keeps plan 5.4 as transcribed (and reproduces run (a) as recorded: false-split 37/45,
false-merge 123/131 on the fixture-only pool); `linking: v2_2` is the default.

**(a) One report supplies both.** A classified report links to an event only if ONE report already in the event supplies
both the shared entity AND (the shared theme+direction OR title Jaccard >= 0.3). No union-of-event matching (v2 compared the
report's entities against the union of all the event's entities, and its themes against the union of the event's themes,
which let an event grow by chaining). The 72-hour horizon (from the event's latest report) and the unclassified-report
rule (title Jaccard >= 0.4 to a report already in the event) are unchanged. A report that the union reading would have
joined and this reading refuses carries the flag `union_link_rejected:<event_id>`.

**(b) Background entities do not count.** A shared entity counts only if it appears in at most
`entity_background.max_share` (1%) of the classified items first seen in the `entity_background.days_before_pool` (28)
days strictly before the start of the pool window (items first seen at or before `as_of - 7 days`, judged with the
classifications made at or before that instant: as-of relative, no look-ahead). It applies uniformly to every entity key
(resolved tickers, normalised names, countries) with no exemptions, and the generic stop list does not grow. If that
background holds fewer than `entity_background.min_items` (300) classified items, each classified report carries
`background_thin` and the test is applied with what exists (annotate, do not gate). An entity dropped by the test stays on
the event record, with `link_exclusion: background_share` and the report flag
`entity_not_linkable:background_share:<name>`.

**(c)** "Resolved entity" means resolver output or, for an unresolved or ambiguous name, the normalised name, in both cases
subject to (b).

**§5.5 unchanged.** The new-development rule is kept as written (shared entity with the event, no shared theme-direction,
title Jaccard < 0.2 with every report, joins nothing => new event with `related_event_ids`); its shared entity is the one
that survives (b).

**Expected gate failure, needs an operator ruling.** The Aden palace report shares its actor and themes with the Houthi
event, so it is expected to **link into** the Houthi event rather than start a related one; `E_HOUTHI_ADEN_PALACE` is then
expected to fail its zero-tolerance gate. No parameter is tuned toward it.

Also fixed in phase 3b (not erratum items): `AGGREGATOR_HOSTS` and the publisher-suffix regex moved from `builder.py` to
`events.yaml#unratified` (`aggregator_hosts`, `publisher_suffix_pattern`); batch ties on `first_seen_at` break on
`published_at` (a missing one last) and then `news_id`, not a hash; severity and confidence go through `_num` so `0.0` is a
value.

## Plan erratum v3 (Stage 1 ships as a candidate register)

Ruled 2026-10-18 with operator approval (Phase 4a; `BRIEF_P4A.md`). It follows the E1 failure (`E1_ACCEPTANCE.md`,
`E1_FAILURE_CONSULTATION.md` with its Corrections), the two outside reviews and the Opus audit of them
(`EXTERNAL_REVIEWS_E1.md`).

1. **Stage 1 production is the candidate register** (`CANDIDATE_REGISTER_SPEC.md`), not corroborated events. A candidate is
   never called a confirmed event in any field or string. Plan sections 5.6/5.7 (corroboration to `corroborated` /
   `rejected_uncorroborated`) are superseded for production; **v2.2 (above) is retained as a comparator only**, and v2
   (plan 5.4 as transcribed) as the first baseline.
2. **Single-source candidates are shown.** The plan's "`rejected_uncorroborated` ... hidden by default" (section 5.6) is
   superseded: they are displayed as `single_source` and are not removed after 72 hours.
3. **Two-label model** for the holdout labels: episode and storyline (`LABELLING_GUIDE_V2.md`). The Aden palace strike is its
   own episode under the Houthi storyline.
4. **Linking is to be judged by the v2.3 pre-registration** (`V23_PREREGISTRATION.md`): anchors not union, themes excluded
   from identity, no hard frequency cutoff, `published_at` for E2 onsets, a blind contiguous holdout, one run. The v1.1
   pairwise gates and zero-tolerance examples are regression information only. The seen reference set is a development set.
5. **E2 de-duplication** by (instrument, reference close date, window).
6. **Persistent identity** (`events/state.py`): the 7-day pool is a discovery window; ids are stable from creation; no silent
   retrospective reassignment.
7. **`canonical_title`** strips a trailing " - X" only for aggregator hosts (fixes the code against plan 5.2; 17 of 944 pooled
   items were affected).

`config/events.yaml` is not changed by this erratum (its version and pinned values are untouched); the new arm parameters
live in a separate `config/events_v23.yaml` created in Phase 4b before the freeze commit.
