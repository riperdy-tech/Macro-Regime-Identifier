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

## Run record

One run of the 54 cells (`python scripts/events_v23_replay.py --set reference --tune-b`, raw output `v23_dev_grid_b.json`),
then one run of the chosen cell (`v23_dev_chosen_b.json`). The grid and the rule above were not changed after the run.
**All figures are OPTIMISTIC (seen data).** Columns as in `V23_DEV_TUNING.md`: baseline per-episode recall 0.536; "uplift" is
over it; "contam upper" is the story-resampled 90% upper bound; "ratio" is the largest predicted cluster over the largest
labelled episode (41 items). 1143 items were embedded once (all items first seen 2026-10-08 .. 2026-10-18, which holds the
944-item pool and the 300 reference items) before the grid was run.

| # | dense w | cutoff | entity w | recall | uplift | contam | contam 90% upper | largest cluster | ratio | events | pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.5 | 0.40 | 0.00 | 0.800 | +0.264 | 0.544 | 0.482 | 30 | 0.73 | 297 | no |
| 2 | 0.5 | 0.40 | 0.25 | 0.800 | +0.264 | 0.546 | 0.483 | 30 | 0.73 | 298 | no |
| 3 | 0.5 | 0.40 | 0.50 | 0.800 | +0.264 | 0.538 | 0.471 | 30 | 0.73 | 296 | no |
| 4 | 0.5 | 0.55 | 0.00 | 0.818 | +0.282 | 0.296 | 0.288 | 19 | 0.46 | 589 | no |
| 5 | 0.5 | 0.55 | 0.25 | 0.818 | +0.282 | 0.303 | 0.296 | 19 | 0.46 | 577 | no |
| 6 | 0.5 | 0.55 | 0.50 | 0.818 | +0.282 | 0.311 | 0.306 | 19 | 0.46 | 562 | no |
| 7 | 0.5 | 0.70 | 0.00 | 0.710 | +0.174 | 0.119 | 0.154 | 8 | 0.20 | 753 | no |
| 8 | 0.5 | 0.70 | 0.25 | 0.710 | +0.174 | 0.131 | 0.159 | 8 | 0.20 | 743 | no |
| 9 | 0.5 | 0.70 | 0.50 | 0.712 | +0.176 | 0.154 | 0.182 | 9 | 0.22 | 732 | no |
| 10 | 0.5 | 0.85 | 0.00 | 0.612 | +0.076 | 0.111 | 0.167 | 4 | 0.10 | 814 | no |
| 11 | 0.5 | 0.85 | 0.25 | 0.642 | +0.105 | 0.100 | 0.148 | 4 | 0.10 | 807 | no |
| 12 | 0.5 | 0.85 | 0.50 | 0.664 | +0.128 | 0.095 | 0.143 | 5 | 0.12 | 796 | no |
| 13 | 0.5 | 1.00 | 0.00 | 0.566 | +0.029 | 0.050 | 0.083 | 2 | 0.05 | 845 | no |
| 14 | 0.5 | 1.00 | 0.25 | 0.586 | +0.050 | 0.045 | 0.077 | 2 | 0.05 | 839 | no |
| 15 | 0.5 | 1.00 | 0.50 | 0.600 | +0.064 | 0.043 | 0.071 | 3 | 0.07 | 833 | no |
| 16 | 0.5 | 1.20 | 0.00 | 0.536 | +0.000 | 0.071 | 0.125 | 2 | 0.05 | 860 | no |
| 17 | 0.5 | 1.20 | 0.25 | 0.536 | +0.000 | 0.071 | 0.125 | 2 | 0.05 | 858 | no |
| 18 | 0.5 | 1.20 | 0.50 | 0.557 | +0.021 | 0.062 | 0.111 | 2 | 0.05 | 850 | no |
| 19 | 1.0 | 0.40 | 0.00 | 0.787 | +0.251 | 0.598 | 0.516 | 32 | 0.78 | 199 | no |
| 20 | 1.0 | 0.40 | 0.25 | 0.833 | +0.296 | 0.595 | 0.516 | 32 | 0.78 | 197 | no |
| 21 | 1.0 | 0.40 | 0.50 | 0.840 | +0.304 | 0.608 | 0.530 | 25 | 0.61 | 194 | no |
| 22 | 1.0 | 0.55 | 0.00 | 0.824 | +0.288 | 0.592 | 0.527 | 29 | 0.71 | 213 | no |
| 23 | 1.0 | 0.55 | 0.25 | 0.845 | +0.309 | 0.592 | 0.530 | 29 | 0.71 | 207 | no |
| 24 | 1.0 | 0.55 | 0.50 | 0.827 | +0.291 | 0.605 | 0.543 | 18 | 0.44 | 203 | no |
| 25 | 1.0 | 0.70 | 0.00 | 0.838 | +0.302 | 0.511 | 0.464 | 29 | 0.71 | 341 | no |
| 26 | 1.0 | 0.70 | 0.25 | 0.838 | +0.302 | 0.508 | 0.464 | 29 | 0.71 | 339 | no |
| 27 | 1.0 | 0.70 | 0.50 | 0.838 | +0.302 | 0.508 | 0.463 | 30 | 0.73 | 334 | no |
| 28 | 1.0 | 0.85 | 0.00 | 0.818 | +0.282 | 0.317 | 0.290 | 23 | 0.56 | 536 | no |
| 29 | 1.0 | 0.85 | 0.25 | 0.846 | +0.309 | 0.328 | 0.304 | 23 | 0.56 | 521 | no |
| 30 | 1.0 | 0.85 | 0.50 | 0.846 | +0.309 | 0.321 | 0.293 | 23 | 0.56 | 517 | no |
| 31 | 1.0 | 1.00 | 0.00 | 0.757 | +0.220 | 0.160 | 0.171 | 11 | 0.27 | 711 | no |
| 32 | 1.0 | 1.00 | 0.25 | 0.757 | +0.220 | 0.169 | 0.178 | 11 | 0.27 | 700 | no |
| 33 | 1.0 | 1.00 | 0.50 | 0.757 | +0.220 | 0.200 | 0.220 | 11 | 0.27 | 690 | no |
| 34 | 1.0 | 1.20 | 0.00 | 0.633 | +0.097 | 0.105 | 0.154 | 4 | 0.10 | 803 | no |
| 35 | 1.0 | 1.20 | 0.25 | 0.662 | +0.126 | 0.095 | 0.138 | 4 | 0.10 | 797 | no |
| 36 | 1.0 | 1.20 | 0.50 | 0.667 | +0.131 | 0.095 | 0.138 | 7 | 0.17 | 787 | no |
| 37 | 1.5 | 0.40 | 0.00 | 0.810 | +0.274 | 0.591 | 0.514 | 32 | 0.78 | 200 | no |
| 38 | 1.5 | 0.40 | 0.25 | 0.820 | +0.284 | 0.590 | 0.511 | 32 | 0.78 | 200 | no |
| 39 | 1.5 | 0.40 | 0.50 | 0.829 | +0.293 | 0.602 | 0.520 | 25 | 0.61 | 194 | no |
| 40 | 1.5 | 0.55 | 0.00 | 0.810 | +0.274 | 0.591 | 0.514 | 32 | 0.78 | 200 | no |
| 41 | 1.5 | 0.55 | 0.25 | 0.820 | +0.284 | 0.590 | 0.511 | 32 | 0.78 | 200 | no |
| 42 | 1.5 | 0.55 | 0.50 | 0.829 | +0.293 | 0.602 | 0.520 | 25 | 0.61 | 194 | no |
| 43 | 1.5 | 0.70 | 0.00 | 0.810 | +0.274 | 0.587 | 0.516 | 32 | 0.78 | 207 | no |
| 44 | 1.5 | 0.70 | 0.25 | 0.820 | +0.284 | 0.589 | 0.516 | 32 | 0.78 | 206 | no |
| 45 | 1.5 | 0.70 | 0.50 | 0.809 | +0.272 | 0.602 | 0.526 | 25 | 0.61 | 198 | no |
| 46 | 1.5 | 0.85 | 0.00 | 0.848 | +0.312 | 0.557 | 0.497 | 32 | 0.78 | 257 | no |
| 47 | 1.5 | 0.85 | 0.25 | 0.848 | +0.312 | 0.558 | 0.500 | 32 | 0.78 | 255 | no |
| 48 | 1.5 | 0.85 | 0.50 | 0.840 | +0.303 | 0.553 | 0.497 | 31 | 0.76 | 251 | no |
| 49 | 1.5 | 1.00 | 0.00 | 0.810 | +0.274 | 0.506 | 0.480 | 29 | 0.71 | 364 | no |
| 50 | 1.5 | 1.00 | 0.25 | 0.810 | +0.274 | 0.492 | 0.462 | 29 | 0.71 | 363 | no |
| 51 | 1.5 | 1.00 | 0.50 | 0.810 | +0.274 | 0.489 | 0.453 | 30 | 0.73 | 357 | no |
| 52 | 1.5 | 1.20 | 0.00 | 0.843 | +0.306 | 0.270 | 0.269 | 20 | 0.49 | 556 | no |
| 53 | 1.5 | 1.20 | 0.25 | 0.815 | +0.279 | 0.294 | 0.289 | 20 | 0.49 | 550 | no |
| 54 | 1.5 | 1.20 | 0.50 | 0.843 | +0.306 | 0.287 | 0.275 | 20 | 0.49 | 537 | no |

