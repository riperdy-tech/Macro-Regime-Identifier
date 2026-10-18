# Stage 1 candidate register: specification

Written 2026-10-18 (Phase 4a). Operator approvals of 2026-10-18: Stage 1 ships as the **candidate register** with E2
de-duplication by (instrument, reference close, window); single-source candidates are **shown, not hidden**. This
document supersedes plan section 5.6/5.7 for production (see erratum v3 in `SPEC_FREEZE.md`; v2.2 is kept as a
comparator). It is part of the frozen set in `V23_PREREGISTRATION.md`.

A candidate is a grouping a linker proposed, with the evidence for it. **A candidate is never described as a confirmed
event** in any field name, status value, log line or UI string; the words "confirmed event" do not appear. In Stages 1 and
2 a candidate never changes any number, score or verdict (D1). It is a record, not a trading signal.

## Record

One candidate per persistent event in `events/state.py` (the `event_id` is the `candidate_id`). Additive artifact
`candidate_register.json`, versioned, written beside the other outputs; no existing output changes.

| Field | Meaning |
| --- | --- |
| `candidate_id` | the stable `evt_...` id from creation; never renamed or deleted; supersession is a separate record |
| `report_ids`, `item_ids` | the collapsed reports of the candidate and the news items inside them |
| `deduped_report_count` | reports after syndication collapse (the counted unit) |
| `distinct_publishers` | publishers of the reports, each with `syndication_provenance` (which items are wire copies of which report) |
| `corroboration` | evidence category, one of `officially_sourced`, `independently_corroborated`, `single_source`, `conflicting`; the evidence is listed, not collapsed to a count. Publisher count alone is not independence: wire copies are one source |
| `classification_status` | per report: classified / unclassified / failed, with `classified_at`; coverage of the candidate |
| `entities_resolved`, `entities_unresolved` | resolver output (company-level keys, `tickers`) and normalised unresolved names, with resolver flags carried as flags |
| `themes` | theme-direction annotations from the classifier. **Annotation only, not identity**: never a link or dedup key |
| `related_candidates` | other candidates with the match evidence (`proposed_link` records: score, anchors, terms) |
| `storyline` | the storyline link if any (id, relation), annotation until a storyline linker is separately approved |
| `onset` | **`published_at`** of the earliest report (null with flag `published_at_missing` if none). E2 uses this |
| `first_available_at` | **`first_seen_at`** of the earliest report: when the system could first have known. The look-ahead boundary |
| `last_report_at`, `status_window` | latest `first_seen_at`; `open` or `closed` per the 3-day rule, with `closed_on` |
| `market_response` | empty block until E2 fills it: per (instrument, reference close date, window) entries |
| `flags` | annotations (see below); flags annotate, they never gate |

Flags carried (annotate, never silently gate): `published_at_missing`, `background_thin`, `unclassified_reports`,
`conflicting_reports`, `single_source`, `syndication_only`, `resolver_flags`, and any linker flag on a report.

## Visibility

**Single-source candidates are shown.** The plan's "hidden by default for `rejected_uncorroborated`" is superseded: a
candidate with one source is displayed with `corroboration: single_source` and does not vanish after 72 hours. The old
status `rejected_uncorroborated` is not produced in the register. Nothing is deleted by a threshold; a quiet candidate
becomes `closed`, not hidden.

## E2 de-duplication key

Market checks are keyed by **(instrument, reference close date, window)**. Fragments of one story that resolve to the same
key count **once**: the check is computed once and attached to each candidate that shares the key, with `shared_with` listing
them, and any aggregate over candidates counts the key, not the candidates. `reference close date` is the `P_ref` session
(plan erratum v2.1: last session close at or before the onset instant, from `published_at`). An unresolved instrument or a
missing observation is `data_unavailable` with a reason, never zero (`0.0` is a value).

Browse facets (entity, theme) may group candidates for display. They are **never a dedup key and never an identity key**.

## What the register does not claim

It does not say that two candidates are or are not the same story beyond the stored linker evidence; it does not say that a
single-source candidate is true or false; it makes no claim about market impact (that is E2, orthogonal and empty here); and
it is not investment advice.
