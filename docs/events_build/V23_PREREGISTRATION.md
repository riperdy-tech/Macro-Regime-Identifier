# v2.3 pre-registration: how Stage 1 linking will be judged

Written 2026-10-18 (Phase 4a), branch `feat/mri-events`. Operator approvals of 2026-10-18 and the Opus audit conditions
are in `BRIEF_P4A.md`, `EXTERNAL_REVIEWS_E1.md` ("Opus audit of the reviews") and `E1_FAILURE_CONSULTATION.md`
(with its Corrections). This document is **frozen by commit hash** before the holdout is labelled; the freeze commit is
recorded in the "Freeze record" at the end, by a later commit that changes nothing above it.

MRI is a diagnostic platform. Stage 1 never changes a number or a verdict (D1). A pass here licenses only the candidate
register to carry the linker's groupings; it does not license any use of news in a decision.

## 0. Status of the evidence

The reference set (300 items, 2026-10-08..10-16, `tests/fixtures/events_reference/`) has been seen twice by the people
tuning the builder and informed the labelling guide. It is a **DEVELOPMENT set**. Cutoffs may be tuned on it and only on
it, and any figure from it is reported as **optimistic**. The v1.1 pairwise gates and zero-tolerance example gates
(`E1_ACCEPTANCE.md`) stay as regression information; they are not pass criteria for production.

## 1. The two arms (both built and run on the same holdout; one is declared before the run)

Common to both arms:

- Pool and collapse: as in `builder.py` (7-day discovery window, syndication collapse to reports, `canonical_title` strips
  a trailing " - X" only for aggregator hosts). Persistent identity via `events/state.py`: events keep their id; open
  while `as_of_day - last_report_day <= 3`; the linker sees only the bounded summary of each open event.
