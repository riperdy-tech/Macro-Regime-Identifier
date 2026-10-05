import type { HistoryRun, MacroDimensionSeries, RankedSector, ScoredItem } from "./types";

export function text(value: unknown, fallback = "Data unavailable"): string {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

export function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function formatPct(value: unknown): string {
  const numeric = numberValue(value);
  if (numeric === null) {
    return "n/a";
  }
  return `${(numeric * 100).toFixed(1)}%`;
}

export function formatScore(value: unknown): string {
  const numeric = numberValue(value);
  if (numeric === null) {
    return "n/a";
  }
  return numeric.toFixed(2);
}

export function formatCount(value: unknown): string {
  const numeric = numberValue(value);
  if (numeric === null) {
    return "n/a";
  }
  return Math.round(numeric).toLocaleString("en-US");
}

export function formatSigned(value: unknown, digits = 0): string {
  const numeric = numberValue(value);
  if (numeric === null) {
    return "n/a";
  }
  const formatted = Math.abs(numeric).toFixed(digits);
  if (numeric > 0) {
    return `+${formatted}`;
  }
  if (numeric < 0) {
    return `−${formatted}`;
  }
  return digits === 0 ? "0" : numeric.toFixed(digits);
}

const TAIWAN_TIME_ZONE = "Asia/Taipei";

function formatTaiwanDateParts(date: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: TAIWAN_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

function timestampFromRunId(runId: unknown): Date | null {
  if (typeof runId !== "string") {
    return null;
  }
  const match = runId.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
  if (!match) {
    return null;
  }
  const [, year, month, day, hour, minute, second] = match;
  const date = new Date(`${year}-${month}-${day}T${hour}:${minute}:${second}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

// Render timestamps as Taiwan calendar dates only. This intentionally omits
// HH:MM so runs near UTC midnight read naturally for Taiwan users.
export function formatStamp(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Data unavailable";
  }
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) {
    return String(value);
  }
  return formatTaiwanDateParts(d);
}

export function formatRunDate(runDate: unknown, runId?: unknown): string {
  const timestamp = timestampFromRunId(runId);
  if (timestamp) {
    return formatTaiwanDateParts(timestamp);
  }
  return formatStamp(runDate);
}

export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function getObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function getNested(root: unknown, ...keys: string[]): unknown {
  let value = root;
  for (const key of keys) {
    value = getObject(value)[key];
  }
  return value;
}

export function sectorRows(payload: Record<string, unknown> | null): RankedSector[] {
  if (!payload) {
    return [];
  }
  return asArray<RankedSector>(payload.sector_ranking ?? payload.ranking);
}

export function combinedRows(payload: Record<string, unknown> | null): RankedSector[] {
  if (!payload) {
    return [];
  }
  return asArray<RankedSector>(
    payload.combined_experimental_ranking ?? payload.combined_ranking ?? payload.ranking,
  );
}

export function scoreItems(value: unknown): ScoredItem[] {
  return asArray<ScoredItem>(value);
}

export function sectorLabelById(
  ...payloads: (Record<string, unknown> | null)[]
): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const payload of payloads) {
    for (const row of sectorRows(payload)) {
      if (row.sector_id && row.label) {
        labels[row.sector_id] = row.label;
      }
    }
  }
  return labels;
}

// Names the exports carry no label for. Sector names match config/sectors.yaml.
const NAME_OVERRIDES: Record<string, string> = {
  oil_gas_ep: "Oil & Gas E&P",
  software: "Software & SaaS",
  biotech: "Biotechnology",
};

const ACRONYMS = new Set(["ai", "bbc", "bls", "cnbc", "dol", "eia", "gdelt", "rss", "scmp", "us"]);

export function prettySectorId(sectorId: string): string {
  return (
    NAME_OVERRIDES[sectorId] ??
    sectorId
      .split("_")
      .map((word) =>
        ACRONYMS.has(word) ? word.toUpperCase() : word ? word.charAt(0).toUpperCase() + word.slice(1) : word,
      )
      .join(" ")
  );
}

export function historyRuns(payload: Record<string, unknown> | null): HistoryRun[] {
  if (!payload) {
    return [];
  }
  return asArray<HistoryRun>(payload.runs);
}

export function prettyRegime(regime?: string | null): string {
  return regime ? prettySectorId(regime) : "Data unavailable";
}

const DIMENSION_LABELS: Record<string, string> = {
  growth_momentum: "Growth",
  inflation_pressure: "Inflation",
  labor_market: "Labor",
  policy_stance: "Policy",
  credit_liquidity: "Financial conditions",
  yield_curve: "Yield curve",
};

export function dimensionLabel(id: string): string {
  return DIMENSION_LABELS[id] ?? prettySectorId(id);
}

export type DimensionSeries = { id: string; label: string; points: { date: string; value: number }[] };

// Mean of the exported indicator z-scores per month, per dimension. A display
// summary of macro_features_timeline.json, not the engine's own dimension score.
export function dimensionSeries(payload: Record<string, unknown> | null): DimensionSeries[] {
  const out: DimensionSeries[] = [];
  for (const dim of asArray<MacroDimensionSeries>(getObject(payload).dimensions)) {
    const id = dim.dimension_id ?? "";
    if (!id || id === "unmapped") {
      continue;
    }
    const sums = new Map<string, { total: number; count: number }>();
    for (const feature of dim.features ?? []) {
      for (const point of feature.points ?? []) {
        if (point.date && typeof point.value === "number" && Number.isFinite(point.value)) {
          const cell = sums.get(point.date) ?? { total: 0, count: 0 };
          cell.total += point.value;
          cell.count += 1;
          sums.set(point.date, cell);
        }
      }
    }
    const points = [...sums.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, cell]) => ({ date, value: cell.total / cell.count }));
    if (points.length) {
      out.push({ id, label: dimensionLabel(id), points });
    }
  }
  return out;
}
