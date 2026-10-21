# E1 reference set: labelling record (plan v2 section 5 acceptance, section 9 step 2)

**Reference set v1.1** (2026-10-18, Opus audit fixes, `docs/events_build/BRIEF_P2_FIXES.md`). v1 is in git history at
`ad96231`; what changed is listed at the end of this file. Written on branch feat/mri-events, before any E1 code exists.
Files: `items.csv`, `labels.yaml`, `build_items.py` (the selector), `classification_state.csv` and
`build_classification_state.py` (the stored classification state, see "Classification state"), `acceptance.py` (the
scorer for the definitions below). None of them imports clustering, Jaccard or classifier-output code.

## Method

- Pool: all 944 `news_items` in the local store with `first_seen_at` in [2026-10-08, 2026-10-17). 300 items were
  chosen: 91 by keyword rule (every item whose title matches the three plan examples, the Aden story, or the broader
  Fed/inflation/Saudi/airport vocabulary; see `EXAMPLE_RE` in `build_items.py`), plus 209 drawn by
  `random.Random(20261018).sample` from the other 853.
- **Seed: 20261018.** Re-running `python tests/fixtures/events_reference/build_items.py <db> <out>` on the same
  store reproduces `items.csv`.
- Labelled by one reader from title, publisher and published date only. No clustering, Jaccard, event, classifier
  or theme code or output was run, read or consulted. The v1.1 core/peripheral decisions and relabels were made from
  titles, publishers and dates only, before `classification_state.csv` was generated. That file reads only
  `news_id`, `classification_status` and `classified_at`; no theme, entity, summary or any other classifier field.
  Labels are not to be tuned after E1 results are seen. Any change after E1 exists is a new reference-set version
  with a recorded reason.
- The newest `first_seen_at` in the set is 2026-10-16T00:49Z (the store has no later items in the window). 24 items
  were published on 10-07 and first seen on or after 10-08; one has no `published_at`. The pool is defined by
  first_seen, as in plan section 5.1.

## The label rule

An **event** is one real-world incident, release or continuing story, identified by who did what to whom (or which
release). Two items share an event label when the headline of each reports that incident, or reports a consequence or
reaction that the headline itself attributes to it (casualties, airline suspensions, advisories, a named market or
oil move, an official response). A continuing campaign by the same actor against the same target set is ONE event
across days (this is how the plan counts the Houthi/Saudi example); an attack on a different target in a different
place, or a third party's own decision, is a separate event listed under `related`. Not enough: the same country,
company or topic in the headline; a feature or analysis that does not name the incident; a roundup.

The same width applies to a data release: one named release (CPI, PPI, FOMC minutes) plus its preview and the market
reaction the headline attributes to it is ONE event; a different release is a different event listed under `related`
(PPI is not the CPI event, as the Aden palace attack is not the Saudi airport campaign). The plan's "Fed / hikes"
example is labelled as the single CPI release event `E_CPI_SEP` (its Reuters item carries the rate-hike odds).

- `NONE`: real news about something no other item in this set belongs with. Each NONE item is its own one-item event.
- `noise`: not news (advice columns, market previews, earnings transcripts, roundups, promotions). Also a
  one-item event each.
- `report` (`report_label`): items that are the same report republished: identical headline (ignoring the
  " - Publisher" suffix) or the same article retitled, judged from the headline. Different outlets that rewrote the
  same fact (CNBC and oilprice on the Houthi vow) are different reports. Every multi-item report group also has one
  shared event label (7 groups).

### Core and peripheral members (v1.1)

Every member of an event is `core` unless `labels.yaml` marks it `membership: peripheral`.

- **core**: the headline itself reports the incident or release, or a consequence or reaction it attributes to it, as
  its main claim.
- **peripheral**: the headline (a) names the incident only in a clause beside another story, (b) is commentary, a
  newsletter, an explainer or a feature about it, (c) does not name the incident or its attacker though it plainly
  belongs to the story, or (d) joins two incidents in one headline. Peripheral members are excluded from every pair,
  every rate and every gate: E1 may put them in the event or leave them out, and neither counts as an error. They are
  also never an "outsider" in another event's cluster.