### Outcome

* Rule branch: the feasible set (uplift >= 0.15) has 42 cells. Chosen: **cell 7** (dense weight 0.5, cutoff 0.70, entity
  weight 0.0).
* Chosen cell: per-episode recall 0.710 against baseline 0.536 (uplift +0.174, 90% CI [0.101, 0.256]); contamination 0.119
  (90% CI [0.021, 0.154]); largest predicted cluster 8 against largest labelled episode 41 (ratio 0.20); all-item B-cubed
  P/R/F1 0.975/0.842/0.904; pairwise precision 0.839, recall 0.128 (2 ambiguous links, 279 proposed links recorded).
* **Pass criteria on the development set: NOT met.** Criterion 1 (contamination 90% upper bound <= 0.15) fails at **0.154**,
  a miss of 0.004 that the criteria do not forgive; no cell of the 54 passes all three (`any_cell_passes_all` is empty). The
  criteria were not relaxed. Criteria 2 and 3 hold for the chosen cell. The lowest upper bounds among the 42 feasible cells are 0.154 (cell 7), 0.159 (cell 8), 0.171 (cell 31) and 0.178
  (cell 32); at dense weight 1.5 the lowest is 0.269 (cell 52), with cells at cutoffs up to 0.70 collapsing the pool to
  about 200 events (few, large clusters).
