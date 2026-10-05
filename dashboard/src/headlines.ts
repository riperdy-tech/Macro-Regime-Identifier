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

export type OverlayMove = { id: string; from: number; to: number; thin: boolean };

// Macro-only rank vs combined rank, both over the same universe. The macro-only
// rank is the rank of each row's own sector_macro_score, because the exported
// macro ranking numbers the parent sectors and the sub-industries separately and
// so cannot be compared with the combined rank. A sector is "thin" when the
// overlay gives its news no weight, so it carries its macro-only score.
export function overlayMoves(data: DashboardData): OverlayMove[] {
  const rows = combinedRows(data.combined);
  if (!rows.length || rows.some((r) => !r.sector_id || typeof r.rank !== "number" || typeof r.sector_macro_score !== "number")) {
    return [];
  }
  const macroOrder = [...rows].sort((a, b) => (b.sector_macro_score ?? 0) - (a.sector_macro_score ?? 0));
  const macroRank = new Map(macroOrder.map((r, i) => [r.sector_id, i + 1]));
  return rows.map((r) => ({
    id: r.sector_id as string,
    from: macroRank.get(r.sector_id) as number,
    to: r.rank as number,
    thin: (r.news_component_weight ?? 0) === 0,
  }));
}

export function combinedHeadline(data: DashboardData): string {
  const moves = overlayMoves(data);
  if (!moves.length) {
    return HEADLINE_FALLBACK;
  }
  const changed = moves
    .filter((m) => m.from !== m.to)
    .sort((a, b) => Math.abs(b.from - b.to) - Math.abs(a.from - a.to));
  if (!changed.length) {
    return "The news overlay leaves the macro-only ranking unchanged.";
  }
  const labels = sectorLabelById(data.sectors, data.combined);
  const first = changed[0];
  const move = Math.abs(first.from - first.to);
  const direction = first.from > first.to ? "up" : "down";
  const head = `The news overlay moves ${prettySectorLabel(first.id, labels)} ${direction} ${spelled(move)} ${move === 1 ? "place" : "places"}`;
  if (changed.length === 1) {
    return `${head}; no other sector moves.`;
  }
  return `${head}; no other sector moves more than ${spelled(Math.abs(changed[1].from - changed[1].to))}.`;
}

export function monitoringHeadline(data: DashboardData): string {
  const daily = getObject(data.daily);
  if (!data.daily) {
    return HEADLINE_FALLBACK;
  }
  const label = str(getNested(data.accumulation, "readiness_label"));
  const failed = asArray(daily.errors).length > 0 || !String(daily.status ?? "").startsWith("success");
  const lead = failed ? "Pipeline needs attention." : daily.status === "success" ? "Pipeline healthy." : "Pipeline ran with warnings.";
  const history =
    label === "validation_candidate"
      ? "News history is long enough to plan validation."
      : label === "monitor_ready"
        ? "News history is long enough for monitoring."
        : "News history is still too short for validation.";
  return `${lead} ${history}`;
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