The decision was made from title, publisher and date only. Peripheral members (22 items):

| Event | Peripheral | Reason |
|---|---|---|
| `E_HOUTHI_SAUDI` (14) | `48d77d` | Brent move after the attack, plus a Ukraine ceasefire clause (d) |
| | `9fb4d5` | oil mixed on diesel and "Saudi-Houthi conflict"; names no attack (a, c) |
| | `c17a52` | oil rises after IEA warning; Aramco clause is secondary (a) |
| | `c8aa62` | Iran statement leads; Aramco facilities attacked is a clause (a) |
| | `794292`, `aed681` | Breakingviews commentary; newsletter (b) |
| | `b2343d`, `24fd57` | explainers on how the Houthis operate (b) |
| | `f87057`, `a9a1ae` | feature on aviation and geopolitical conflicts generally (b); one report group |
| | `ffed4f` | Delta CEO on launching a Saudi flight; does not name the attacks (c) |
| | `c465c9` | strike reported on Sanaa airport; names no attacker (c) |
| | `d33723` | FAA caution on Saudi flights; does not name the attacks (c) |
| | `8cfd21` | booms heard near Aramco HQ; no attack named (c) |
| `E_HOUTHI_ADEN_PALACE` (1) | `2ad0fa` | Aden palace plus coalition details of attacks on Saudi infrastructure (d) |
| `E_COREWEAVE_DISTRESS` (1) | `e3a485` | stock rebounds as Wall Street argues about AI spending; names no bond or CDS distress (c) |
| `E_CPI_SEP` (2) | `bb3286` | preview, published the day before, does not report the release (b) |
| | `4b60c3` | stocks drop "as inflation worsens"; does not name the release (c) |
| `E_TRUMP_RUSSIA_DIESEL_DEAL` (3) | `e2220c`, `9c7ec0`, `10d6af` | diesel-price coverage naming Trump's diesel order or moves, not the Russia deal (c) |
| `E_ADVICE_LOSER_PORTFOLIO` (1) | `2958e2` | see ambiguous case 16 |

Items kept core in Houthi that the title ties to the campaign: `472021` (oil jumps 2% "on Saudi attacks" is the
attributed consequence), `af233d` and `c53a8d` (conference plans after / despite the airport attack), `70c205`,
`a7f779`, `e4ebf1`, `846216`, `56a65f`, `564dd5`, `5ed12f`. The Hormuz (`E_IRAN_HORMUZ_ATTACKS`), IEA and diesel events
stay outside the Houthi event; no Hormuz, IEA or diesel item is a member of it, core or peripheral.

## Counts (v1.1)

| | |
|---|---|
| items | 300 (keyword-selected 91, random 209) |
| labelled events (2+ items) | 24, covering 107 items (85 core, 22 peripheral) |
| `NONE` | 151 |
| `noise` | 42 |
| multi-item report groups | 7 (14 items) |
| same-event pairs among core members, all events | 406 (Houthi alone 351) |
| **rate universe** (see below) | **242 items, 45 same-event pairs** |

The three plan examples and Aden by core members: `E_HOUTHI_SAUDI` 27 (41 with peripheral), `E_COREWEAVE_DISTRESS` 4
(5), `E_CPI_SEP` 3 (5), `E_HOUTHI_ADEN_PALACE` 2 (3, `related: [E_HOUTHI_SAUDI]`). The plan's 35 / 7 / 6 are
whole-feed counts from a different window and a different reading of "item". Two more CoreWeave-naming items are NONE
(`81a1c3`, `88a9d4`: a different AI-stocks story and a commentary piece that do not report the distress).

v1 counted 899 same-event pairs, 820 of them inside the Houthi event; a builder that got the Houthi event right and
split everything else would have passed the 10% false-split rate. v1.1 removes the gated events from the rates.

## Classification state