* Against arm A's chosen cell (cell 30 of `V23_DEV_TUNING.md`), same three criteria:

| Criterion | Arm A (cell 30) | Arm B (cell 7) | Needed |
|---|---|---|---|
| Contamination 90% upper bound | 0.186 (fail) | 0.154 (fail) | <= 0.15 |
| Largest cluster / largest labelled episode | 0.27 (11 of 41, pass) | 0.20 (8 of 41, pass) | <= 1.5 |
| Per-episode recall uplift over baseline | +0.168 (pass) | +0.174 (pass) | >= +0.15 |

  Arm B lowers contamination (0.161 to 0.119 point) and moves recall by +0.006; neither difference is outside the story-
  resampled intervals, which are wide on 300 items and one dominant story. It does not turn a failing criterion into a pass.
* Behaviour of the chosen cell, per story (core items, clusters, outsiders): **Houthi/Saudi** 27 core items in 16 clusters,
  the largest holding 8, no outsider in any cluster it fills (arm A: 15 clusters, largest 11). **Aden palace** 2 core items,
  2 singleton clusters, not merged into the Houthi cluster (arm A: the same). **CPI Sep** 3 core items in 3 clusters, one of
  which also holds the PPI item (arm A: the same). **CoreWeave distress** 4 core items in 2 clusters (3 + 1), no outsider
  (arm A: the same). The dense term did not repair the main error of arm A, the fragmentation of the long Houthi campaign;
  it removed some false merges at the same recall. At lower cutoffs or higher dense weights the Houthi story does merge
  (cells 1 to 3: largest cluster 30) but only at contamination above 0.47.
* `config/events_v23.yaml` `arm_b` holds cell 7; the top-level `arm:` stays `A`. Choosing the primary arm is a freeze
  decision under the pre-registration, and this record does not make it.

## Pairwise separability of the dense cosine alone (information only; seen set)

The 278 non-peripheral reference items, 38,503 pairs, 406 positive pairs of which 351 are Houthi/Saudi. Arm A's score is the
Opus audit's `char35_tfidf[bg28]` (character 3-5-gram TF-IDF with the 28-day background); the dense score is the stored
`bge-small-en-v1.5` cosine on the same pairs.

| Score | AUC, all pairs | best precision at recall >= 0.8, all | AUC, Houthi positives removed | best precision at recall >= 0.8, Houthi removed |
|---|---|---|---|---|
| char 3-5-gram TF-IDF (arm A title term) | 0.981 | 0.372 (threshold 0.130) | 0.964 | 0.126 (threshold 0.159) |
| dense cosine (bge-small) | 0.959 | 0.115 (threshold 0.567) | 0.987 | 0.093 (threshold 0.653) |

Mean dense cosine is 0.640 for same-episode pairs and 0.438 for different-episode pairs (95th percentile of the latter
0.580), so a dense-only threshold at recall 0.8 sits inside the negative distribution. Dense ranks the non-Houthi positives
better (AUC 0.987 against 0.964) but neither score reaches useful precision at recall 0.8 once the Houthi pairs are
removed; the dense cosine alone is worse than the lexical score on the full pair set because it ranks distinct events of
the same region and theme above the long Houthi chain's weakest pairs. Information only: nothing was tuned on it.

## Addendum 2026-10-18 (P5a): re-statement under the corrected contamination gate

The original numbers above are not edited. The gate is now max(point, basic-bootstrap upper = 2 * point - 5th percentile)
(`V23_PREREGISTRATION.md` section 10). For the chosen arm B cell, with the unchanged bootstrap draws (seed 20261019,
1000 draws): contamination point 0.119, percentile 95th 0.154, **basic upper bound / gate 0.217** (criterion 0.15).
The criterion is still NOT met; under the corrected gate it fails by a wider margin. Scorer change to NONE/NOISE keying
(by `report_label`) leaves every development score identical (re-run and compared byte for byte).

