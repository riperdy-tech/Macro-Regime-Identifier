# v2.3 arm B: development-set tuning (Phase 4c)

Written 2026-10-18 BEFORE any arm B grid cell was run (and before any arm B score on the reference labels was computed).
Branch `feat/mri-events`. Binding documents: `V23_PREREGISTRATION.md` (arm B definition), `V23_DEV_TUNING.md` (arm A,
whose selection rule is reused unchanged), `MODEL_PROVENANCE.md`. The grid and the rule below are fixed by this commit.

## Status of the evidence

Same as arm A: the 300-item reference set is a DEVELOPMENT set that has been seen repeatedly. **Every figure is
OPTIMISTIC (parameters chosen on seen data and scored on the same data) and is not evidence that arm B passes.** The pass
decision is the blind holdout, one run, under the pre-registration.

## What arm B is here

Score of a report against an event = max over the event's three anchors of
`title_cosine + entity_weight * entity_cosine + dense_weight * dense_cosine`, where `dense_cosine` is the dot product of
the STORED L2-normalised `bge-small-en-v1.5` vectors of the two canonical titles. (The dense cosine is added per anchor
and the best anchor is chosen on the sum, so one anchor supplies all the evidence: the same reading as arm A's "best over
the anchors".) A missing vector adds nothing for that anchor and the decision is flagged `dense_missing`. The gates are
arm A's, unchanged (best anchor's title cosine >= `title_cosine_floor` or a shared entity key with IDF >=
`entity_idf_floor`); dense similarity alone never opens a gate. Vectors are read from `event_embeddings`; the replay never
loads the model. Replay mechanics (whole-store pool, day by day, stored classification only, no overlay, the 28-day
IDF background) are exactly those of `V23_DEV_TUNING.md`.

## Grid (54 cells, fixed)

| Parameter | Values |
| --- | --- |
| `dense_weight` | 0.5, 1.0, 1.5 |
| `cutoff` | 0.40, 0.55, 0.70, 0.85, 1.00, 1.20 |
| `entity_weight` | 0.0, 0.25, 0.5 |

3 x 6 x 3 = 54 cells (<= 60). Held at arm A's chosen values (not in the grid): n-gram range 3-5, `title_idf_offset` 1.0,
`background_days` 28, anchors 3, `title_cosine_floor` 0.10, `entity_idf_floor` 2.0, `ambiguity_margin` 0.05,
`near_miss_fraction` 0.8. Because the dense term raises every score by roughly `dense_weight` times the baseline cosine of
unrelated titles, the cutoff range is wider than arm A's; a cell whose cutoff sits below that offset links nearly
everything and will simply fail on contamination and size.

## Selection rule (arm A's, unchanged)

Per cell: the pre-registered scorer's contamination (point and 90% story-resampled interval), per-episode recall uplift
over the syndication baseline, largest predicted cluster over the largest labelled episode.

1. Feasible set F = cells with recall uplift (point) **>= 0.15**.
2. If F is not empty: choose the cell with the **smallest contamination 90% upper bound** (undefined ranks last);
   tie-break the **smaller largest predicted cluster**, then the smaller `cutoff`, then the smaller `entity_weight`, then
   the earlier grid row.
3. If F is empty: say so; report as best cell the one with the largest uplift (tie-break smaller contamination upper
   bound). The criteria are not relaxed.

The size criterion (largest cluster <= 1.5 x largest labelled episode) is reported for the chosen cell, not used to select.
Whether any cell passes all three pre-registered criteria is recorded as information. Run once: `python
scripts/events_v23_replay.py --set reference --tune-b --out <grid.json>`, then once for the chosen cell. If no cell passes,
the record says so plainly. The chosen cell (feasible or best) is written to `config/events_v23.yaml` `arm_b`; the top-level
`arm:` stays `A` until the freeze decides the primary arm.

## Comparison with arm A

Reported against arm A's chosen cell (cell 30 of `V23_DEV_TUNING.md`) on the same three pre-registered criteria, plus the
per-story behaviour (Houthi/Saudi, Aden palace, CPI Sep, CoreWeave distress).

## Pairwise separability (information only)

Separately from tuning: AUC and best precision at recall >= 0.8 of the dense cosine alone, over all pairs of the 278
non-peripheral reference items and with the Houthi/Saudi positive pairs removed, set beside the arm A character-n-gram
score on the same pairs (the definition of the Opus audit's `sep_pairs.py`: char 3-5-gram TF-IDF with the 28-day
background, `word_tfidf`/`char35_tfidf` as computed there). Nothing is tuned on it.
