# E1 event builder: failed acceptance, and what to do next (for outside AI consultation)

Date: 2026-10-18. Branch `feat/mri-events` of `riperdy-tech/Macro-Regime-Identifier`. Nothing is merged to `master`.
Author: the MRI implementation session (Claude). This note is written so an outside reviewer can advise without access to the local machine; every file named below is on this branch.

## 1. What we are building, in one paragraph

MRI (the Macro Regime Indicator) is a diagnostic engine that feeds a stock-screening and underwriting system. The operator wants it to notice shifts in the financial environment from **news**, not only from market data: for example a distressed bond sale by CoreWeave, a Fed chair talking about rate hikes, or a Houthi strike on a Saudi airport. The plan (`docs/MRI_EVENTS_PLAN.md`, v2) builds an **event register**: news reports are grouped into **events**, each event is checked against independent market moves, and the result is shown on stock pages the next morning. In Stages 1 and 2 news never changes any number or verdict. The register is a record, not a trading signal. Only a later, separately approved Stage 3 could use it for decisions.

The component that failed is **E1, the event builder** (plan section 5): it must turn a stream of roughly 100 news items a day into events, where one event is one real-world story. It must be deterministic (no new model call at build time) and all parameters live in `config/events.yaml`.

## 2. E1 as specified (plan section 5, abridged)

1. **Pool**: live items first seen in the last 7 days.
2. **Publisher**: the `source_url` domain; for aggregator headlines, the trailing "- Publisher" suffix.
3. **Syndication collapse**: items with the same canonical title, or title Jaccard >= 0.6, are one *report*.
4. **Link a report to an open event** when all three hold: (a) it shares at least one resolved entity with relevance >= 0.5, excluding a small stop list of generic entities; (b) it shares at least one macro theme with the same direction, **or** its title Jaccard with an event report is >= 0.3; (c) it arrived within 72 hours of the event's latest report. An unclassified report links only through title Jaccard >= 0.4.
5. **New development**: a report that shares an entity but no theme and has title Jaccard < 0.2 starts a new event, linked through `related_event_ids`.
6. **Corroboration**: an event is `corroborated` with two or more distinct publishers after collapse, or one official source (the Fed, BLS, DOL, EIA). Otherwise after 72 hours it becomes `rejected_uncorroborated`.
7. **Event record**: id, onset, themes with direction, entities, report and publisher counts, severity, canonical headline.

**Inputs the builder relies on**: each news item is classified by a DeepSeek model (18 macro themes with direction, free-text entity names, sector impacts, severity, confidence). Entities are free text with no tickers. The classifier covers only about 55% of items in October because of a daily cap (it is being raised from 60 to 150 a day). `source_group` in the feed is a topic label, not a publisher.

## 3. How acceptance was defined, before any E1 code existed

A hand-labelled **reference set** of 300 items first seen 2026-10-08..10-16 (`tests/fixtures/events_reference/`; method and seed in `LABELLING.md`, labels in `labels.yaml`, items in `items.csv`). Labels were made from title, publisher and date only, by an agent that never saw classifier or clustering output. Version 1.1 followed a review. Facts: 24 labelled events over 107 items (85 core, 22 peripheral), plus 147 unlabelled (`NONE`) and 42 `noise` items.

Pass criteria (computed by `acceptance.py` over non-example events, pairwise):

- **false-merge <= 5%**: two items from different labelled events grouped together;
- **false-split <= 10%**: two items from one labelled event separated;
- four **zero-tolerance example events**, each exactly one event with no outsiders: Houthi/Saudi (27 core items), CoreWeave (4), US CPI/Fed (3), and the Aden palace strike (2) as a separate event related to Houthi.

Two replays: **run (a)** uses classifications as stored at each day (no look-ahead); **run (b)** adds classifications for 158 previously unclassified reference items, made on 2026-10-18 through the production classifier (overlay in `classification_overlay.parquet`, about 1,890 tokens per item). Run (b) is the one the gates apply to.

## 4. Results (nothing was tuned between runs)

All numbers are over the whole 7-day store pool, scored on the 300 reference items. Full record: `E1_ACCEPTANCE.md`.

