# v2.3 arm A: development-set tuning (Phase 4b)

Written 2026-10-18 BEFORE any grid cell was run. Branch `feat/mri-events`. Binding documents: `V23_PREREGISTRATION.md`,
`CANDIDATE_REGISTER_SPEC.md`. The grid and the selection rule below are fixed by this commit; nothing in them is
adjusted after the results are seen.

## Status of the evidence

The reference set (300 items, 2026-10-08..10-16, `tests/fixtures/events_reference/`) is a DEVELOPMENT set. It has been
seen repeatedly and informed the labelling guide. **Every figure below is OPTIMISTIC: parameters chosen on seen data, then
scored on the same data. None of it is evidence that arm A passes.** The pass decision is the blind holdout, one run,
under `V23_PREREGISTRATION.md`.

Labels: `labels.yaml` v1.1 carries a single `event` per item. For the scorer each event id is used as the episode
(`episode_id = event`, `storyline_id = event`, `report_label = report`, `NONE` stays `NONE`, `noise` becomes `NOISE`,
`membership` unchanged). The Aden palace and the Houthi/Saudi campaign are therefore separate units here, and storyline
recall equals episode recall on this set. Labels are not touched.

## What is replayed

Whole-store pool, day by day (`scripts/events_v23_replay.py`): for each UTC day D from the first labelled day to the last,
`as_of` = 00:00Z of D+1; the pool is the items first seen in the 7 days up to `as_of` and no earlier than the first labelled
day; reports are syndication-collapsed with the builder's rule; each not-yet-assigned report goes through
`EventState.apply_day` with the arm A linker. Stored classification only (a classification counts when `classified_at <=
as_of`, as production sees it); no overlay is read. The IDF background is the reports first seen in the `background_days`
days before the first labelled day. All 300 reference items are scored; the other pooled items are distractors.

## Grid (60 cells, fixed)

| Parameter | Values |
| --- | --- |
| `cutoff` | 0.25, 0.30, 0.35, 0.45, 0.55 |
| `entity_weight` | 0.0, 0.25, 0.5 |
| (`title_cosine_floor`, `entity_idf_floor`) | (0.10, 2.0), (0.20, 3.0) |
| `background_days` | 14, 28 |

5 x 3 x 2 x 2 = 60 cells. Held fixed (not tuned, not in the grid): n-gram range 3-5, `title_idf_offset` 1.0, anchors 3,
`ambiguity_margin` 0.05, `near_miss_fraction` 0.8 (the last two only add annotations and proposed links; they do not change
which cluster an item lands in).

## Selection rule (declared first)

For each cell compute the pre-registered scorer output: contamination point value and 90% story-resampled interval, per-
episode recall uplift over the label-based syndication baseline, largest predicted cluster against the largest labelled
episode.

1. Feasible set F = cells whose per-episode recall uplift (point estimate) is **at least 0.15**.
2. If F is not empty: choose the cell with the **smallest contamination 90% upper bound** (a cell with undefined
   contamination ranks last); tie-break the **smaller largest predicted cluster**; a further tie goes to the smaller
   `cutoff`, then smaller `entity_weight`, then the earlier row of the grid above.
3. If F is empty: say so plainly, and report as "best cell" the one with the **largest uplift** (tie-break smaller
   contamination upper bound). The criteria are not relaxed.

The size criterion (largest cluster <= 1.5 x largest labelled episode) is NOT part of the selection. It is reported for the
chosen cell, and the table also records whether any cell passes all three pre-registered criteria, as information.

The chosen cell is written to `config/events_v23.yaml`. The full results table (all 60 cells) is filled in below by the run.

## Run record

One run of the 60 cells (`python scripts/events_v23_replay.py --tune`, raw output `v23_dev_grid.json`), then one run of the
chosen cell (`v23_dev_chosen.json`). The grid and rule above were not changed. **All figures are OPTIMISTIC (seen data).**
"recall" is mean per-episode recall; "uplift" is over the label-based syndication baseline (0.536, identical to the
builder-collapse baseline); "contam upper" is the story-resampled 90% upper bound; "ratio" is largest predicted cluster over
the largest labelled episode (41 items). The two floor settings behave identically in almost every row: the title cosine of
any linked pair already clears 0.2, so the floors gate almost nothing here.