- **Link against the anchors** (the event's first 3 reports), **never the union** of its reports and never a centroid of
  everything. Score = best (or a fixed combination, fixed before freeze) over the 3 anchors.
- **Themes are excluded from identity.** Themes, directions, sectors and severities are annotations on a candidate and
  never enter a link score. No hard frequency cutoff on entities; specificity enters only as a continuous weight.
- The classifier is used as production uses it: **live classification at the production cap (150 a day), no overlay**.
  An unclassified report is still linkable through titles. Entities are secondary evidence only.

**Arm A (sparse lexical).** Character 3-5-gram TF-IDF of the canonical title. The IDF is computed from a **pre-pool
background**: titles first seen in the days before the pool start, day-granular (one count per calendar day per n-gram,
so a one-day burst cannot starve the weight), taken from the store as it stood at that instant. Entity IDF is a
secondary feature, computed the same way over resolved/normalised entity keys. A report links to an open event when
its best anchor score reaches the frozen cutoff (and the frozen minimum on at least one high-IDF shared term, if the
freeze sets one); otherwise it starts a new event. Near-misses are recorded as `proposed_link`s with evidence.

**Arm B (A plus dense).** A, plus the cosine of stored dense title vectors against the anchors. The embedding model is
**pinned by name, version and sha256**; each vector is **computed once at ingestion and persisted with its provenance**
(model name, version, sha256, preprocessing, dtype); the build reads **stored vectors only** and never calls the model
(embeddings are not bitwise-reproducible across hardware, so the stored vector, not the model, is the record). The
combination (a fixed weighted sum, or a gate on A's candidate plus a dense confirmation) is chosen before freeze.
Operator approval of the dependency and model download: 2026-10-18; the download and pin happen in Phase 4b.

Arm declared primary before the run: **B if its pinned model and stored vectors are in the freeze, otherwise A.** Both are
scored; the choice is not made after seeing the holdout.

## 2. Free parameters, and the rule for setting them

| Parameter | Set by | Where it lives at freeze |
| --- | --- | --- |
| char n-gram range (3-5), analyzer, lower-casing, normalisation | this document | arm config |
| IDF background window (days before pool start), per-day count cap, smoothing | Phase 4b, on the development set | arm config |
| link cutoff(s), anchor combination (best / mean of 3), entity-IDF weight, minimum high-IDF term | Phase 4b, on the development set only | arm config |
| dense model name, version, sha256; dense weight or confirmation cutoff | Phase 4b, development set only | arm config |
| open window (3 days), anchors (3), recent (5), core-entity share (30%) | this document / `state.py` | code |

All cutoffs are tuned ONLY on the seen reference set and **then frozen**. No parameter is read from, or adjusted after
seeing, any holdout item or holdout score.

## 3. Candidate register semantics (Stage 1)

What the arms produce feeds the candidate register specified in `CANDIDATE_REGISTER_SPEC.md`: a candidate is a linker
grouping, never a confirmed event; single-source candidates are shown; market checks are keyed by (instrument,
reference close date, window) so fragments of one story count once. Cluster quality is what this protocol measures.

## 4. The holdout

- **Window.** Every item first seen in a **contiguous 5-day window that starts at or after the freeze commit** (about 550
  items). `tests/fixtures/events_holdout/sample_holdout.py` selects it: all items, no keyword selection, no sampling,
  real distractors in; it refuses a window that starts before the freeze date or has not fully elapsed. It is not run
  before the window has elapsed.
- **Labelling.** Fully labelled by **two independent labellers** working only from title, publisher and date under
  `LABELLING_GUIDE_V2.md`, with the two-label model (episode and storyline). Disagreements are adjudicated by a **third
  pass using only the guide**, logged item by item. Labelling starts only after the window has elapsed and the freeze
  record below is filed.
- **Ceiling.** The inter-labeller B-cubed (and per-episode recall, both directions) between the two original files is
  reported as the ceiling on any clustering (`score_holdout.inter_labeller_ceiling`).
- **Run.** Live classification at the production cap, no overlay; one replay in the same daily order as production
  (`apply_day` per day), one run per arm.
- **Resampling.** Story-resampled 90% confidence intervals: bootstrap over storylines (a one-item story is its own unit),
  1000 draws, seed 20261019, percentile interval [5th, 95th].

## 5. Metrics

Primary (`score_holdout.score`):

1. **Per-episode recall**: for each true episode with at least 2 core items, the share of its core items in the one
   predicted cluster that holds most of them; mean over true episodes.
2. **Contamination**: over predicted clusters with at least 2 core items, the share of those items that are outsiders
   (cluster size minus its most frequent episode's count). Undefined if there is no multi-item cluster, and undefined fails.
3. **Largest predicted cluster against the largest labelled episode** (all members counted).

Secondary: all-item B-cubed precision, recall and F1; pairwise precision and recall; the per-episode fragmentation table.
**Storyline-level recall is reported separately, as information.** Peripheral items are excluded from every metric.

## 6. PASS criteria (ratified 2026-10-18)

All three, on the declared primary arm:

1. contamination **90% upper bound <= 0.15**;
2. **no predicted cluster larger than 1.5 x the largest labelled episode**;
3. per-episode recall **at least 0.15 above the syndication-only baseline** (the clustering that merges only items sharing a
   `report_label`; point estimate, with its paired CI reported).

The scorer is tested on all-singletons (does NOT pass: contamination undefined, recall below baseline), everything-merged
(fails contamination and size), perfect (passes), a hub with 10 outsiders (fails contamination although its recall is
perfect) and each episode split five ways (fails recall) in `tests/test_events_holdout_scorer.py`.

## 7. One run, and what failure means

**One run per arm. Nothing is changed afterwards**: no cutoff, label, scorer or code, and no second run on the same
window. A failure of the primary arm means **Stage 1 stays candidate-only** (no event clustering claims; candidates are
shown without "same story" grouping beyond syndication collapse and de-duplication keys) and **full clustering is Stage 2
work** on a new window under a new pre-registration. A pass licenses linker groupings in the candidate register only.

## 8. What is frozen by commit hash

Builder code, arm configs, cutoffs, IDF source, scorer, sampler, labelling guide, E2 dedup key. The exact paths:

```
src/macro_engine/events/builder.py        src/macro_engine/events/config.py
src/macro_engine/events/entities.py       src/macro_engine/events/state.py
src/macro_engine/events/store_io.py       src/macro_engine/events/confirmation_prices.py
config/events.yaml                        config/entity_aliases.yaml
config/event_theme_map.yaml               config/events_v23.yaml            (arm configs; created in Phase 4b)
src/macro_engine/events/linker.py         (Phase 4b: arms A and B, IDF source, embedding ingestion)
tests/fixtures/events_holdout/score_holdout.py
tests/fixtures/events_holdout/sample_holdout.py
tests/fixtures/events_holdout/LABELLING_GUIDE_V2.md
docs/events_build/V23_PREREGISTRATION.md  docs/events_build/CANDIDATE_REGISTER_SPEC.md
docs/events_build/SPEC_FREEZE.md          (erratum v3)
```

The E2 de-duplication key is (instrument, reference close date, window), specified in `CANDIDATE_REGISTER_SPEC.md`.

## 9. Freeze record (to be filed by a later commit; the text above does not change)

| Field | Value |
| --- | --- |
| Freeze commit | PENDING |
| Arm configs, cutoffs, embedding model name / version / sha256 | PENDING (Phase 4b) |
| Primary arm | PENDING (B if the pinned model and vectors are frozen, else A) |
| Holdout window (5 contiguous days, start >= freeze date) | PENDING |
| Labeller files, adjudication log | PENDING |
| Result, one run per arm | PENDING |
