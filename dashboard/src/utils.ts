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

export function historyRuns(payload: Record<string, unknown> | null): HistoryRun[] {
  if (!payload) {
    return [];
  }
  return asArray<HistoryRun>(payload.runs);
}
