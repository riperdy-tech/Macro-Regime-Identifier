# External AI reviews of the E1 failure note (summaries for the Opus auditor)

Prompt given to both: `docs/events_build/E1_FAILURE_CONSULTATION.md` (branch feat/mri-events). Summaries below are the operator-pasted
responses, condensed but keeping every substantive claim and number. The orchestrator did not alter claims.

## ChatGPT
Decision: ship Stage 1 as annotated candidate register; reject v2 (transitive hubs) and v2.2 (fragments, keep as comparator); Stages 1-2 non-decisional.
- Core claim: shared entity + shared theme is evidence of related context, not same event. Single-report comparison reduces but cannot eliminate chaining.
  Event model inconsistent across granularity: needs explicit two levels.
- Q1: separate three operations: report dedup (canonical title, provenance), event matching (distinctive entities, action, target, location, release
  identity, weighted title similarity, time compatibility), related-story detection (campaign, actor, causal link, theme). Theme never sufficient for
  identity. Pipeline: normalise identity features; candidate generation via rare-term overlap/entities/dates/title similarity; score against an event
  ANCHOR (stable representative), never the accumulating union; assign conservatively (retain ambiguous candidates, builder may say 'no link strong enough').
- Q2: deterministic auditable baseline first (weighted TF-IDF/sparse lexical, alias + variant normalisation, IDF down-weighting instead of hard cutoff,
  compatibility rules for actor/action/target/place/release, frozen scoring function). Embeddings only as a later, separately tested enhancement: pin
  model artifact/preprocessing/version, compute at ingestion with provenance, use for candidate generation or as one feature, never sole proof. Notes
  deterministic logic != bitwise-identical inference across hardware; define reproducibility standard.
- Q3: keep pairwise metrics (false-split = 1 - pairwise recall, false-merge = 1 - pairwise precision) but add B-cubed P/R macro-averaged by true event,
  event-level precision/recall, per-event fragmentation/contamination. Pairs not independent; resample by story. Holdout 300-500 items, >= 8-10
  multi-item stories with >= 5 core reports, deliberate hard negatives (same country/central bank/company/commodity, different story). All-classified
  replay != operational replay; separately measure classifier availability/latency. Freeze code/config/labels/scorer; one run.
- Q4: pool = discovery window not event lifetime. Persist reports, assignments, first-seen, provenance; stable id at creation; no silent retrospective
  reassignment; if two events appear to connect, record proposed connection with evidence; merges preserve lineage via aliases/supersession.
- Q5: yes ship candidates (items, deduped reports, candidate group ids, match scores + evidence, publisher/report counts with syndication provenance,
  entities/themes/timestamps/classification status, related candidates, corroboration evidence kept separate, market response orthogonal). Single-source
  candidates should not vanish after 72h. Corroboration categories: officially sourced / independently corroborated / single-source candidate /
  conflicting evidence. Publisher count alone not independence (wire copies).
- Q6: two-level model: storyline/campaign id plus event id (specific incident), explicit relationships. Houthi campaign = storyline; Aden = separate
  related event. Reject hard 1% cutoff; use smoothed frequency weighting, computed on pre-pool background after syndication collapse.
- Q7: classification delay/selection bias; changing report identity; publisher dependence; event vs storyline ambiguity; identity instability; late
  discovery/time truncation; published_at vs first_seen_at vs classified_at look-ahead. Two evaluations: frozen-classification matching test AND
  live-timing replay.
- Rulings: approve candidate register; reject v2.2 for production; approve name folding and persistent identity (as one versioned change set with
  canonical_title fix and redesigned linker, not as proxies); Aden separate related event; do NOT tune on the seen reference set; blind holdout after
  event-vs-storyline semantics and metrics are decided.

## Gemini
Core claim: failure is mathematical: an event needs Entity + Action + Time; plan uses Entity + Macro Theme + Time; 18 themes x direction = 36
low-entropy 'consequence' buckets, so distinct events collide; single-linkage chaining across high-frequency entities; v2.2's hard 1% filter cuts the
macro actors the system cares about.
- Q1/Q2: entity+theme not viable. A pinned local CPU sentence-embedding model (BAAI/bge-small-en-v1.5 or all-MiniLM-L6-v2 via ONNX) with fixed seeds is
  '100% deterministic', ~5 ms/headline, so 'no model call at build time' should mean no paid external LLM. Option A (recommended): hybrid
  Score = w1*dense cosine(report, event centroid) + w2*TF-IDF cosine + w3*weighted entity overlap. Option B (no weights): continuous IDF-weighted
  features over canonicalised entities (28-day IDF), title char 3-5 grams + word uni/bigrams (char n-grams fold houthi/houthis/yemens houthis), and
  classifier signature (theme+direction+sector impacts); weighted cosine vs event CENTROID or top-3 earliest/core reports (never full union) >= tau,
  plus >= 1 high-IDF entity OR 2 medium matches.