| | run (a) v2 | run (a) v2_2 | run (b) v2 | run (b) v2_2 |
|---|---|---|---|---|
| false-split (limit 10%) | 37/45 = 82% | 41/45 = 91% | 15/45 = 33% | 36/45 = 80% |
| false-merge (limit 5%) | 505/513 = 98% | 16/20 = 80% | 1322/1352 = 98% | 11/20 = 55% |
| largest event, 10-17 | 123 items | 12 | 185 | 15 |
| Houthi/Saudi | FAIL | FAIL | FAIL: 24 of 27 core in one hub | FAIL: 27 core across 15 events |
| CoreWeave | FAIL | FAIL | FAIL | **PASS** |
| CPI | FAIL | FAIL | FAIL | FAIL: 3 core in 3 events |
| Aden palace | FAIL | FAIL | FAIL | FAIL: in 2 different Houthi events |

- **v2** is the plan exactly as written. **v2_2** is an erratum added after seeing the first failure (section 6).
- The v2_2 false-merge denominator is only 20 same-cluster pairs, so its rates are noisy. The false-split denominator is 45.

## 5. Diagnosis (an Opus 5.5 auditor rebuilt run (b) read-only and reproduced the numbers exactly; no builder bug explains the failure)

1. **v2 chains unrelated stories into one hub.** Each join satisfies section 5.4 as written, one report at a time. The shared entity is usually broad (`crude oil`, `iran`, `saudi arabia`, `federal reserve`, `china`, `united kingdom`), and the event accumulates 25 theme-direction pairs, so almost any new report shares one. `geopolitical_risk positive` is carried by a large share of items. In the fixture hub, 18 of 18 joins went through the theme clause with a largest title Jaccard of only 0.22. 17 of 18 also pass if compared with a single report instead of the event's union, so the union reading (the plan does not say what to compare against) is not the main cause.
2. **v2_2 stops the chaining and over-splits.** v2_2 requires one existing report to supply both the shared entity and the theme (or Jaccard), and a shared entity counts only if it appears in at most 1% of classified items in the previous 28 days. On the 27 Houthi core reports, 191 joins were refused: 140 because the 1% background rule dropped the shared entity (it would have linked otherwise), 36 by the 72-hour horizon, 14 had no shared entity at all, 1 a theme-direction mismatch. `saudi arabia` sits at 2.0 to 2.5% of the background every day (11 of 539 on 10-09, before the story's own items), `iran` at 9 to 11%, `federal reserve` at 6 to 8%.
3. **Entity names fragment.** Only 14 of the 27 Houthi reports carry a Houthi key, split across `houthis` (7), `houthi` (4), `houthi movement` (2), `yemens houthis` (1), plus `riyadh`, `riyadh airport`, `king khalid international airport`. The name normaliser does no variant folding. The three CPI reports name almost entirely different entities (`consumer price index`/`gasoline`; `bureau of labor statistics`/`us federal reserve`/...; `federal reserve`), and `us federal reserve` is not aliased to `federal reserve`. Two of the three refused CPI joins had no shared entity at all, and title Jaccard cannot link without one.
4. **Corroboration status is a symptom of fragmentation, not a publisher bug.** In v2_2, 35 labelled-core items sit in `rejected_uncorroborated` events and 20 in `detected`. All 944 pooled items parse a publisher; 80 Google News items resolve to Reuters. Five Houthi fragments are Reuters-only and can never reach two publishers; one fragment reached three publishers but only after 72 hours. Turning the 72-hour horizon off changes nothing in v2_2.
5. **Counterfactuals on the seen set (information only, none adoptable):**

| variant | false-split | false-merge | Houthi events | largest event |
|---|---|---|---|---|
| v2_2 as recorded | 36/45 | 11/20 | 15 | 18 |
| background rule off | 17/45 | 319/347 | 3 (24 of 27 in one) | 153 |
| background at 5% | 18/45 | 175/202 | 3 | n/a |
| 72h horizon off | 36/45 | 14/23 | 15 | n/a |

No setting in 2 modes and 4 counterfactuals comes near 10% and 5% together. The Houthi story is linked by the same kind of entity (a country) that glues unrelated stories.
6. **Other distortions in the test**: only the 300 reference items are as-if-classified; the other roughly 640 pool items keep real coverage (about 60 a day), which understates chaining. Each item is scored at the last as-of day it was pooled, so a story longer than 7 days gets a new id when its first item rolls out (3 of the 15 Houthi events). One genuine code bug: `canonical_title` strips a trailing " - X" from non-aggregator titles (17 of 944 items), against the plan's aggregator-only rule. It does not explain the failure.
7. **Aden palace**: in both modes it links into a Houthi event, as predicted. The plan's new-development rule (5.5) fires only when the new report shares an entity but **no** theme, which almost never happens for two stories with the same actor.

**Summary**: the plan's "shared entity plus shared theme" rule cannot separate stories on this data. Tightening it fragments real stories; loosening it creates hubs.

## 6. History of this phase (for honesty about overfitting)

- Plan v2 specified the rule above; parameters were frozen in `config/events.yaml` before E1 code.
- Run (a) was done once and failed. Opus designed erratum **v2.2** (single-report supply plus 1% / 28-day entity background rule, 300-item minimum, flagged unratified). It was implemented behind a config switch with v2 preserved.
- Run (b) was then done once per mode and also failed.
- The reference set has now been seen twice. **Any number from it is no longer evidence for a fix.** An adopted change needs a blind holdout (section 8).

## 7. Questions for the outside reviewer

1. Is "shared entity plus shared theme" a sound basis for grouping news into events at all, or is a different primitive needed? Candidates: specificity-weighted entity overlap (IDF or similar rather than a hard 1% cut), distinctive-term overlap on titles (TF-IDF cosine), dense headline embeddings, or a two-stage design (cheap candidate generation, then a stricter confirmation step).
2. The plan insists on a deterministic builder with no new model call at build time. Is a local embedding model an acceptable exception, or is there a principled deterministic approach? What would you pick and why?
3. Is the acceptance protocol well posed? The false-split denominator is only 45 pairs; the rates are pairwise; the examples are zero tolerance; the Houthi event holds 351 of 406 core pairs. Should the protocol use B-cubed, adjusted Rand, or event-level recall and precision instead? How big must the holdout be (we can label roughly 300 items; at least 3 stories with 5 or more core items each)?
4. How should event identity persist as items roll out of the 7-day pool, so that a long story (weeks) keeps one id without letting a hub form?
5. Should Stage 1 ship as **annotated candidates** (a status and publishers per candidate, explicitly not "one event per story", with the downstream market check de-duplicating) rather than corroborated events? What would you need to see before calling a candidate a corroborated event?
6. Should a story that continues for weeks (like the Houthi campaign) be one event, or a chain of events linked by `related_event_ids`? Does the entity-frequency suppression make sense for exactly the stories that matter most?
7. What failure modes do you expect in the real-time (as-of) setting that a retrospective replay hides?

## 8. Proposed way forward (pending the operator)

1. Fix the `canonical_title` bug (aggregator hosts only), with a test.
2. Add name-variant folding (plurals, possessives, "movement", alias coverage) and persist event identity across pool windows. Test once on the reference set as information only.
3. Fold any further linking change into a single erratum; **freeze code and thresholds by commit hash first**.
4. Test it on a **blind holdout**: a window first seen after 2026-10-19, with at least 3 stories of 5 or more core items each, labelled under the frozen v1.1 rules by someone who never sees builder output, every item classified before the replay, run once, nothing changed afterwards.
5. If it still fails, ship the **candidate register** as Stage 1 and treat full event clustering as unresolved Stage 2 work.

## 9. Decisions waiting on the operator

- What Stage 1 ships: the candidate register, or keep redesigning E1 first.
- Whether to approve the two errata (name-variant folding; identity across windows).
- The Aden palace: a related event, or merged into the Houthi event.
- Whether to adopt erratum v2.2 at all (recommendation: not before a blind holdout passes).

## 10. Where things are

All paths relative to the repo root on branch `feat/mri-events`.

| What | Path |
|---|---|
| Plan v2 | `docs/MRI_EVENTS_PLAN.md` |
| Acceptance record, runs (a) and (b) | `docs/events_build/E1_ACCEPTANCE.md` |
| Spec freeze, errata, unratified list | `docs/events_build/SPEC_FREEZE.md` |
| Builder | `src/macro_engine/events/builder.py` |
| Replay script | `scripts/events_replay.py` |
| Frozen parameters | `config/events.yaml` |
| Reference set, labels, scorer | `tests/fixtures/events_reference/` |

Limits of this note: results are from one reference set of 300 items from one nine-day window, in one market regime. The classifier's coverage was about 55% of items when the window was live. The auditor's counterfactuals were run on the same data after seeing the failures, so they indicate mechanism, not expected performance. MRI is a diagnostic platform; nothing here is investment advice.
