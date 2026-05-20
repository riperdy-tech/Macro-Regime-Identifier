import type { DashboardData, Manifest } from "./types";

const FILES = {
  daily: "daily_diagnostic_summary.json",
  sectors: "current_sector_ranking.json",
  newsScores: "news_score_report.json",
  combined: "combined_sector_diagnostic.json",
  monitoring: "news_monitoring_report.json",
  accumulation: "news_accumulation_report.json",
  coverage: "news_source_coverage_report.json",
} as const;

export async function loadDashboardData(): Promise<DashboardData> {
  const exported = await loadFromBase("/data");
  if (exported.manifest) {
    return { ...exported, source: "exported" };
  }
  const sample = await loadFromBase("/sample-data");
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
    source: "empty",
  };
}

async function loadFromBase(base: string): Promise<Omit<DashboardData, "source">> {
  const manifest = await fetchJson<Manifest>(`${base}/manifest.json`);
  if (!manifest) {
    return emptyData();
  }
  const entries: [string, unknown][] = await Promise.all(
    Object.entries(FILES).map(async ([key, filename]) => [
      key,
      await fetchJson(`${base}/${filename}`),
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
  };
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
  };
}
