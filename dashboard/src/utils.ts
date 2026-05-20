import type { HistoryRun, RankedSector, ScoredItem } from "./types";

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
  return numeric.toFixed(3);
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

export function historyRuns(payload: Record<string, unknown> | null): HistoryRun[] {
  if (!payload) {
    return [];
  }
  return asArray<HistoryRun>(payload.runs);
}
