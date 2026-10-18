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

(filled in after the single run)
