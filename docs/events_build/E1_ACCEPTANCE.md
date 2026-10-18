# E1 acceptance, run (a) as-stored: first and only run

Date 2026-10-18. Code at `011698e` (builder `199b535`, replay `011698e`). Parameters: `config/events.yaml` version 2, unchanged.
Labels: reference set v1.1, unchanged. Command (run once, after the code and unit tests were finished):

    python scripts/events_replay.py --mode stored --db data/macro_engine.duckdb --out <report.json>

Nothing was classified and DeepSeek was not called. Run (b) (as-if-all-classified) was not run: it needs the operator's
approval to classify 158 items (LABELLING.md). Run (a) is reported, not gating; run (b) carries the zero-tolerance gates.

## Result

| | value |
|---|---|
| items / with a stored payload | 300 / 142 |
| false-split | 37 / 45 = 82.2% (limit 10%) FAIL |
| false-merge | 123 / 131 = 93.9% (limit 5%) FAIL |
| same two rates over the 7-day pool at 2026-10-17T00:00Z | split 33/40, merge 111/118 |
| `E_HOUTHI_SAUDI` | FAIL. 27 core: 20 unattached, 6 in one 19-report mixed event (12 outsiders), 1 in a singleton |
| `E_COREWEAVE_DISTRESS` | FAIL. 4 core: 3 unattached, 1 in a singleton that is `detected`, not corroborated |
| `E_CPI_SEP` | FAIL. 3 core: 2 unattached, 1 in the same mixed event (17 outsiders) |
| `E_HOUTHI_ADEN_PALACE` | FAIL. 2 core, both unattached (neither is classified, so neither can start an event) |
| ids that vanished while their onset item was still pooled | 3 |

## Diagnosed failure modes

**1. Unattached members (the gates, expected).** An unclassified report never starts an event and links only through
title Jaccard >= 0.4 to a report already in an event. 158 of 300 items have no classification, 20 of 27 Houthi core
members, 3 of 4 CoreWeave, 2 of 3 CPI and both Aden members among them (LABELLING.md, classification state). They stay unattached because they cannot start an event
and did not reach the 0.4 overlap with a report already in one (not broken down per item). This is the information
run (a) was defined to give: with this coverage the gates cannot pass. It is not a builder error.

**2. One hub event swallows unrelated news (the false merges; a rule-level finding).** `evt_e43c0678bba4634b` has 19
reports from 2026-10-11 to 10-16: Houthi airport strikes, Indian CPI, Black Sea shipping insurance, Goldman on China,
IMF yields, US PPI, UK GDP, a container ship in Hormuz. It holds members of four labelled events (Houthi, CPI, PPI, Hormuz) and eight NONE items (the outsiders counted by the gates). It
grows one report at a time, and each join satisfies plan 5.4 as written: the new report shares a resolved or
normalised entity with the event (most often `crude oil` 7 joins, `iran` 6, `saudi arabia` 5, `federal reserve` 4,
`united kingdom` 2, `china`) and shares at least one theme with the same direction (the event carries 25
theme-direction pairs by the end, so almost any report shares one). None of those entities is on the generic stop
list, and the plan's stop list is deliberately small ("the Fed" must link). A shared entity plus a shared theme is a
weak test for broad entities (a commodity, a central bank, a country) and for broad themes.

**3. Where the plan is ambiguous and I chose a reading.** Section 5.4 says a report "shares at least one resolved
entity" and "shares at least one macro theme" without saying with what. I compared against the **event** (union of all
its reports' entities and (theme, direction) pairs); only the Jaccard clause names "an event report". The union reading
is what lets a hub grow by chaining. Comparing against a single report of the event would restrict chaining, but would
not remove it (a chain of pairwise links still joins A to Z). I did not try the alternative: the parameters and rules are
frozen and the acceptance run is once. This is for the operator to rule on (and ratify with the 5.4 wording), not a
code bug I fixed.

**4. The Aden/Houthi relation cannot be shown by run (a)** (no classified Aden member). In run (b) the new-development
rule (5.5) only fires when the Aden report shares no (theme, direction) with the Houthi event; Aden and Houthi both carry
`geopolitical_risk`-type themes, so under the 5.4 clause the Aden report is likely to **link into** the Houthi event, not
start a related one. That is a prediction from the rule text, not a result.

