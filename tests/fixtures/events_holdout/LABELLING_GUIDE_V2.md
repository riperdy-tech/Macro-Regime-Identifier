# Labelling guide v2 (episode and storyline), for the v2.3 holdout

Status: written 2026-10-18 for the pre-registration in `docs/events_build/V23_PREREGISTRATION.md`. It is frozen by commit
hash with the rest of the protocol before any holdout item is labelled. It supersedes the single "event" label of
`tests/fixtures/events_reference/LABELLING.md` (v1.1) for the holdout; v1.1 and `labels.yaml` of the reference set are
NOT edited. The worked examples below come from the reference set, which is now a **development set**: it informed this
guide, so nothing scored on it is evidence.

## Who labels, and from what

- **Two labellers, independently.** Each works alone, from this guide and, for every item, only its **title, publisher
  and published date** (the columns of the sampler's `items.csv`). No classifier output, theme, entity, summary, cluster,
  builder or linker output is read, run or consulted. Neither sees the other's labels until both are filed.
- **Every item in the window is labelled**, including the ones that look like noise. Nothing is selected by keyword.
- **Adjudication.** Where the two labellers' `episode_id` or `storyline_id` partitions disagree for an item, a **third
  pass** decides it using only this guide (and the same three columns). Each adjudicated item is logged with both
  original labels, the rule applied and the decision. The adjudicated file is the truth the scorer reads; the
  inter-labeller B-cubed between the two original files is reported as the ceiling on any clustering.
- Labels are not changed after a build has been scored. A later change is a new label version with a recorded reason.

## The two labels

**Episode** (`episode_id`): one specific incident or discrete development, with its own onset: who did what to whom,
where and when, or one named scheduled release, together with the consequences and reactions that the headline itself
attributes to it (casualties, suspensions, advisories, a named market or oil move, an official response to it).

**Storyline** (`storyline_id`): the continuing campaign, dispute or theme that groups episodes with the same principal
actor and a causal thread. Every episode belongs to exactly one storyline; a storyline may hold one episode.

Reserved values: `NONE` for real news that no other item belongs with (episode and storyline are both `NONE`, and the
item is its own one-item episode and storyline); `NOISE` for non-news (advice columns, market previews and open
reports, earnings-call transcripts, roundups, promotions), the same way. Any other id is `EP_<SHORT>` / `SL_<SHORT>`.

### When two items share an episode

Share an episode when the headlines show they report **the same incident**, or one reports a consequence or reaction that
the other incident's headline-level facts attribute to it. Not enough: the same country, company, person, commodity or
topic; a feature or analysis that does not name the incident; a roundup.

Start a **new episode** inside the same storyline when any of these holds, judged from the headline:

1. a **new action with its own onset**: a new strike, a new filing, a new release, a new decision, or a headline that
   itself says "second", "renewed", "new";
2. a **different target class or place** (an airport, then an oil headquarters; Saudi Arabia, then the Yemeni
   government in Aden);
3. a **different actor responding** with its own action (a coalition strike on a fuel depot after an attack);
4. a **quiet gap of more than 72 hours** between the items on the same target class.

Repeated strikes on the same target class by the same actor with less than 72 hours between consecutive reports are
**one strike wave = one episode**. If the headlines do not establish that two items are the same incident, label them
as **different episodes** and put `uncertain` in `notes`; do not guess a merge.

## Core, peripheral, and the hard cases

`membership` is absent (core) or `peripheral`. Peripheral items are excluded from every metric: a clustering may put
them in the episode or leave them out and neither counts as an error, and they are never outsiders in another cluster.

- **core**: the headline itself reports the incident, or a consequence or reaction it attributes to it, as its main
  claim.
- **peripheral**: the headline (a) names the incident only in a clause beside another story, (b) is commentary, a
  newsletter, an explainer or a feature, (c) plainly belongs to the story but does not name the incident or its actor,
  or (d) joins two episodes in one headline.
- **Ambiguous wrap-up or roundup** that spans several episodes: label it with the episode it leads with, as
  `peripheral`. Do not give it its own episode and do not use it to join two episodes.
- **Commentary and opinion** (Breakingviews, columns, "how are the Houthis...") are peripheral to the episode whose
  onset they follow most closely, in the same storyline.
- **Previews** published before a scheduled release are peripheral to the release episode (they forecast, they do not
  report). **Reaction** headlines are core when they name the release or incident and peripheral when they do not.
- **Syndications** (`report_label`): items that are the same report republished (identical headline ignoring the
  " - Publisher" suffix, or the same article retitled) share a `report_label`, and therefore the same `episode_id`.
  Outlets that rewrote the same fact are different reports. A singleton gets `r_<first 6 hex of news_id after news_>`.

## Worked examples (from the reference set, titles and dates as published)

**Houthi / Saudi storyline, `SL_HOUTHI_SAUDI`.** One storyline, several episodes:

- `EP_RIYADH_AIRPORT_STRIKES` (10-08 to 10-12). "Houthi strikes on Saudi airports leave 3 dead" (10-08); "Plane damaged
  at Riyadh airport as Houthis escalate attacks" (10-08); "Pilot killed in attacks ... on Riyadh airport" (10-09);
  "Houthi attack on Saudi airport kills 12" (10-10); "One killed ... in second Houthi strike on Riyadh airport in two
  days" (10-11). Consecutive reports are less than 72 hours apart on the same target class, so this is one wave.
  Core consequences the headline attributes to it: "Lufthansa, Indian airlines suspend flights to Riyadh after Houthi
  attacks", "EU aviation body widens Saudi airspace warning after Houthi strikes", "Most Gulf stocks in red after
  attack on Riyadh airport", "Delta delays debut of Saudi Arabia service ... after Riyadh airport attacks", "Oil Prices
  Jump 2% on Saudi Attacks". Peripheral: "Airport attacks put aviation in crosshairs of geopolitical conflicts" (a
  feature, two copies sharing one `report_label`); "Breakingviews - COMMENTARY: Riyadh attacks hit core of Saudi's
  economic pivot"; "Delta CEO: 'Safety is going to make the call' on launching Saudi Arabia flight" (does not name the
  attacks).
