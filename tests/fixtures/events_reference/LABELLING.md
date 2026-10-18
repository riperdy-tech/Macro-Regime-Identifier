# E1 reference set: labelling record (plan v2 section 5 acceptance, section 9 step 2)

Written 2026-10-18 on branch feat/mri-events, before any E1 code exists. Files: `items.csv`, `labels.yaml`,
`build_items.py` (the selector; it imports no clustering, Jaccard or classifier code).

## Method

- Pool: all 944 `news_items` in the local store with `first_seen_at` in [2026-10-08, 2026-10-17). 300 items were
  chosen: 91 by keyword rule (every item whose title matches the three plan examples, the Aden story, or the broader
  Fed/inflation/Saudi/airport vocabulary; see `EXAMPLE_RE` in `build_items.py`), plus 209 drawn by
  `random.Random(20261018).sample` from the other 853.
- **Seed: 20261018.** Re-running `python tests/fixtures/events_reference/build_items.py <db> <out>` on the same
  store reproduces `items.csv`.
- Labelled by one reader from title, publisher and published date only. No clustering, Jaccard, event, classifier
  or theme code or output was run, read or consulted. Labels were fixed in one pass and are not to be tuned after E1
  results are seen. Any change after E1 exists is a new reference-set version with a recorded reason.
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

- `NONE`: real news about something no other item in this set belongs with. Each NONE item is its own one-item event.
- `noise`: not news (advice columns, market previews, earnings transcripts, roundups, promotions). Also a
  one-item event each.
- `report` (`report_label`): items that are the same report republished: identical headline (ignoring the
  " - Publisher" suffix) or the same article retitled, judged from the headline. Different outlets that rewrote the
  same fact (CNBC and oilprice on the Houthi vow) are different reports. Every multi-item report group also has one
  shared event label (7 groups).

## Counts

| | |
|---|---|
| items | 300 (keyword-selected 91, random 209) |
| labelled events (2+ items) | 27, covering 111 items |
| `NONE` | 147 |
| `noise` | 42 |
| same-event item pairs | 899 of 44,850 possible |
| multi-item report groups | 7 (14 items) |

The three plan examples and Aden: Houthi/Saudi `E_HOUTHI_SAUDI` 41 items; CoreWeave `E_COREWEAVE_DISTRESS` 5;
US CPI `E_CPI_SEP` 5 (its Reuters item is "US inflation heats up in September; Fed rate hike this month back on
table"); Aden palace `E_HOUTHI_ADEN_PALACE` 3, `related: [E_HOUTHI_SAUDI]`. The plan's 35 / 7 / 6 are whole-feed
counts from a different window and a different reading of "item". Two more CoreWeave-naming items are NONE (`81a1c3`,
`88a9d4`: a different AI-stocks story and a commentary piece that do not report the distress).

## Ambiguous cases and how they were decided

1. Houthi consequence items that name the attacks only in passing: `c8aa62` (Iran counting moments to strike US
   warships; Aramco attacked) and `c17a52` (oil up after IEA warning; Aramco attacked) are in the Houthi event
   because Aramco is the named incident; `48d77d` (Brent tops $106 after Saudi airport attack, plus Ukraine) and
   `9fb4d5` (oil mixed on diesel and Saudi-Houthi conflict) likewise; the Ukraine/diesel clauses are ignored.
2. `c465c9` (strike reported on Sanaa airport) names no attacker: Houthi event, as the coalition-response leg.
3. `2ad0fa` (Aden palace; coalition details attacks on Saudi infrastructure): Aden event, the lead clause.
4. `1cc3c6` (Syria) and `657b17` (Turkey) are a separate related event, not Houthi: their headlines do not name the
   attacks. `a7f779` (Trump to look at joining Saudi fight after the airport strike) is Houthi: the headline names it.
5. `745609` (Saudi and Iranian foreign ministers), `53f9bd` (Saudi air-safety probe into a flydubai incident),
   `04122d` (Taiz shelling), `9fde84` (Yemen hospitals) are NONE: Yemen or Saudi, but not the incident.
6. `bb3286` (CPI preview published 10-13) is in `E_CPI_SEP` as the preview of the same release. `4b60c3` (US stocks
   fall as inflation worsens) is in; `fea256` (inflation fears rattle tech), `297d25`, `be0464`, `c6d50c`,
   `437082`, `4251c0` are NONE (they do not name the release).
7. `81a1c3` names CoreWeave but reports the OpenAI revenue story: NONE.
8. `e3a485` (CoreWeave stock rebounds as Wall Street squares off on AI spending) is in the CoreWeave event; it is the
   weakest member.
9. `7fb3da`/`5c2c77` (BofA shares fall on revenue miss and penalty) are in `E_BOFA_PENALTY` with `3000b3` (the fine).
10. `34653b` (Russia accuses Ukraine of refinery attack as strikes resume) is in `E_ENERGY_CEASEFIRE` (the ceasefire
    story continued); `611782` (crude near $98 as Iran steps up Hormuz attacks) is in the Hormuz event.
11. `a55bfb` (IEA 10-07) and `9d81b0` (IEA 10-15) are one event; the second may be a new announcement.
12. `36378b` (Bessent adviser on yields) is grouped with `7c555c` (the hire): same person, adjacent stories.
13. `3ecbaf`/`6f47a0` (Anthropic revenue) and `d52ce1`/`7f2ec6` (SpaceX wireless) are grouped on a shared subject
    that the headlines tie together; both could be argued as two events.
14. North Korean launches (`bb66f9`, `e278d4`) are NONE: two launches, no named continuing campaign. This is
    the closest case to the "campaign is one event" rule.
15. IMF/World Bank meeting week: Georgieva (`dd3eb6`, `1584f4`), World Bank jobs council (`6ca9d3`, `9f4509`), the
    World Bank growth outlook, the IMF yield warning and the Reuters preview are separate stories; only identical
    reports are grouped.

(IDs above are the first six hex characters after `news_` in `news_id`.)

## Acceptance numbers E1 must meet (fixed now; metric definitions are binding)

Run E1 over the 300 items in `first_seen_at` order, as daily replay (as-of each day's end, event ids persisted),
and read each item's final E1 event. An item E1 puts in no event is its own one-item event. Let
`L(i,j)` be "same labelled event" (label shared and not `NONE`/`noise`; report-group items count as the same event)
and `P(i,j)` "same E1 event", over all unordered pairs of the 300 items.

- **False-split rate** = |{pairs with L and not P}| / |{pairs with L}| (denominator 899) **<= 10%**.
- **False-merge rate** = |{pairs with P and not L}| / |{pairs with P}| **<= 5%**. If E1 forms no multi-item event
  the rate is undefined and the run fails. Pairs of two `NONE`/`noise` items count as not-L.
- **Plan examples**, zero tolerance: all members of `E_COREWEAVE_DISTRESS`, of `E_CPI_SEP` and of `E_HOUTHI_SAUDI`
  each sit in a single E1 event with `event_status = corroborated`, and that event contains no item outside its
  labelled members; `E_HOUTHI_ADEN_PALACE` members sit in a different single E1 event whose `related_event_ids`
  contains the Houthi event's id.
- Reported for information, not gating: event-level fragmentation per labelled event, and the same two rates
  restricted to pairs both inside the 7-day pool at as-of 2026-10-17T00:00Z.