Plan section 5 forms an event only from a classified report. The state of the 300 items at freeze is in
`classification_state.csv` (`news_id, state, classified_at`): **142 classified, 151 unclassified (no row), 7 failed
(an error row)**; 158 items have no usable classification. Core members of the gated events:

| Gated event | core members | classified (final) | classified by the end of their own first-seen day |
|---|---|---|---|
| `E_HOUTHI_SAUDI` | 27 | 7 | 6 |
| `E_COREWEAVE_DISTRESS` | 4 | 1 | 1 |
| `E_CPI_SEP` | 3 | 1 | 0 |
| `E_HOUTHI_ADEN_PALACE` | 2 | 0 | 0 |

Of the 142 classified items, 25 were classified after the end of their first-seen UTC day, so a replay that respects
`classified_at` sees fewer of them at each as-of date than the final state does.

### Two acceptance runs

The acceptance replay is the daily replay below, run twice. Both reuse the same labels and metrics.

- **Run (a), as-stored.** At each as-of instant T (the end of each UTC day, i.e. T = 00:00Z of the next day, ending at
  2026-10-17T00:00Z) an item counts as classified only if `classification_state.csv` shows state `classified` and
  `classified_at <= T`. An item that is unclassified, failed, or classified after T is unclassified at T. No
  look-ahead. **Reported, not gating.** With 27 Houthi core items and only 6 usable at their own day, this run cannot
  show the gates passing, and that is information.
- **Run (b), as-if-all-classified.** Every item is treated as classified at the end of its own first-seen UTC day,
  with the classification the classifier produces for it. It is **only runnable after the operator approves
  classifying the unclassified items**, because it needs a classification for each of them. The **zero-tolerance
  gates apply to run (b)**, and so do the rate limits. Backfilled classifications get a real `classified_at` later
  than the as-of; run (b) deliberately ignores that field and is an idealised, labelled-as-such replay.

Items to classify for run (b): **158 = 151 unclassified + 7 failed**, listed by `classification_state.csv` rows with
state `unclassified` or `failed`. By universe: 106 + 5 in the rate universe, the rest in the gated events or
peripheral. Cost estimate from the P1 telemetry figure of about 1,365 tokens per item: 158 x 1,365 = about 216,000
tokens in total (input plus output as measured per item; the daily-cap telemetry, not re-measured here). Nothing was
classified and DeepSeek was not called when this was written.

## Ambiguous cases and how they were decided

1. Houthi consequence items: `472021`, `af233d`, `c53a8d`, `70c205` stay core (the headline attributes the consequence
   to the attacks). `48d77d` (Brent plus Ukraine clause), `9fb4d5` (oil on diesel and the conflict), `c17a52` and
   `c8aa62` (Aramco as a clause) are peripheral (v1 had them core). Both the Ukraine and diesel clauses are ignored for
   `E_ENERGY_CEASEFIRE` and the diesel event.
2. `c465c9` (strike reported on Sanaa airport) names no attacker: peripheral (v1: core as the coalition-response leg).
3. `2ad0fa` (Aden palace; coalition details attacks on Saudi infrastructure) is in the Aden event as a peripheral member:
   it joins two incidents, so its place is the one thing the headline does not settle.
4. `1cc3c6` (Syria) and `657b17` (Turkey) are a separate related event, not Houthi: their headlines do not name the
   attacks. `a7f779` (Trump to look at joining Saudi fight after the airport strike) is Houthi core.
5. `745609` (Saudi and Iranian foreign ministers), `53f9bd` (Saudi air-safety probe into a flydubai incident),
   `04122d` (Taiz shelling), `9fde84` (Yemen hospitals) are NONE: Yemen or Saudi, but not the incident.
6. CPI width follows Houthi's: core is the release (`e2ffb1`, `0d6376`, `e0b158` Reuters with the rate-hike odds).
   `bb3286` (preview, 10-13) and `4b60c3` (stocks fall "as inflation worsens") are peripheral. `fea256`, `297d25`,
   `be0464`, `c6d50c`, `437082`, `4251c0` are NONE (they do not name the release).