- Q3: protocol statistically brittle: Houthi = 351 of 406 positive pairs (86.5%); 45-pair denominator: one 4-item story splitting shifts false-split
  ~9 points; zero-tolerance on a 27-item story is virtually impossible for unsupervised stream clustering. Replace with B-cubed P/R/F1 over all labelled
  items; gates B3 precision >= 0.85, recall >= 0.80; replace zero-tolerance with cluster purity >= 0.85 and completeness >= 0.80 on flagship stories.
  Holdout ~150 stratified items covering 8-10 multi-report stories (4-12 items) across domains + ~150 NONE distractors.
- Q4: stateful event table; event stays OPEN while now - last_report <= 72h regardless of age; bounded summary state: 3 genesis anchors pinned, last 5
  reports, core entity profile = entities in >= 30% of reports; closed events never accept joins, keep id forever.
- Q5: yes ship Candidate Register: tight syndication/near-duplicate collapse, publisher counts, official flags; market check per candidate; UI groups
  candidates by Primary Entity + Macro Theme. Upgrade to Corroborated Events only after blind holdout B3 P >= 0.85 and R >= 0.80 with 100% classifier coverage.
- Q6: a multi-week Houthi campaign MUST NOT be one flat event: market reaction needs discrete shock onsets (Oct 8 facility, Oct 14 Riyadh airport, Oct 16
  Aden). Event = discrete episode (48-72h burst or distinct target/action); sequential episodes with the same actor+theme linked via related_event_ids.
  Aden is a separate episode under the same campaign. Hard 1% suppression is an anti-pattern; use continuous IDF weights (federal reserve ~0.25, coreweave ~0.95).
- Q7: (1) order-dependent greedy seeding by ambiguous wrap-up articles (mitigate: only focused, high-confidence reports seed clusters; hold multi-topic
  wrap-ups in a pending buffer, attach without updating centroid); (2) run (b) overlay bias: when the 150/day cap gives ~100% coverage of the
  background pool, active themes/entities roughly double and chaining pressure rises ~2-3x vs run (b); (3) rolling IDF baseline starvation at regime
  shifts (day 1 actor rare => merges, day 10 ubiquitous => splits; use 90-day baseline with max one count per calendar day per entity); (4) late
  aggregator echoes re-indexed 48-96h later with fresh first_seen spawn zombie duplicate events.
- Says our Step 2 (variant folding + window persistence on v2/v2.2) will still fail the blind holdout; proposes erratum v2.3: entity normalisation
  (possessives/plurals, role suffixes movement/group/ministry, canonical aliases fed/fomc/us federal reserve -> federal_reserve, bls/cpi -> us_cpi_report,
  char 4-gram Jaccard >= 0.75 fuzzy entity match); day-capped IDF = log((D+1)/(d(e)+1)) over D=28 days; bounded centroid/core profile; two-stage
  linkage (candidate gate within 72h AND (>=1 normalised entity OR title TF-IDF cosine >= 0.35); confirmation weighted sum >= tau or local embedding
  cosine >= 0.72); new-development rule: shared primary entity + same theme but title/predicate similarity < 0.15 and > 36h after event onset => new episode.
- Rulings: ship candidate register; approve canonical_title fix, entity normalisation, stateful identity, plus continuous IDF + title TF-IDF (v2.3);
  Aden separate episode linked via related_event_ids; reject v2.2.

## Opus audit of the reviews (2026-10-18)

Verdict: **approved with conditions.** Both outside reviews are advice; the orchestrator rules on each point. Headlines of
the audit's findings 1 to 15:

1. No tested primitive reaches the old gates (false-split 10%, false-merge 5%, four zero-tolerance examples) on the seen set;
   the gates were calibrated for a different problem. They are replaced for production by the ratified v2.3 pass rule
   (V23_PREREGISTRATION.md) and kept as regression information only.
2. Gemini's B-cubed gates (precision 0.85, recall 0.80) are lenient: B-cubed on a stream dominated by singletons rewards
   singletons, so they cannot be the primary metric (the all-singletons clustering must not pass).
3. Embeddings are not bitwise-guaranteed across hardware and library builds: vectors are computed once at ingestion and
   stored with the model name, version and sha256; the build reads stored vectors only.
4. Step 2 alone (name folding plus window persistence, on v2/v2.2) is rejected: folding is not a fix.
5. The coverage effect (the cap raising classified share toward 100%) holds for v2 (more chaining) but not for v2_2.
6. Theme is excluded from identity (it is annotation only); there is no hard frequency cutoff (continuous weights instead).
7. Link a report against an event's anchors (its first 3 reports), never the union of its reports.
8. E2 onsets come from `published_at`, not `first_seen_at` (ingest is daily).
9. The two-level model (episode and storyline) is needed; the Aden palace is its own episode under the Houthi storyline.
10. Persistent identity: the 7-day pool is a discovery window, not an event lifetime; no silent retrospective reassignment.
11. The seen reference set is now a development set: its numbers are optimistic and are no longer evidence for a fix.
12. A fresh holdout of a contiguous window, fully labelled by two independent labellers, with story-resampled CIs.
13. Primary metrics at episode level (recall per episode, contamination, largest cluster), B-cubed and pairwise secondary.
14. Single-source candidates are shown, not hidden (supersedes the plan's hidden-by-default for `rejected_uncorroborated`).
15. Stage 1 ships as the candidate register with E2 de-duplication by (instrument, reference close, window); full
    clustering is Stage 2 work if the holdout fails.
