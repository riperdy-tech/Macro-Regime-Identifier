export type Manifest = {
  generated_at?: string;
  available_files?: string[];
  missing_files?: string[];
  latest_run_date?: string | null;
  latest_macro_date?: string | null;
  latest_news_score_date?: string | null;
  data_status?: "complete" | "partial" | "missing" | string;
};

export type RankedSector = {
  rank?: number;
  sector_id?: string;
  label?: string;
  raw_sector_score?: number;
  confidence_adjusted_score?: number;
  combined_score?: number;
  sector_macro_score?: number;
  sector_news_score?: number;
  news_item_count?: number;
  news_component_weight?: number;
  proxy_ticker?: string;
  top_supporting_components?: Record<string, unknown>[];
  top_opposing_components?: Record<string, unknown>[];
};

export type ScoredItem = {
  id?: string;
  sector_id?: string;
  theme_id?: string;
  score?: number;
  item_count?: number;
  avg_confidence?: number;
};

export type HistoryRun = {
  run_id?: string;
  run_date?: string;
  run_mode?: string;
  replay_date?: string;
  status?: string;
  archive_path?: string;
  macro_regime?: string;
  macro_confidence?: number;
  top_combined_sectors?: string[];
  readiness_label?: string | null;
  guardrail_status?: string;
  classification_success_rate?: number;
  max_overlay_rank_change?: number;
  warning_count?: number;
  error_count?: number;
};

export type RegimeTimelinePoint = {
  date?: string;
  reported_regime?: string | null;
  raw_dominant_regime?: string | null;
  confidence?: number | null;
  valid?: boolean | null;
  probabilities?: Record<string, number>;
};

export type DashboardData = {
  manifest: Manifest | null;
  daily: Record<string, unknown> | null;
  sectors: Record<string, unknown> | null;
  newsScores: Record<string, unknown> | null;
  combined: Record<string, unknown> | null;
  monitoring: Record<string, unknown> | null;
  accumulation: Record<string, unknown> | null;
  coverage: Record<string, unknown> | null;
  history: Record<string, unknown> | null;
  timeline: Record<string, unknown> | null;
  macroFeatures: Record<string, unknown> | null;
  newsSources: Record<string, unknown> | null;
  validation: Record<string, unknown> | null;
  nberBenchmark: Record<string, unknown> | null;
  riskState: RiskState | null;
  dataHealth: DataHealth | null;
  source: "exported" | "sample" | "empty";
};

// outputs/risk_state.json (macro_engine.risk_state). A part the run could not build is null
// and named in `reasons`.
export type RiskState = {
  asof?: string | null;
  built_at?: string;
  status?: string;
  consumers?: string[];
  dial?: { state?: "full" | "half" | null; rule?: string; since?: string | null };
  month_decision?: {
    month?: string | null;
    dial?: "full" | "half" | null;
    decided_on?: string | null;
    spy_close?: number;
    spy_mean_200?: number;
    reason?: string;
  };
  price_rule?: { spy_close?: number; mean_200?: number; below?: boolean; closes_used?: number } | null;
  macro_reading?: {
    month?: string;
    recession_plus_stagflation?: number;
    top_third_threshold?: number | null;
    in_top_third?: boolean | null;
  } | null;
  vix_alarm?: { active?: boolean | null };
  evidence?: { prereg?: string; verdict?: string; n_months?: number };
  degraded?: boolean;
  reasons?: string[];
};

// data_health.json (macro_engine.dashboard_export.build_data_health). null = not reported.
export type DataHealthInput = {
  feature_id?: string;
  series_id?: string | null;
  observation_date?: string | null;
  age_days?: number | null;
  used?: boolean | null;
  reason?: string | null;
  extended_by_fallback?: boolean | null;
  fallback_months?: number | null;
};
export type DataHealthGroup = {
  group?: string;
  oldest_input_date?: string | null;
  oldest_input_age_days?: number | null;
  input_count?: number;
  carried_forward_count?: number;
  not_used_count?: number;
  inputs?: DataHealthInput[];
};
export type DataHealth = {
  reading_date?: string | null;
  input_groups?: DataHealthGroup[] | null;
  input_groups_reason?: string | null;
  carried_forward?:
    | { group?: string; feature_id?: string; series_id?: string | null; observation_date?: string | null; age_days?: number | null }[]
    | null;
  daily?: {
    run_date?: string | null;
    status?: string | null;
    warning_count?: number | null;
    warnings?: { warning?: string; count?: number }[] | null;
    errors?: string[] | null;
    non_fatal_failures?: string[] | null;
    feature_freshness?: { gap_days?: number | null; stale?: boolean | null } | null;
  } | null;
  sector_prices?: {
    last_price_date?: string | null;
    age_days?: number | null;
    fresh?: boolean | null;
    fresh_within_days?: number;
    market_observed?: boolean | null;
    reasons?: string[] | null;
  } | null;
};

export type NewsSourceGroup = {
  group?: string;
  sources?: { name?: string; url?: string }[];
};

export type MacroFeaturePoint = { date?: string; value?: number };
export type MacroFeatureSeries = {
  feature_id?: string;
  series_id?: string;
  points?: MacroFeaturePoint[];
};
export type MacroDimensionSeries = {
  dimension_id?: string;
  features?: MacroFeatureSeries[];
};

export type ValidationSummaryRow = {
  horizon?: string;
  cross_section?: string;
  observation_count?: number;
  rank_ic_spearman?: number | null;
  top_minus_bottom_spread?: number | null;
  hit_rate_top_positive?: number | null;
};