| # | cutoff | w_ent | floors (title, ent-idf) | bg days | recall | uplift | contam | contam 90% upper | largest cluster | ratio | events | pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.25 | 0.00 | (0.10, 2.0) | 14 | 0.760 | 0.223 | 0.279 | 0.265 | 16 | 0.39 | 633 | no |
| 2 | 0.25 | 0.00 | (0.10, 2.0) | 28 | 0.779 | 0.243 | 0.267 | 0.254 | 14 | 0.34 | 638 | no |
| 3 | 0.25 | 0.00 | (0.20, 3.0) | 14 | 0.760 | 0.223 | 0.279 | 0.265 | 16 | 0.39 | 633 | no |
| 4 | 0.25 | 0.00 | (0.20, 3.0) | 28 | 0.779 | 0.243 | 0.267 | 0.254 | 14 | 0.34 | 638 | no |
| 5 | 0.25 | 0.25 | (0.10, 2.0) | 14 | 0.760 | 0.223 | 0.287 | 0.272 | 16 | 0.39 | 620 | no |
| 6 | 0.25 | 0.25 | (0.10, 2.0) | 28 | 0.779 | 0.243 | 0.272 | 0.262 | 14 | 0.34 | 626 | no |
| 7 | 0.25 | 0.25 | (0.20, 3.0) | 14 | 0.760 | 0.223 | 0.283 | 0.269 | 16 | 0.39 | 626 | no |
| 8 | 0.25 | 0.25 | (0.20, 3.0) | 28 | 0.779 | 0.243 | 0.272 | 0.262 | 14 | 0.34 | 633 | no |
| 9 | 0.25 | 0.50 | (0.10, 2.0) | 14 | 0.760 | 0.223 | 0.319 | 0.316 | 16 | 0.39 | 601 | no |
| 10 | 0.25 | 0.50 | (0.10, 2.0) | 28 | 0.779 | 0.243 | 0.321 | 0.316 | 14 | 0.34 | 600 | no |
| 11 | 0.25 | 0.50 | (0.20, 3.0) | 14 | 0.760 | 0.223 | 0.286 | 0.275 | 16 | 0.39 | 624 | no |
| 12 | 0.25 | 0.50 | (0.20, 3.0) | 28 | 0.779 | 0.243 | 0.279 | 0.268 | 14 | 0.34 | 631 | no |
| 13 | 0.30 | 0.00 | (0.10, 2.0) | 14 | 0.763 | 0.226 | 0.205 | 0.212 | 16 | 0.39 | 704 | no |
| 14 | 0.30 | 0.00 | (0.10, 2.0) | 28 | 0.748 | 0.211 | 0.164 | 0.186 | 12 | 0.29 | 716 | no |
| 15 | 0.30 | 0.00 | (0.20, 3.0) | 14 | 0.763 | 0.226 | 0.205 | 0.212 | 16 | 0.39 | 704 | no |
| 16 | 0.30 | 0.00 | (0.20, 3.0) | 28 | 0.748 | 0.211 | 0.164 | 0.186 | 12 | 0.29 | 716 | no |
| 17 | 0.30 | 0.25 | (0.10, 2.0) | 14 | 0.763 | 0.226 | 0.212 | 0.215 | 16 | 0.39 | 693 | no |
| 18 | 0.30 | 0.25 | (0.10, 2.0) | 28 | 0.749 | 0.213 | 0.187 | 0.200 | 13 | 0.32 | 706 | no |
| 19 | 0.30 | 0.25 | (0.20, 3.0) | 14 | 0.763 | 0.226 | 0.212 | 0.215 | 16 | 0.39 | 696 | no |
| 20 | 0.30 | 0.25 | (0.20, 3.0) | 28 | 0.749 | 0.213 | 0.187 | 0.200 | 13 | 0.32 | 709 | no |
| 21 | 0.30 | 0.50 | (0.10, 2.0) | 14 | 0.763 | 0.226 | 0.244 | 0.250 | 16 | 0.39 | 676 | no |
| 22 | 0.30 | 0.50 | (0.10, 2.0) | 28 | 0.760 | 0.223 | 0.232 | 0.245 | 13 | 0.32 | 684 | no |
| 23 | 0.30 | 0.50 | (0.20, 3.0) | 14 | 0.763 | 0.226 | 0.230 | 0.241 | 16 | 0.39 | 691 | no |
| 24 | 0.30 | 0.50 | (0.20, 3.0) | 28 | 0.749 | 0.213 | 0.208 | 0.224 | 13 | 0.32 | 703 | no |
| 25 | 0.35 | 0.00 | (0.10, 2.0) | 14 | 0.684 | 0.147 | 0.153 | 0.182 | 11 | 0.27 | 760 | no |
| 26 | 0.35 | 0.00 | (0.10, 2.0) | 28 | 0.682 | 0.146 | 0.158 | 0.182 | 10 | 0.24 | 762 | no |
| 27 | 0.35 | 0.00 | (0.20, 3.0) | 14 | 0.684 | 0.147 | 0.153 | 0.182 | 11 | 0.27 | 760 | no |
| 28 | 0.35 | 0.00 | (0.20, 3.0) | 28 | 0.682 | 0.146 | 0.158 | 0.182 | 10 | 0.24 | 762 | no |
| 29 | 0.35 | 0.25 | (0.10, 2.0) | 14 | 0.706 | 0.170 | 0.188 | 0.213 | 12 | 0.29 | 742 | no |
| 30 | 0.35 | 0.25 | (0.10, 2.0) | 28 | 0.704 | 0.168 | 0.161 | 0.186 | 11 | 0.27 | 749 | no |
| 31 | 0.35 | 0.25 | (0.20, 3.0) | 14 | 0.706 | 0.170 | 0.188 | 0.213 | 12 | 0.29 | 743 | no |
| 32 | 0.35 | 0.25 | (0.20, 3.0) | 28 | 0.704 | 0.168 | 0.161 | 0.186 | 11 | 0.27 | 751 | no |
| 33 | 0.35 | 0.50 | (0.10, 2.0) | 14 | 0.706 | 0.170 | 0.200 | 0.229 | 12 | 0.29 | 736 | no |
| 34 | 0.35 | 0.50 | (0.10, 2.0) | 28 | 0.704 | 0.168 | 0.200 | 0.229 | 11 | 0.27 | 736 | no |
| 35 | 0.35 | 0.50 | (0.20, 3.0) | 14 | 0.706 | 0.170 | 0.200 | 0.229 | 12 | 0.29 | 744 | no |
| 36 | 0.35 | 0.50 | (0.20, 3.0) | 28 | 0.704 | 0.168 | 0.200 | 0.229 | 11 | 0.27 | 745 | no |
| 37 | 0.45 | 0.00 | (0.10, 2.0) | 14 | 0.582 | 0.046 | 0.114 | 0.185 | 6 | 0.15 | 812 | no |
| 38 | 0.45 | 0.00 | (0.10, 2.0) | 28 | 0.582 | 0.046 | 0.114 | 0.185 | 6 | 0.15 | 813 | no |
| 39 | 0.45 | 0.00 | (0.20, 3.0) | 14 | 0.582 | 0.046 | 0.114 | 0.185 | 6 | 0.15 | 812 | no |
| 40 | 0.45 | 0.00 | (0.20, 3.0) | 28 | 0.582 | 0.046 | 0.114 | 0.185 | 6 | 0.15 | 813 | no |
| 41 | 0.45 | 0.25 | (0.10, 2.0) | 14 | 0.628 | 0.092 | 0.100 | 0.154 | 9 | 0.22 | 803 | no |
| 42 | 0.45 | 0.25 | (0.10, 2.0) | 28 | 0.646 | 0.110 | 0.095 | 0.143 | 7 | 0.17 | 803 | no |
| 43 | 0.45 | 0.25 | (0.20, 3.0) | 14 | 0.628 | 0.092 | 0.100 | 0.154 | 9 | 0.22 | 803 | no |
| 44 | 0.45 | 0.25 | (0.20, 3.0) | 28 | 0.646 | 0.110 | 0.095 | 0.143 | 7 | 0.17 | 803 | no |
| 45 | 0.45 | 0.50 | (0.10, 2.0) | 14 | 0.651 | 0.115 | 0.111 | 0.158 | 10 | 0.24 | 787 | no |
| 46 | 0.45 | 0.50 | (0.10, 2.0) | 28 | 0.648 | 0.111 | 0.111 | 0.158 | 8 | 0.20 | 789 | no |
| 47 | 0.45 | 0.50 | (0.20, 3.0) | 14 | 0.651 | 0.115 | 0.111 | 0.158 | 10 | 0.24 | 792 | no |
| 48 | 0.45 | 0.50 | (0.20, 3.0) | 28 | 0.648 | 0.111 | 0.111 | 0.158 | 8 | 0.20 | 795 | no |
| 49 | 0.55 | 0.00 | (0.10, 2.0) | 14 | 0.567 | 0.031 | 0.115 | 0.167 | 3 | 0.07 | 834 | no |
| 50 | 0.55 | 0.00 | (0.10, 2.0) | 28 | 0.566 | 0.029 | 0.130 | 0.174 | 3 | 0.07 | 837 | no |
| 51 | 0.55 | 0.00 | (0.20, 3.0) | 14 | 0.567 | 0.031 | 0.115 | 0.167 | 3 | 0.07 | 834 | no |
| 52 | 0.55 | 0.00 | (0.20, 3.0) | 28 | 0.566 | 0.029 | 0.130 | 0.174 | 3 | 0.07 | 837 | no |
| 53 | 0.55 | 0.25 | (0.10, 2.0) | 14 | 0.588 | 0.052 | 0.103 | 0.147 | 3 | 0.07 | 827 | no |
| 54 | 0.55 | 0.25 | (0.10, 2.0) | 28 | 0.586 | 0.050 | 0.120 | 0.167 | 3 | 0.07 | 830 | no |
| 55 | 0.55 | 0.25 | (0.20, 3.0) | 14 | 0.588 | 0.052 | 0.103 | 0.147 | 3 | 0.07 | 827 | no |
| 56 | 0.55 | 0.25 | (0.20, 3.0) | 28 | 0.586 | 0.050 | 0.120 | 0.167 | 3 | 0.07 | 830 | no |
| 57 | 0.55 | 0.50 | (0.10, 2.0) | 14 | 0.612 | 0.076 | 0.097 | 0.133 | 5 | 0.12 | 816 | no |
| 58 | 0.55 | 0.50 | (0.10, 2.0) | 28 | 0.610 | 0.074 | 0.100 | 0.136 | 4 | 0.10 | 816 | no |
| 59 | 0.55 | 0.50 | (0.20, 3.0) | 14 | 0.612 | 0.076 | 0.097 | 0.133 | 5 | 0.12 | 818 | no |
| 60 | 0.55 | 0.50 | (0.20, 3.0) | 28 | 0.610 | 0.074 | 0.100 | 0.136 | 4 | 0.10 | 820 | no |

