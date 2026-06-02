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
  validation: Record<string, unknown> | null;
  source: "exported" | "sample" | "empty";
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
  observation_count?: number;
  rank_ic_spearman?: number | null;
  top_minus_bottom_spread?: number | null;
  hit_rate_top_positive?: number | null;
};
