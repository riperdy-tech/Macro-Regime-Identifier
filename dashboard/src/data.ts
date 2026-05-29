import type { DashboardData, Manifest } from "./types";

const FILES = {
  daily: "daily_diagnostic_summary.json",
  sectors: "current_sector_ranking.json",
  newsScores: "news_score_report.json",
  combined: "combined_sector_diagnostic.json",
  monitoring: "news_monitoring_report.json",
  accumulation: "news_accumulation_report.json",
  coverage: "news_source_coverage_report.json",
  history: "history_index.json",
  timeline: "regime_timeline.json",
} as const;

const BASE_URL = import.meta.env.BASE_URL;

export async function loadDashboardData(): Promise<DashboardData> {
  const exported = await loadFromBase("data");
  if (exported.manifest) {
    return { ...exported, source: "exported" };
  }
  const sample = await loadFromBase("sample-data");
  if (sample.manifest) {
    return { ...sample, source: "sample" };
  }
  return {
    manifest: null,
    daily: null,
    sectors: null,
    newsScores: null,
    combined: null,
    monitoring: null,
    accumulation: null,
    coverage: null,
    history: null,
    timeline: null,
    source: "empty",
  };
}

async function loadFromBase(base: string): Promise<Omit<DashboardData, "source">> {
  const manifest = await fetchJson<Manifest>(dataPath(base, "manifest.json"));
  if (!manifest) {
    return emptyData();
  }
  const entries: [string, unknown][] = await Promise.all(
    Object.entries(FILES).map(async ([key, filename]) => [
      key,
      await fetchJson(dataPath(base, filename)),
    ]),
  );
  return {
    manifest,
    daily: valueFor(entries, "daily"),
    sectors: valueFor(entries, "sectors"),
    newsScores: valueFor(entries, "newsScores"),
    combined: valueFor(entries, "combined"),
    monitoring: valueFor(entries, "monitoring"),
    accumulation: valueFor(entries, "accumulation"),
    coverage: valueFor(entries, "coverage"),
    history: valueFor(entries, "history"),
    timeline: valueFor(entries, "timeline"),
  };
}

function dataPath(base: string, filename: string): string {
  const normalizedBaseUrl = BASE_URL.endsWith("/") ? BASE_URL : `${BASE_URL}/`;
  return `${normalizedBaseUrl}${base}/${filename}`;
}

async function fetchJson<T = Record<string, unknown>>(url: string): Promise<T | null> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

function valueFor(entries: [string, unknown][], key: string): Record<string, unknown> | null {
  const found = entries.find(([entryKey]) => entryKey === key)?.[1];
  return found && typeof found === "object" ? (found as Record<string, unknown>) : null;
}

function emptyData(): Omit<DashboardData, "source"> {
  return {
    manifest: null,
    daily: null,
    sectors: null,
    newsScores: null,
    combined: null,
    monitoring: null,
    accumulation: null,
    coverage: null,
    history: null,
    timeline: null,
  };
}
