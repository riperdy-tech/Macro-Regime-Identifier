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

export type DashboardData = {
  manifest: Manifest | null;
  daily: Record<string, unknown> | null;
  sectors: Record<string, unknown> | null;
  newsScores: Record<string, unknown> | null;
  combined: Record<string, unknown> | null;
  monitoring: Record<string, unknown> | null;
  accumulation: Record<string, unknown> | null;
  coverage: Record<string, unknown> | null;
  source: "exported" | "sample" | "empty";
};