**5. Event ids.** Ids derive from the earliest-seen member of the event's first report, so they do not depend on run
time. 3 ids disappeared while their onset item was still in the pool, each because the event was absorbed into an older
event after a classification arrived (chaining again): `evt_3e2b...` (10-12), `evt_845d...` (10-13), `evt_75b6...`
(10-17). There was no case of the same event getting a new id. An id changes only when an event merges.

## Not done / next

No code change was made after the run, and none is proposed as a bug fix: no builder defect was found by diagnosis
(determinism, look-ahead and id derivation tests pass; the hub event is the specified rules operating on broad
entities and broad themes). Operator rulings needed before run (b): (i) compare shared entity/theme against the event
union or a single report (5.4); (ii) whether broad entities (commodities, countries, central banks) belong on the
stop list or need an inverse-frequency discount, which would be a new parameter; (iii) approval to classify the 158 items
for run (b).

---

# Phase 3b: full-store replay, v2 and v2_2 (INFORMATION, not a gate)

Date 2026-10-18. Code at `3b1b39c` (erratum v2.2 behind `linking`, `events.yaml` version 3). Parameters were not
changed after seeing any number below, and the labels are untouched. Each command was run once:

    python scripts/events_replay.py --mode stored --pool store --linking v2   --out <report.json>
    python scripts/events_replay.py --mode stored --pool store --linking v2_2 --out <report.json>

The first run above (section "first and only run") stays as recorded: it built over the 300 fixture items only. Its numbers are
reproduced by `--pool fixture --linking v2` (false-split 37/45, false-merge 123/131, 3 ids lost; checked, and now a test under
`MRI_STORE_CHECKS=1`). The runs here build, for each day's `as_of`, over **every item in the store** (27,594 items; 700 to 775
first seen in each 7-day pool, 324 classified at the 10-17 instant, 142 of the 300 reference items carrying a payload), with
no look-ahead, and score only the 300 reference items. (These counts differ from the audit's "944 items, 504 classified"; I did not
establish why. The replay counts only classifications made by each `as_of`.) The store was still being appended by the live pipeline
when these were run (newest `first_seen_at` 2026-10-18 00:26), so a later re-run can differ in the background and the pool.

**These runs are information.** The reference set has been seen and the v2.2 rule was designed after seeing it, so the reference
set cannot validate v2_2; that needs a blind holdout built later.

| | v2 (plan 5.4 as written) | v2_2 (erratum) |
|---|---|---|
| false-split (limit 10%) | 37 / 45 = 82.2% FAIL | 41 / 45 = 91.1% FAIL |
| false-merge (limit 5%) | 505 / 513 = 98.4% FAIL | 16 / 20 = 80.0% FAIL |
| final-pool rates (10-17), split / merge | 33/40, 496/503 | 37/40, 14/17 |
| largest event, 10-17 / any day | 123 items / 176 | 12 items / 19 |
| ids lost while the onset item was pooled | 17 | 22 |
| `E_HOUTHI_SAUDI` | FAIL: 20 of 27 core unattached, 1 in a 3-item event, 6 in the 41-item hub (34 outsiders) | FAIL: 20 unattached, the other 7 in three events (5, 1, 1), no outsiders |
| `E_COREWEAVE_DISTRESS` | FAIL: 3 of 4 unattached, 1 in the hub | FAIL: 3 unattached, 1 in a one-item `detected` event |
| `E_CPI_SEP` | FAIL: 2 of 3 unattached, 1 in the hub | FAIL: 2 unattached, 1 in a 3-item corroborated event (2 outsiders) |
| `E_HOUTHI_ADEN_PALACE` | FAIL: both core unattached (unclassified) | FAIL: both core unattached (unclassified) |

What this shows, without drawing a conclusion about quality:

- Over the whole store the v2 hub is far larger than the fixture-only one (123 items against 19): the fixture-only run
  understated the chaining. v2_2 cuts the largest event to 12 items and removes the outsiders from the Houthi event.
- v2_2 does not repair false-split: 20 of 27 Houthi core, 3 of 4 CoreWeave, 2 of 3 CPI and both Aden members are unclassified in
  run (a), so they cannot start or join an event, in either mode. The gates need run (b) (classify the 158 items; operator approval).
- The v2_2 false-merge rate (16/20) rests on 20 same-cluster pairs; v2 has 513. The rate is a ratio of small numbers there.
- The Aden/Houthi relation is not shown (no classified Aden member), and the prediction in `SPEC_FREEZE.md` stands: it is expected to
  link into the Houthi event once classified, and to fail its gate.
