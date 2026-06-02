// Central copy for in-product explanations: short (?) tooltips, per-tab intros,
// and the expanded "How it works" guide. Keeping every string here keeps the
// short tooltip and the deeper guide consistent and editable in one place.
// Layered depth: tooltips are plain-language; the guide/glossary carry detail.

export type TabId =
  | "overview"
  | "macro"
  | "sectors"
  | "news"
  | "combined"
  | "monitoring"
  | "history";

// Short, plain-language definitions shown in the (?) bubble. Keep <= ~160 chars.
export const TOOLTIPS: Record<string, string> = {
  // cross-cutting
  regime:
    "A short name for the current economic 'weather' (e.g. reflation, recession), chosen from the backdrop of growth, inflation, rates and credit.",
  confidence:
    "How clearly the data points to one regime. Low means signals are mixed or weak; high means they agree.",
  reported_vs_raw:
    "Raw leader is the top regime before smoothing. Reported regime is the published label after transition filters reduce day-to-day flip-flop.",
  data_status:
    "Whether the exported file set the dashboard reads is complete or missing pieces.",
  data_source:
    "Exported outputs = real backend run. Sample fixtures = bundled demo data when no run is present.",

  // macro tab
  regime_timeline:
    "Monthly regime label from 1990 to today. Each colored band is one month; faded bands had insufficient data to validate.",
  regime_probabilities:
    "The model's probability for each candidate regime this period. They sum to 1; the highest is the leader.",
  macro_indicators:
    "Each FRED indicator's history as a z-score (standard deviations from its own recent norm), grouped by the regime dimension it feeds. Shows what is actually driving each axis.",
  macro_date: "The date of the latest macro reading the regime is based on.",

  // sectors tab
  confidence_adjusted_score:
    "A sector's diagnostic attractiveness under the current regime, scaled down when macro confidence is low. Research signal only, not advice.",
  sector_ranking:
    "Sectors ordered by their confidence-adjusted score under today's regime. Higher = more supported by the current backdrop.",
  sector_components:
    "The dimension exposures and regime priors that pushed a sector's score up (supporting) or down (opposing).",
  validation:
    "An honest backtest: do the sector scores actually rank-order future sector-ETF returns? Rank IC near 0 and hit-rate near 50% mean no measurable edge.",

  // news tab
  news_themes:
    "Macro themes extracted from news text by the AI classifier, then scored for direction and strength by the backend.",
  retry_rate:
    "Share of news items whose first AI classification failed validation and was retried. High = noisy model output.",
  repair_rate:
    "Share of classifications auto-corrected (e.g. enum/number fixes) before they passed. High = formatting drift.",
  classification_success:
    "Share of news items the AI classified into valid structured output on this run.",
  low_confidence_items:
    "News items the classifier was least sure about. Useful for spotting ambiguous or off-topic articles.",

  // combined tab
  combined_overlay:
    "The sector picture after a bounded news overlay is added to the macro-only scores, so a little news can't overpower the macro backdrop.",
  overlay_rank_change:
    "Largest number of places any sector moved when the news overlay was applied. Big jumps warrant a closer look.",
  news_item_count:
    "How many news items fed the overlay this run. Thin counts make the overlay less reliable.",

  // monitoring tab
  readiness_label:
    "How much operating history exists: from early_history to monitor_ready to validation_candidate. More runs unlock more trust.",
  unmapped_share:
    "Share of news items that did not match any known source group. High = coverage gaps in the news feed.",
  old_item_share:
    "Share of news items older than the freshness window. High = the feed is stale.",
  coverage_warnings:
    "Automated flags when news source coverage is thin, uneven, or stale.",
  guardrail:
    "Pass/fail safety check on the run (e.g. data sanity, rank-change limits) before results are trusted.",
  input_quality:
    "Overall health of the inputs that fed this run.",

  // history tab
  history_readiness:
    "Run-over-run trend cards need >= 2 recorded daily runs. For deep history, use the Regime Timeline on the Macro tab.",
  recorded_runs:
    "Number of daily pipeline runs archived so far. Grows one per successful run once history persistence is live.",
  avg_confidence:
    "Average macro confidence across the recent recorded runs shown here.",
};