- `EP_ARAMCO_HQ_STRIKE` (10-13 to 10-14). "Saudi Aramco administrative building attacked, limited fire reported";
  "Houthis Vow More Attacks After Strike on Aramco Headquarters"; "Yemen's Houthis vow more attacks on Saudi airports,
  oil facilities after Aramco HQ hit". New target class (oil headquarters), own onset, so a new episode although it
  follows the airport wave within 72 hours. "Loud booms heard near Saudi Aramco headquarters in Dhahran" names no
  attack: peripheral.
- `EP_COALITION_RESPONSE` (10-12 to 10-13), a different actor's own action: "Missiles intercepted near Riyadh after Saudi
  coalition hits Yemen fuel depot".
- `EP_ADEN_PALACE_STRIKE` (10-15). "Houthis attack presidential palace complex in Yemen's Aden, PM unharmed"; "Houthi
  missile attack on Yemeni presidential palace kills five". A different target (the Yemeni government, inside Yemen),
  its own onset: its own episode under `SL_HOUTHI_SAUDI`, **not** merged into the airport or Aramco episodes. "Yemen's
  Houthis hit palace in Aden, coalition details attacks on Saudi infrastructure" joins two episodes: peripheral.

**US inflation data, `SL_US_INFLATION_SEP`.** `EP_CPI_SEP_RELEASE` (10-14): "Consumer prices rose 3.7% annually in
September as gas prices skyrocket"; "US inflation rises more than expected"; "US inflation heats up in September; Fed
rate hike this month back on table" (the rate-hike odds the headline attributes to the release are core). The **preview**
("The latest inflation data is expected to show price increases are accelerating", 10-13) is peripheral. "US stocks drop
to their worst day since July as inflation worsens" names no release: peripheral. `EP_PPI_SEP_RELEASE` (10-15, "Wholesale
prices jumped 6.3% annually in September") is a different release: its own episode, same storyline.

**CoreWeave, `SL_COREWEAVE_AI_DEBT`.** `EP_COREWEAVE_BOND_SELLOFF` (10-14): "CoreWeave shares tumble as bond yields near
15%"; "CoreWeave's AI bonds tumble". `EP_COREWEAVE_CDS_RECORD` (10-15): "CoreWeave credit default swaps hit distress
levels"; "CoreWeave's Debt Insurance Costs Just Hit a Record". Two developments with their own onsets under one
storyline. "CoreWeave's polarizing stock rebounds as Wall Street squares off about AI spending" names neither: peripheral.
A headline that names CoreWeave but reports the OpenAI revenue story is `NONE`.

These are illustrations of the rules, made with the seen items in view. The labellers of the holdout do not see them
as answers: a holdout item is judged by the rules, not by resemblance to an example.

## Label file schema

One YAML file per labeller, `items:` keyed by the `news_id` of the sampler's `items.csv`. Every item has an entry.

```yaml
items:
  news_f4545cd7fe5a0b22:
    episode_id: NONE              # NONE | NOISE | EP_<SHORT>
    storyline_id: NONE            # NONE | NOISE | SL_<SHORT>; equal to episode_id when that is NONE or NOISE
    report_label: r_f4545c        # shared by syndicated copies; singleton: r_<first 6 hex>
    notes: ""                     # free text; write `uncertain` where the headlines did not settle it
  news_0e2b17abcdef0123:
    episode_id: EP_RIYADH_AIRPORT_STRIKES
    storyline_id: SL_HOUTHI_SAUDI
    report_label: r_0e2b17
    membership: peripheral        # omit for core
    notes: commentary on the strikes
```

Constraints the scorer checks (`score_holdout.validate_labels`): every item is labelled; the three required fields are
present; an episode sits in exactly one storyline; a `NONE`/`NOISE` episode has the same storyline value; items sharing a
`report_label` share an `episode_id`; `membership` is `core` or `peripheral` if present.