### Outcome

* Rule branch: feasible set (uplift >= 0.15) has 32 cells. Chosen: **cell 30** (cutoff 0.35, entity weight 0.25, floors
  (0.10, 2.0), background 28 days).
* Chosen cell: per-episode recall 0.704 against baseline 0.536 (uplift +0.168, 90% CI [0.092, 0.252]); contamination 0.161
  (90% CI [0.034, 0.186]); largest predicted cluster 11 against largest labelled episode 41 (ratio 0.27); all-item B-cubed
  P/R/F1 0.963/0.847/0.901; pairwise precision 0.843, recall 0.185 (8 ambiguous links, 115 proposed links recorded).
* **Pass criteria on the development set: NOT met.** Criterion 1 (contamination 90% upper bound <= 0.15) fails at 0.186; no
  cell of the 60 passes all three criteria (the lowest upper bound among feasible cells is 0.186). Criteria 2 and 3 hold for
  the chosen cell. The criteria were not relaxed. Every cell that has a contamination upper bound at or below 0.15 (cutoffs
  0.45 and 0.55, entity weight >= 0.25) has an uplift below 0.15.
* Behaviour of the chosen cell: Houthi/Saudi (27 core items) sits in 15 clusters, the largest holding 11, with no outsider
  in the clusters it fills; Aden palace (2 core) is not merged into the Houthi cluster (2 singletons); CPI Sep (3 core) is
  3 clusters, one of them also holding a PPI item; CoreWeave distress (4 core) is 2 clusters (3 + 1). Arm A under-merges (high
  precision, low recall) rather than building a hub; the main error is fragmentation of long campaigns.
* Larger cutoffs trade recall for contamination monotonically; entity weight helps contamination only at high cutoffs.