// One-sentence "what this tab is for" line, shown under the tab bar.
export const TAB_INTROS: Record<TabId, string> = {
  overview:
    "A one-screen health check: did the run succeed, what is the regime, and what are the top sector, news, and combined signals right now.",
  macro:
    "The macro backdrop: today's regime and confidence, the full 1990-to-present regime timeline, and the probability behind each regime.",
  sectors:
    "How the current regime maps to sectors: the diagnostic ranking and the drivers behind the strongest and weakest sectors. Research only.",
  news:
    "What recent news is saying: macro themes, sector tailwinds and headwinds, and how reliably the AI classified the articles.",
  combined:
    "The macro-only sector view blended with a bounded news overlay, so you can see whether news confirms or nudges the macro picture.",
  monitoring:
    "Data-quality and run-health checks: source coverage, freshness, input quality, guardrails, and how much history has accumulated.",
  history:
    "The record of recent daily runs: did the pipeline run and export, and are there enough runs to read run-over-run trends.",
};

// ---- Expanded guide ("How it works") content -------------------------------

export const DATA_FLOW: string[] = [
  "Collect: FRED economic series (growth, inflation, rates, credit, curve) plus news articles.",
  "Transform: raw series become normalized features, features roll up into dimensions.",
  "Classify: dimensions are scored into a macro regime with a confidence level.",
  "Map: the regime plus sector exposures produce per-sector diagnostic scores.",
  "Interpret: news text is classified by AI into themes and bounded sector overlays.",
  "Publish: the backend exports JSON files; this website only reads and displays them.",
];

export const TAB_GUIDE: { tab: string; read: string }[] = [
  {
    tab: "Overview",
    read: "Start here. Confirm the run succeeded and skim the regime plus the top sector, news, and combined signals.",
  },
  {
    tab: "Macro",
    read: "Read today's regime and confidence, then scan the 1990-to-present timeline for how long the current regime has held.",
  },
  {
    tab: "Sectors",
    read: "See which sectors the current regime supports or pressures, and open the components to see why. Signals are diagnostic, not advice.",
  },
  {
    tab: "News",
    read: "Check which themes are showing up and whether the AI classified articles reliably (low retry/repair rates are healthier).",
  },
  {
    tab: "Combined",
    read: "Compare macro-only sectors with the news-overlay version. Small, bounded changes are expected; large rank jumps deserve scrutiny.",
  },
  {
    tab: "Monitoring",
    read: "Decide whether to trust the run: coverage, freshness, input quality, guardrails, and readiness all live here.",
  },
  {
    tab: "History",
    read: "Verify the pipeline keeps running and exporting, and watch run-over-run trends once enough runs accumulate.",
  },
];

export const GLOSSARY: { term: string; def: string }[] = [
  {
    term: "Regime",
    def: "A broad label for the macro backdrop (e.g. goldilocks, reflation, stagflation, recession, tightening) derived from growth, inflation, policy, credit and the yield curve.",
  },
  {
    term: "Confidence",
    def: "How decisively the data favors one regime over the others. Published scores are scaled down when confidence is low.",
  },
  {
    term: "Reported vs raw regime",
    def: "The raw leader is the highest-probability regime each period. The reported regime applies a transition filter to avoid noisy day-to-day switching.",
  },
  {
    term: "Dimension",
    def: "A grouped macro factor (growth momentum, inflation pressure, policy stance, credit/liquidity, yield curve, and additive ones like monetary liquidity and housing activity).",
  },
  {
    term: "Sector exposure / regime prior",
    def: "Hand-set priors describing how a sector tends to respond to each dimension and regime. They are priors, not measured guarantees.",
  },
  {
    term: "Confidence-adjusted score",
    def: "A sector's combined regime-prior and exposure score, multiplied by a confidence factor. A relative research signal, not a recommendation.",
  },
  {
    term: "News overlay",
    def: "A bounded adjustment to sector scores from classified news, capped so a small amount of news cannot dominate the macro backdrop.",
  },
  {
    term: "Retry / repair rate",
    def: "Quality measures for AI classification: retries are re-asks after a failed parse; repairs are automatic fixes to malformed output.",
  },
  {
    term: "Readiness label",
    def: "How much operating history has accumulated, which gates how much the system trusts its own trends.",
  },
  {
    term: "Guardrail",
    def: "A safety check that must pass before a run's results are treated as reliable.",
  },
];

export const GUIDE_DISCLAIMER =
  "This is a diagnostic research tool. It organizes macro data, sector signals, news themes, and run history. It is not financial advice, not an automated trading system, and not a promise any signal will be right.";