7. `81a1c3` names CoreWeave but reports the OpenAI revenue story: NONE.
8. `e3a485` (CoreWeave stock rebounds as Wall Street squares off on AI spending) is peripheral (v1: core, "weakest
   member"). CoreWeave core is the bond yields (`4525b4`, `0cedf4`) and the CDS cost (`8eb206`, `dfd130`).
9. `7fb3da`/`5c2c77` (BofA shares fall on revenue miss and penalty) are in `E_BOFA_PENALTY` with `3000b3` (the fine).
10. `611782` (crude near $98 as Iran steps up Hormuz attacks) is in the Hormuz event. The ceasefire story: see 17.
11. `36378b` (Bessent adviser on yields) is grouped with `7c555c` (the hire): same person, adjacent stories.
12. `3ecbaf`/`6f47a0` (Anthropic revenue) are grouped on a shared subject that the headlines tie together.
13. North Korean launches (`bb66f9`, `e278d4`) are NONE: two launches, no named continuing campaign. This is
    the closest case to the "campaign is one event" rule.
14. IMF/World Bank meeting week: Georgieva (`dd3eb6`, `1584f4`), World Bank jobs council (`6ca9d3`, `9f4509`), the
    World Bank growth outlook, the IMF yield warning and the Reuters preview are separate stories; only identical
    reports are grouped.
15. `0bfedc` (J&J Q3 2026 earnings call transcript) was in `E_JNJ_Q3` in v1. By the rule above, earnings transcripts are
    `noise` (the Citi, Domino's and FB Financial transcripts already were). v1.1: `0bfedc` is `noise`; `aeb14b`
    (J&J slide on the quarter) is `NONE`; `E_JNJ_Q3` is gone.
16. `2958e2` ("I feel like a loser: I check my ETFs every day...", 10-10) against the `R_LOSER` report ("...My stock
    portfolio is swinging wildly...", 10-10 and 10-11). The shared text is the column's series label; the question
    differs (ETFs vs a stock portfolio) and, unlike `R_SISTER` (same sums and family facts), no specific is shared.
    From title, publisher and date alone this cannot be decided, so it is a peripheral member of
    `E_ADVICE_LOSER_PORTFOLIO` (excluded from all pairs) and its `report` label stays `r_2958e2`.
17. Diesel. The Russia deal was first reported 10-09T22:00 (`d5398f`). `9c7ec0` (10-09T16:18, "US diesel prices remain
    high despite Trump's moves to boost supplies") predates it and names no deal; `10d6af` (10-14, "Trump's diesel
    dilemma - by the numbers") and `e2220c` (10-07, "Trump's diesel order exposes White House limits") also name
    Trump's diesel moves but not the Russia deal. All three are peripheral to `E_TRUMP_RUSSIA_DIESEL_DEAL` (v1 had
    `9c7ec0` core and the other two NONE, which was inconsistent). `d77cd8` (Russia and Germany add diesel supply)
    names neither Trump nor the deal: NONE.
18. **72-hour link horizon.** Plan 5.4 links only within 72 h of the event's latest report, so a label whose members are
    further apart than that cannot be achieved by the specification. Three v1 events were split by it:
    `E_IEA_RESERVE_RELEASE` (`a55bfb` 10-07T19:28 to `9d81b0` 10-15T19:00, 192 h), `E_SPACEX_WIRELESS` (`d52ce1` 10-09 to
    `7f2ec6` 10-13, 116 h) and `E_ENERGY_CEASEFIRE` (`294f1c` 10-12T01:01 to `34653b` 10-15T16:50, 88 h). The labels
    follow the plan, not the other way round: v1.1 relabels the late or early member of each as NONE with a
    `related_items` / `related_events` entry in `labels.yaml` (information only, no metric reads it). `a55bfb`/`9d81b0`
    and `d52ce1`/`7f2ec6` become four NONE items; `34653b` becomes NONE related to `E_ENERGY_CEASEFIRE`, which keeps
    `b142e6` and `294f1c`. A check of every v1.1 event, using core members only, finds no gap over 72 h by
    `published_at` (largest: the diesel event, 50 h by `first_seen_at`).

(IDs above are the first six hex characters after `news_` in `news_id`.)

## Acceptance numbers E1 must meet (fixed now; metric definitions are binding)

Run E1 over the 300 items in `first_seen_at` order, as daily replay (as-of each day's end, event ids persisted) in both
runs above, and read each item's final E1 event. An item E1 puts in no event is its own one-item event. The scorer
`acceptance.py` implements exactly the following.

**Gated events** (zero tolerance, scored separately): `E_HOUTHI_SAUDI`, `E_HOUTHI_ADEN_PALACE`,
`E_COREWEAVE_DISTRESS`, `E_CPI_SEP`.

**Rate universe U**: the core members of every non-gated event, plus every `NONE` and `noise` item. Peripheral items
and all members (core or peripheral) of the four gated events are not in U (242 items). Let `L(i,j)` be "same
labelled event" for `i, j` in U (a shared event label that is not `NONE`/`noise`; report-group items count as the same
event) and `P(i,j)` "same E1 event", over all unordered pairs of items in U. `|L| = 45`.

- **False-split rate** = |{pairs with L and not P}| / |{pairs with L}| (denominator **45**) **<= 10%** (at most 4 of the
  45 pairs).
- **False-merge rate** = |{pairs with P and not L}| / |{pairs with P}| **<= 5%**. If E1 forms no multi-item event
  inside U the rate is undefined and **the run fails**. Pairs of two `NONE`/`noise` items count as not-L.
- **Plan examples**, zero tolerance, on core members: all core members of `E_COREWEAVE_DISTRESS`, of `E_CPI_SEP` and of
  `E_HOUTHI_SAUDI` each sit in a single E1 event with `event_status = corroborated`, and that event contains no
  outsider; `E_HOUTHI_ADEN_PALACE` core members sit in a different single E1 event, with no outsider, whose
  `related_event_ids` contains the Houthi event's id. An **outsider** is an item in that E1 event that is a core member
  of another event, `NONE`, or `noise`; peripheral items are never outsiders.
- Reported for information, not gating: event-level fragmentation per labelled event, the same two rates over the
  pairs inside the 7-day pool at as-of 2026-10-17T00:00Z, and run (a)'s gates and rates.

Degenerate clusterings the scorer must fail (`tests/test_events_acceptance_scorer.py`), with the figures it gives:

| Clustering | false-split | false-merge | result |
|---|---|---|---|
| gated events exactly right, everything else a singleton | 45/45 = 100% | undefined (no pair) | FAIL |
| every item in one event | 0% | 29,161 cluster pairs, 99.8% | FAIL |
| gated events right, all other items in one event | 0% | 99.8% | FAIL |
| every item a singleton | 100% | undefined | FAIL, all gates fail |
| labels reproduced exactly (peripheral members in or out) | 0% | 0% | PASS |

## What changed from v1 (reference set v1.1)

- B1: both rates redefined over U, 899 pairs (820 Houthi) replaced by 45; scorer and failing-degenerate tests added.
- B2: classification state recorded, two runs defined (above).
- B3: 22 peripheral members, listed above. Houthi core 27, CPI 3, CoreWeave 4, Aden 2.
- C2: `0bfedc` to `noise`, `aeb14b` to `NONE`; `9c7ec0`, `10d6af`, `e2220c` peripheral to the diesel deal;
  `2958e2` peripheral to `E_ADVICE_LOSER_PORTFOLIO`.
- C3: `E_IEA_RESERVE_RELEASE`, `E_SPACEX_WIRELESS` and `E_JNJ_Q3` removed (their members NONE or noise);
  `34653b` to NONE related to `E_ENERGY_CEASEFIRE`.
- v1 had 27 events over 111 items and 899 pairs; v1.1 has 24 events over 107 items and 406 core pairs (45 in U).
