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
members, 3 of 4 CoreWeave, 2 of 3 CPI and both Aden members among them (LABELLING.md, classification state). Most of
these have no title overlap >= 0.4 with the few classified members, so they stay unattached. This is the information
run (a) was defined to give: with this coverage the gates cannot pass. It is not a builder error.

**2. One hub event swallows unrelated news (the false merges; a rule-level finding).** `evt_e43c0678bba4634b` has 19
reports from 2026-10-11 to 10-16: Houthi airport strikes, Indian CPI, Black Sea shipping insurance, Goldman on China,
IMF yields, US PPI, UK GDP, a container ship in Hormuz. 12 of its labelled members are outsiders to each other. It
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
