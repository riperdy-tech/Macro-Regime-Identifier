import type { DashboardData, RankedSector } from "./types";
import {
  asArray,
  combinedRows,
  getNested,
  getObject,
  numberValue,
  prettyRegime,
  prettySectorId,
  scoreItems,
  sectorLabelById,
  sectorRows,
} from "./utils";

// Pure sentence builders. They only read fields the export already carries.
export const HEADLINE_FALLBACK = "Not enough data yet to summarise this tab.";

const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

function spelled(n: number): string {
  return NUMBER_WORDS[n] ?? String(n);
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function macroRegimes(data: DashboardData): { reported: string | null; raw: string | null } {
  const macro = getObject(getObject(data.daily).macro);
  const sectors = getObject(data.sectors);
  return {
    reported: str(macro.reported_regime) ?? str(sectors.reported_macro_regime),
    raw: str(macro.raw_dominant_regime) ?? str(sectors.raw_macro_leader),
  };
}

export function macroHeadline(data: DashboardData): string {
  const { reported, raw } = macroRegimes(data);
  if (!reported) {
    return HEADLINE_FALLBACK;
  }
  if (raw && raw !== reported) {
    return `Raw signal favours ${prettyRegime(raw)}, but the reported state holds ${prettyRegime(reported)}.`;
  }
  return `Reported and raw signals agree on ${prettyRegime(reported)}.`;
}

export function prettySectorLabel(id: string | undefined, labels: Record<string, string>): string {
  return id ? labels[id] ?? prettySectorId(id) : "Unknown";
}

function sectorName(row: RankedSector, labels: Record<string, string>): string {
  return row.label ?? prettySectorLabel(row.sector_id, labels);
}

export function sectorsHeadline(data: DashboardData): string {
  const rows = [...sectorRows(data.sectors)].sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
  const { reported } = macroRegimes(data);
  if (rows.length < 3 || !reported) {
    return HEADLINE_FALLBACK;
  }
  const labels = sectorLabelById(data.sectors);
  return `Under ${prettyRegime(reported)}, ${sectorName(rows[0], labels)} and ${sectorName(rows[1], labels)} score highest; ${sectorName(rows[rows.length - 1], labels)} lowest.`;
}

export function topNewsTheme(data: DashboardData): { id: string; score?: number } | null {
  const positive = scoreItems(getNested(data.newsScores, "top_positive_macro_themes"))[0];
  const fallback = asArray<{ id?: string; score?: number }>(getNested(data.daily, "news", "top_themes"))[0];
  const top = positive ?? fallback;
  return top?.id ? { id: top.id, score: top.score } : null;
}

export function newsHeadline(data: DashboardData): string {
  const top = topNewsTheme(data);
  if (!top) {
    return HEADLINE_FALLBACK;
  }
  const items = numberValue(getNested(data.accumulation, "latest_run", "classified_items"));
  const theme = prettySectorId(top.id);
  return items === null
    ? `${theme} is the strongest theme in the latest news scores.`
    : `${theme} is the strongest theme across ${items.toLocaleString("en-US")} classified items.`;
}

export type RankChange = { sector_id?: string; rank_change?: number; macro_rank?: number; combined_rank?: number };

// rank_change = macro rank - combined rank, so positive means the news moved the sector up.
export function rankChanges(data: DashboardData): RankChange[] {
  const overlay = getObject(getNested(data.monitoring, "overlay_monitoring"));
  return asArray<RankChange>(overlay.sectors_changed_by_news_json)
    .filter((row) => typeof row.rank_change === "number" && row.rank_change !== 0)
    .sort((a, b) => Math.abs(b.rank_change ?? 0) - Math.abs(a.rank_change ?? 0));
}

export function combinedHeadline(data: DashboardData): string {
  if (!combinedRows(data.combined).length) {
    return HEADLINE_FALLBACK;
  }
  const changes = rankChanges(data);
  if (!changes.length) {
    return "The news overlay leaves the macro-only ranking unchanged.";
  }
  const labels = sectorLabelById(data.sectors, data.combined);
  const first = changes[0];
  const move = Math.abs(first.rank_change ?? 0);
  const direction = (first.rank_change ?? 0) > 0 ? "up" : "down";
  const head = `The news overlay moves ${prettySectorLabel(first.sector_id, labels)} ${direction} ${spelled(move)} ${move === 1 ? "place" : "places"}`;
  if (changes.length === 1) {
    return `${head}; no other sector moves.`;
  }
  return `${head}; no other sector moves more than ${spelled(Math.abs(changes[1].rank_change ?? 0))}.`;
}

export function monitoringHeadline(data: DashboardData): string {
  const daily = getObject(data.daily);
  if (!data.daily) {
    return HEADLINE_FALLBACK;
  }
  const label = str(getNested(data.accumulation, "readiness_label"));
  const healthy = daily.status === "success" && asArray(daily.errors).length === 0;
  const history =
    label === "validation_candidate"
      ? "News history is long enough to plan validation."
      : label === "monitor_ready"
        ? "News history is long enough for monitoring."
        : "News history is still too short for validation.";
  return `${healthy ? "Pipeline healthy." : "Pipeline needs attention."} ${history}`;
}

export function historyHeadline(data: DashboardData): string {
  const runs = asArray<{ run_mode?: string }>(getObject(data.history).runs);
  if (!runs.length) {
    return HEADLINE_FALLBACK;
  }
  const replay = runs.filter((run) => run.run_mode === "replay").length;
  return `${runs.length - replay} live and ${replay} replay runs recorded.`;
}

export function overviewSubcopy(data: DashboardData, unchangedMonths: number | null): string | null {
  const macro = getObject(getObject(data.daily).macro);
  const confidence = numberValue(macro.confidence) ?? numberValue(getObject(data.sectors).macro_confidence);
  if (confidence === null) {
    return null;
  }
  const base = `Reported confidence ${Math.round(confidence * 100)}%`;
  if (unchangedMonths === null || unchangedMonths < 1) {
    return `${base}.`;
  }
  return `${base}, regime unchanged for ${unchangedMonths} ${unchangedMonths === 1 ? "month" : "months"}.`;
}
