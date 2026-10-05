import { useEffect, useMemo, useState } from "react";
import pkg from "../package.json";
import { loadDashboardData } from "./data";
import {
  DivergingBars,
  MiniLine,
  ProbabilityBar,
  ProbabilityHistory,
  QuadrantMap,
  quadrantOf,
  RegimeRibbon,
  SlopeChart,
  regimeColor,
  type SlopeRow,
} from "./charts";
import {
  combinedHeadline,
  historyHeadline,
  macroHeadline,
  macroRegimes,
  monitoringHeadline,
  newsHeadline,
  overlayMoves,
  overviewSubcopy,
  prettySectorLabel,
  sectorsHeadline,
} from "./headlines";
import type {
  DashboardData,
  HistoryRun,
  MacroDimensionSeries,
  NewsSourceGroup,
  RankedSector,
  RegimeTimelinePoint,
  ValidationSummaryRow,
} from "./types";
import {
  asArray,
  combinedRows,
  dimensionLabel,
  dimensionSeries,
  formatCount,
  formatPct,
  formatRunDate,
  formatScore,
  formatSigned,
  formatStamp,
  getNested,
  getObject,
  historyRuns,
  numberValue,
  prettyRegime,
  prettySectorId,
  scoreItems,
  sectorLabelById,
  sectorRows,
  text,
} from "./utils";
import {
  DATA_FLOW,
  GLOSSARY,
  GUIDE_DISCLAIMER,
  PIPELINE_DETAIL,
  TAB_GUIDE,
  TAB_INTROS,
  TOOLTIPS,
} from "./help";

type TabId = "overview" | "macro" | "sectors" | "news" | "combined" | "monitoring" | "history";
type Theme = "light" | "dark";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "macro", label: "Macro" },
  { id: "sectors", label: "Sectors" },
  { id: "news", label: "News" },
  { id: "combined", label: "Combined" },
  { id: "monitoring", label: "Monitoring" },
  { id: "history", label: "History" },
];

const NOT_AVAILABLE = "Not available in this export";

function tabFromHash(): TabId {
  const id = window.location.hash.replace("#", "");
  return TABS.find((tab) => tab.id === id)?.id ?? "overview";
}

function initialTheme(): Theme {
  try {
    const stored = window.localStorage.getItem("dashboard-theme");
    if (stored === "light" || stored === "dark") {
      return stored;
    }
  } catch {
    // storage unavailable: fall through to the system preference
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>(tabFromHash);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData().then((loaded) => {
      setData(loaded);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem("dashboard-theme", theme);
    } catch {
      // storage unavailable: the choice just won't persist
    }
  }, [theme]);

  // The guide modal uses in-page #anchors; only tab ids drive navigation.
  useEffect(() => {
    const onHash = () => {
      const id = window.location.hash.replace("#", "");
      const match = TABS.find((tab) => tab.id === id);
      if (match) {
        setActiveTab(match.id);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const selectTab = (id: TabId) => {
    setActiveTab(id);
    window.history.replaceState(null, "", `#${id}`);
  };

  const shell = {
    tab: activeTab,
    onTab: selectTab,
    theme,
    onTheme: () => setTheme(theme === "dark" ? "light" : "dark"),
    runDate: data?.manifest?.latest_run_date ?? null,
  };

  if (loading) {
    return (
      <Shell {...shell}>
        <div className="skeleton" aria-busy="true" aria-label="Loading dashboard data" />
      </Shell>
    );
  }

  if (!data || data.source === "empty") {
    return (
      <Shell {...shell}>
        <section className="card empty-state">
          <h2>Dashboard data unavailable</h2>
          <p>Run the backend daily pipeline and export dashboard data, then refresh this page.</p>
          <code>python -m macro_engine.cli export-dashboard-data</code>
        </section>
      </Shell>
    );
  }

  return (
    <Shell {...shell}>
      {data.source === "sample" ? (
        <p className="banner">Showing bundled sample data. No exported run was found.</p>
      ) : null}
      {activeTab === "overview" && <Overview data={data} />}
      {activeTab === "macro" && <MacroPanel data={data} />}
      {activeTab === "sectors" && <SectorPanel data={data} />}
      {activeTab === "news" && <NewsPanel data={data} />}
      {activeTab === "combined" && <CombinedPanel data={data} />}
      {activeTab === "monitoring" && <MonitoringPanel data={data} />}
      {activeTab === "history" && <HistoryPanel data={data} />}
    </Shell>
  );
}

function Shell({
  children,
  tab,
  onTab,
  theme,
  onTheme,
  runDate,
}: {
  children: React.ReactNode;
  tab: TabId;
  onTab: (id: TabId) => void;
  theme: Theme;
  onTheme: () => void;
  runDate: string | null;
}) {
  const [showSummary, setShowSummary] = useState(false);
  return (
    <div className="shell">
      <nav className="rail" aria-label="Dashboard sections">
        <div className="rail-brand">
          Macro Regime{" "}
          <br />
          Engine
        </div>
        <ul className="rail-nav">
          {TABS.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={item.id === tab ? "rail-item active" : "rail-item"}
                aria-current={item.id === tab ? "page" : undefined}
                onClick={() => onTab(item.id)}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
        <div className="rail-foot">
          <button type="button" className="rail-link" onClick={() => setShowSummary(true)}>
            Methodology
          </button>
          <button type="button" className="rail-link" onClick={onTheme} aria-label="Toggle light and dark mode">
            ◐ {theme}
          </button>
          <span className="label">
            v{pkg.version}
            {runDate ? ` · ${runDate}` : ""}
          </span>
        </div>
      </nav>
      <main className="main">
        <div className="main-inner">{children}</div>
      </main>
      {showSummary ? <ProgramSummary onClose={() => setShowSummary(false)} /> : null}
    </div>
  );
}

// ---------- Shared layout pieces ----------

function Head({
  tab,
  eyebrow,
  headline,
  tags,
  large = false,
  children,
}: {
  tab: TabId;
  eyebrow: string;
  headline?: React.ReactNode;
  tags?: React.ReactNode;
  large?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <header className="head">
      <div className="head-row">
        <span className="label">
          {eyebrow}
          <InfoTip text={TAB_INTROS[tab]} />
        </span>
        <span className="head-tags">{tags}</span>
      </div>
      <h1 className={large ? "headline large" : "headline"}>{headline}</h1>
      {children}
    </header>
  );
}

function Tag({ children, tone }: { children: React.ReactNode; tone?: "warn" }) {
  return <span className={tone === "warn" ? "tag warn" : "tag"}>{children}</span>;
}

function Card({
  title,
  right,
  info,
  flush = false,
  children,
}: {
  title?: string;
  right?: React.ReactNode;
  info?: string;
  flush?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={flush ? "card flush" : "card"}>
      {title || right ? (
        <div className="card-head">
          <h2 className="label">
            {title}
            {info ? <InfoTip text={info} /> : null}
          </h2>
          {right}
        </div>
      ) : null}
      {children}
    </section>
  );
}

function Stats({ items }: { items: { label: string; value: string; detail?: string }[] }) {
  return (
    <div className="stats" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
      {items.map((item) => (
        <div key={item.label} className="stat">
          <span className="label">{item.label}</span>
          <span className="stat-value">{item.value}</span>
          {item.detail ? <span className="stat-detail">{item.detail}</span> : null}
        </div>
      ))}
    </div>
  );
}

function Pills<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <span className="pills" role="tablist">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          role="tab"
          aria-selected={option.id === value}
          className={option.id === value ? "pill active" : "pill"}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </span>
  );
}

function Missing() {
  return <p className="muted">{NOT_AVAILABLE}.</p>;
}

function Disclaimer({ children }: { children: React.ReactNode }) {
  return <p className="disclaimer">{children}</p>;
}

function monthLabel(date?: string | null): string | null {
  if (!date) {
    return null;
  }
  const parsed = new Date(`${date.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    return date;
  }
  return parsed.toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

function timelinePoints(data: DashboardData): RegimeTimelinePoint[] {
  return asArray<RegimeTimelinePoint>(getObject(data.timeline).points);
}

// ---------- Overview ----------

function Overview({ data }: { data: DashboardData }) {
  const macro = getObject(getObject(data.daily).macro);
  const { reported } = macroRegimes(data);
  const points = timelinePoints(data);
  const asOf = monthLabel(
    typeof macro.date === "string" ? macro.date : (data.manifest?.latest_macro_date ?? null),
  );

  let unchanged: number | null = null;
  if (points.length && reported) {
    unchanged = 0;
    for (let i = points.length - 1; i >= 0 && points[i].reported_regime === points[points.length - 1].reported_regime; i--) {
      unchanged += 1;
    }
  }

  const dims = dimensionSeries(data.macroFeatures);
  const order = ["growth_momentum", "inflation_pressure"];
  const sorted = [...dims].sort((a, b) => {
    const ia = order.indexOf(a.id);
    const ib = order.indexOf(b.id);
    return (ia < 0 ? order.length : ia) - (ib < 0 ? order.length : ib);
  });
  const growth = dims.find((d) => d.id === "growth_momentum");
  const inflation = dims.find((d) => d.id === "inflation_pressure");
  const last = (s?: { points: { value: number }[] }) => (s?.points.length ? s.points[s.points.length - 1].value : null);
  const trailLength = 12;
  const trail =
    growth && inflation
      ? growth.points.slice(-trailLength).flatMap((g) => {
          const match = inflation.points.find((p) => p.date === g.date);
          return match ? [{ date: g.date, growth: g.value, inflation: match.value }] : [];
        })
      : [];

  const dailyProbs = getObject(getObject(data.daily).regime_probabilities);
  const probSource = Object.keys(dailyProbs).length ? dailyProbs : (points[points.length - 1]?.probabilities ?? {});
  const probabilities: Record<string, number> = Object.fromEntries(
    Object.entries(probSource).filter((entry): entry is [string, number] => typeof entry[1] === "number"),
  );

  const nowGrowth = last(growth);
  const nowInflation = last(inflation);
  const placed = nowGrowth !== null && nowInflation !== null ? quadrantOf(nowGrowth, nowInflation) : null;
  const disagreement =
    placed && reported && reported !== "tightening" && placed !== reported
      ? `The indicator averages place the reading in ${prettyRegime(placed)}, not the reported ${prettyRegime(reported)}. The engine weighs more than these two averages${
          (numberValue(getObject(getObject(data.daily).macro).confidence) ?? 1) < 0.12 ? ", and confidence in the label is low" : ""
        }.`
      : null;
  const subcopy = overviewSubcopy(data, unchanged);
  return (
    <>
      <Head
        tab="overview"
        large
        eyebrow={`U.S. macro regime${asOf ? ` · as of ${asOf}` : ""}`}
        tags={<Tag>data {text(data.manifest?.data_status, "unknown")}</Tag>}
        headline={
          reported ? (
            <>
              The U.S. economy currently reads as <span className="accent">{prettyRegime(reported)}</span>.
            </>
          ) : (
            "Not enough data yet to summarise this tab."
          )
        }
      >
        {subcopy ? <p className="subcopy">{subcopy}</p> : null}
      </Head>
      <div className="split">
        <Card title="Growth ↑ · Inflation →" info={TOOLTIPS.regime}>
          <QuadrantMap growth={last(growth)} inflation={last(inflation)} trail={trail} regime={reported} />
          <p className="caption">
            {growth && inflation
              ? "Dot marks the current reading and the fading dots the past 12 months; hover a dot for its values. Scale is ±2 standard deviations around average. Tightening has no quadrant and is shown as a ring."
              : "Growth and inflation readings are not available in this export."}
          </p>
          {disagreement ? <p className="caption">{disagreement}</p> : null}
        </Card>
        <Card title="Dimensions">
          {sorted.length ? (
            <ul className="rows">
              {sorted.map((dim) => (
                <li key={dim.id}>
                  <span>{dim.label}</span>
                  <span className="num">{formatSigned(last(dim), 2)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Missing />
          )}
          <p className="caption">Average of the latest indicator z-scores in each dimension.</p>
        </Card>
      </div>
      <Card title="Regime probabilities" info={TOOLTIPS.regime_probabilities}>
        <ProbabilityBar probabilities={probabilities} />
      </Card>
      <Disclaimer>
        Diagnostic only. Not investment advice. Uses revised FRED data, not point-in-time vintages.
      </Disclaimer>
    </>
  );
}

// ---------- Macro ----------

function MacroPanel({ data }: { data: DashboardData }) {
  const { reported, raw } = macroRegimes(data);
  const daily = getObject(data.daily);
  const macro = getObject(daily.macro);
  const confidence = numberValue(macro.confidence) ?? numberValue(getObject(data.sectors).macro_confidence);
  const points = timelinePoints(data);
  const latestProbs = points[points.length - 1]?.probabilities ?? {};
  const rawProbs = getObject(daily.regime_probabilities);
  const probOf = (regime: string | null): number | null =>
    regime ? (numberValue(rawProbs[regime]) ?? numberValue(latestProbs[regime])) : null;
  const dims = asArray<MacroDimensionSeries>(getObject(data.macroFeatures).dimensions).filter(
    (d) => (d.dimension_id ?? "") !== "unmapped" && (d.features?.length ?? 0) > 0,
  );
  const [dimId, setDimId] = useState<string | null>(null);
  const activeDim = dims.find((d) => d.dimension_id === dimId) ?? dims[0];
  const held = Boolean(reported && raw && reported !== raw);
  const note = regimeConfidenceNote(confidence, prettyRegime(reported));

  const featureEnd = text(getObject(data.macroFeatures).end_date, "");
  const seriesHealth = new Map<string, { last: string; stale: boolean }>();
  for (const dim of dims) {
    for (const feature of dim.features ?? []) {
      const id = feature.series_id || feature.feature_id || "";
      const lastDate = feature.points?.[feature.points.length - 1]?.date ?? "";
      const prev = seriesHealth.get(id);
      if (id && (!prev || lastDate > prev.last)) {
        seriesHealth.set(id, { last: lastDate, stale: Boolean(featureEnd && lastDate < featureEnd) });
      }
    }
  }

  return (
    <>
      <Head tab="macro" eyebrow="Macro · reported vs raw signal" headline={macroHeadline(data)} />
      <Stats
        items={[
          {
            label: "Reported",
            value: reported ? prettyRegime(reported) : "n/a",
            detail: confidence !== null ? `confidence ${formatPct(confidence)}` : undefined,
          },
          {
            label: "Raw leader",
            value: raw ? prettyRegime(raw) : "n/a",
            detail: probOf(raw) !== null ? `probability ${formatPct(probOf(raw))}` : undefined,
          },
          {
            label: "Transition filter",
            value: held ? `Holding ${prettyRegime(reported)}` : "Not holding",
            detail: held ? "Raw leader differs from the published label." : "Raw leader and reported label agree.",
          },
        ]}
      />
      {note ? <p className="caption">{note}</p> : null}
      <Card title="Probability history · 24 months" info={TOOLTIPS.regime_timeline}>
        {points.length > 1 ? <ProbabilityHistory points={points.slice(-24)} /> : <Missing />}
      </Card>
      <Card title="Indicators by dimension" info={TOOLTIPS.macro_indicators}>
        {activeDim ? (
          <>
            <div className="underline-tabs" role="tablist">
              {dims.map((dim) => (
                <button
                  key={dim.dimension_id}
                  type="button"
                  role="tab"
                  aria-selected={dim.dimension_id === activeDim.dimension_id}
                  className={dim.dimension_id === activeDim.dimension_id ? "utab active" : "utab"}
                  onClick={() => setDimId(dim.dimension_id ?? null)}
                >
                  {dimensionLabel(dim.dimension_id ?? "")}
                </button>
              ))}
            </div>
            <div className="multiples">
              {(activeDim.features ?? []).map((feature, i) => (
                <div key={`${feature.feature_id}-${i}`} className="multiple">
                  <span className="label">{featureLabel(feature)}</span>
                  <MiniLine points={feature.points ?? []} />
                </div>
              ))}
            </div>
            <p className="caption">Each line is a z-score: 0 is average, above 0 elevated, below 0 depressed.</p>
          </>
        ) : (
          <Missing />
        )}
      </Card>
      <Card title={`Source health · ${seriesHealth.size} FRED series`}>
        {seriesHealth.size ? (
          <div className="tags">
            {[...seriesHealth.entries()].map(([id, health]) => (
              <Tag key={id} tone={health.stale ? "warn" : undefined}>
                {id}
                {health.stale ? ` · last ${health.last.slice(0, 7)}` : ""}
              </Tag>
            ))}
          </div>
        ) : (
          <Missing />
        )}
      </Card>
    </>
  );
}

function regimeConfidenceNote(confidence: number | null, regime: string): string | null {
  if (confidence === null || !Number.isFinite(confidence)) {
    return null;
  }
  if (confidence >= 0.35) {
    return `High conviction: the macro data points clearly to ${regime} and the underlying dimensions broadly agree.`;
  }
  if (confidence >= 0.12) {
    return `Moderate conviction: ${regime} leads, but one or two others are in contention. Treat it as a lean, not a firm call.`;
  }
  return `Low conviction: no single regime dominates, so the ${regime} label is weak signal. Read the dimensions rather than this one label.`;
}

function featureLabel(f: { feature_id?: string; series_id?: string }): string {
  const id = f.feature_id ?? "";
  const tag = /yoy/.test(id) ? "YoY" : /12m/.test(id) ? "12m Δ" : /6m/.test(id) ? "6m Δ" : /level/.test(id) ? "level" : "";
  return `${f.series_id || id}${tag ? ` · ${tag}` : ""}`;
}

// ---------- Sectors ----------

function SectorPanel({ data }: { data: DashboardData }) {
  const rows = useMemo(
    () => [...sectorRows(data.sectors)].sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)),
    [data.sectors],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = rows.find((r) => r.sector_id === selectedId) ?? rows[0];
  const topId = rows[0]?.confidence_adjusted_score;

  const components = selected
    ? [
        ...asArray<Record<string, unknown>>(selected.top_supporting_components),
        ...asArray<Record<string, unknown>>(selected.top_opposing_components),
      ]
        .filter((c) => typeof c.contribution === "number")
        .sort((a, b) => (b.contribution as number) - (a.contribution as number))
        .map((c, i) => ({
          key: `${text(c.component_id)}-${i}`,
          label:
            c.component_type === "regime_prior"
              ? `Regime prior · ${prettyRegime(text(c.component_id, "unknown"))}`
              : dimensionLabel(text(c.component_id, "unknown")),
          value: c.contribution as number,
        }))
    : [];

  return (
    <>
      <Head tab="sectors" eyebrow="Sectors · macro tailwinds and headwinds" headline={sectorsHeadline(data)} tags={<Tag>experimental</Tag>} />
      <Card flush info={TOOLTIPS.sector_ranking}>
        {rows.length ? (
          <table className="grid-table">
            <thead>
              <tr>
                <th className="col-rank label">#</th>
                <th className="label">Sector</th>
                <th className="label">Proxy</th>
                <th className="num-col label">Raw</th>
                <th className="num-col label">Conf.-adj.</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={row.sector_id ?? i}
                  className={row.sector_id === selected?.sector_id ? "selectable selected" : "selectable"}
                  tabIndex={0}
                  onClick={() => setSelectedId(row.sector_id ?? null)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedId(row.sector_id ?? null);
                    }
                  }}
                >
                  <td className="col-rank num">{row.rank ?? i + 1}</td>
                  <td>{row.label ?? prettySectorId(row.sector_id ?? "unknown")}</td>
                  <td className="mono">{text(row.proxy_ticker, "—")}</td>
                  <td className="num-col num">{formatSigned(row.raw_sector_score, 2)}</td>
                  <td className={i === 0 && topId !== undefined ? "num-col num accent" : "num-col num"}>
                    {formatSigned(row.confidence_adjusted_score, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="card-pad"><Missing /></div>
        )}
      </Card>
      <Card title={`Selected sector · score components${selected ? ` · ${selected.label ?? prettySectorId(selected.sector_id ?? "")}` : ""}`} info={TOOLTIPS.sector_components}>
        <DivergingBars rows={components} />
      </Card>
      <ValidationCard data={data} />
    </>
  );
}

const CROSS_SECTION_LABELS: Record<string, string> = {
  gics_11: "11 sectors",
  subindustry_6: "6 sub-industries",
  pooled_17: "All 17",
};

function ValidationCard({ data }: { data: DashboardData }) {
  const v = getObject(data.validation);
  const rows = asArray<ValidationSummaryRow>(v.summary);
  const sections = [...new Set(rows.map((r) => r.cross_section ?? "all"))];
  const [section, setSection] = useState<string | null>(null);
  const [horizon, setHorizon] = useState<string | null>(null);
  const activeSection = sections.includes(section ?? "") ? (section as string) : sections[0];
  const inSection = rows.filter((r) => (r.cross_section ?? "all") === activeSection);
  const active = inSection.find((r) => r.horizon === horizon) ?? inSection[0];
  const bestIc = inSection.reduce(
    (m, r) => Math.max(m, typeof r.rank_ic_spearman === "number" ? Math.abs(r.rank_ic_spearman) : 0),
    0,
  );
  const spread = numberValue(active?.top_minus_bottom_spread);
  return (
    <Card
      title="ETF proxy validation vs SPY"
      info={TOOLTIPS.validation}
      right={
        active ? (
          <span className="pill-groups">
            {sections.length > 1 ? (
              <Pills
                options={sections.map((id) => ({ id, label: CROSS_SECTION_LABELS[id] ?? id }))}
                value={activeSection}
                onChange={setSection}
              />
            ) : null}
            {inSection.length > 1 ? (
              <Pills
                options={inSection.map((r) => ({ id: text(r.horizon), label: text(r.horizon) }))}
                value={text(active.horizon)}
                onChange={setHorizon}
              />
            ) : null}
          </span>
        ) : undefined
      }
    >
      {active ? (
        <>
          <div className="metrics">
            <div>
              <span className="label">Rank IC</span>
              <span className="metric-value num">{formatScore(active.rank_ic_spearman)}</span>
            </div>
            <div>
              <span className="label">Top − bottom</span>
              <span className="metric-value num">
                {spread === null ? "n/a" : `${spread > 0 ? "+" : spread < 0 ? "−" : ""}${Math.abs(spread * 100).toFixed(1)}%`}
              </span>
            </div>
            <div>
              <span className="label">Hit rate</span>
              <span className="metric-value num">{formatPct(active.hit_rate_top_positive)}</span>
            </div>
          </div>
          <p className="caption">
            {bestIc >= 0.1 ? "Result: some forward signal." : "Result: weak / mixed."} Not a trading backtest.{" "}
            {text(active.observation_count, "0")} observations; scores {text(v.score_start_date)}–{text(v.score_end_date)}.
          </p>
        </>
      ) : (
        <Missing />
      )}
    </Card>
  );
}

// ---------- News ----------

function NewsPanel({ data }: { data: DashboardData }) {
  const report = getObject(data.newsScores);
  const dailyNews = getObject(getObject(data.daily).news);
  const themeItems = [
    ...scoreItems(report.top_positive_macro_themes),
    ...scoreItems(report.top_negative_macro_themes),
  ];
  const themes = (themeItems.length ? themeItems : scoreItems(dailyNews.top_themes))
    .filter((t) => typeof t.score === "number")
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const sectorItems = [
    ...scoreItems(report.top_sector_news_tailwinds),
    ...scoreItems(report.top_sector_news_headwinds),
  ];
  const sectors = (sectorItems.length
    ? sectorItems
    : [...scoreItems(dailyNews.top_sector_tailwinds), ...scoreItems(dailyNews.top_sector_headwinds)]
  )
    .filter((s) => typeof s.score === "number")
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const labels = sectorLabelById(data.sectors);
  const lowConfidence = asArray<Record<string, unknown>>(report.low_confidence_items);
  const asOf = text(report.latest_news_scoring_date, "latest run");

  return (
    <>
      <Head tab="news" eyebrow="News · AI-classified themes" headline={newsHeadline(data)} tags={<Tag>as of {asOf}</Tag>} />
      <div className="split even">
        <Card title="Macro themes" info={TOOLTIPS.news_themes}>
          {themes.length ? (
            <ul className="rows">
              {themes.map((t, i) => (
                <li key={`${t.id}-${i}`}>
                  <span>{prettySectorId(t.id ?? "unknown")}</span>
                  <span className="num">{formatSigned(t.score, 2)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Missing />
          )}
        </Card>
        <Card title="Sector news scores" info={TOOLTIPS.news_themes}>
          <p className="caption">Strongest tailwinds and headwinds only, not every sector.</p>
          <DivergingBars
            rows={sectors.map((s, i) => ({
              key: `${s.id}-${i}`,
              label: prettySectorLabel(s.id, labels),
              value: s.score ?? 0,
              note: `${formatCount(s.item_count ?? 0)} items`,
            }))}
          />
        </Card>
      </div>
      <Card title="Low-confidence classifications" info={TOOLTIPS.low_confidence_items}>
        {lowConfidence.length ? (
          <ul className="rows">
            {lowConfidence.slice(0, 8).map((item, i) => (
              <li key={`${item.news_id}-${i}`}>
                <span>{text(item.title)}</span>
                <Tag>conf {formatScore(item.confidence)}</Tag>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">None.</p>
        )}
      </Card>
      <Disclaimer>AI classifications are interpretive and can be wrong.</Disclaimer>
    </>
  );
}

// ---------- Combined ----------

function CombinedPanel({ data }: { data: DashboardData }) {
  const overlay = getObject(getNested(data.monitoring, "overlay_monitoring"));
  const labels = sectorLabelById(data.sectors, data.combined);
  const slope: SlopeRow[] = overlayMoves(data).map((m) => ({ ...m, label: prettySectorLabel(m.id, labels) }));
  const maxChange = slope.reduce((m, r) => Math.max(m, Math.abs(r.from - r.to)), 0);
  const fallback = slope.filter((r) => r.thin).length;
  const guardrail = text(getNested(data.daily, "step_statuses", "guardrail_status"));
  return (
    <>
      <Head tab="combined" eyebrow="Combined · macro + bounded news overlay" headline={combinedHeadline(data)} tags={<Tag>experimental</Tag>} />
      <Card title="Macro-only rank → combined rank" info={TOOLTIPS.combined_overlay}>
        <SlopeChart rows={[...slope].sort((a, b) => a.from - b.from)} />
        <p className="caption">Greyed sectors carry their macro-only score because the overlay gives their news no weight.</p>
      </Card>
      <Stats
        items={[
          { label: "Max rank change", value: String(maxChange) },
          { label: "Macro-only fallback", value: `${fallback} ${fallback === 1 ? "sector" : "sectors"}` },
          { label: "Guardrail", value: guardrail },
        ]}
      />
      <p className="caption">
        Overlay status {text(overlay.overlay_status, "n/a")} · {formatCount(overlay.news_item_count)} news items in the
        overlay. 75% macro, 25% bounded news.
      </p>
    </>
  );
}

// ---------- Monitoring ----------

const READINESS_STEPS = [
  { id: "insufficient_history", label: "Insufficient", range: "<5 run dates" },
  { id: "early_history", label: "Early", range: "5–20" },
  { id: "monitor_ready", label: "Monitor ready", range: "20+" },
  { id: "validation_candidate", label: "Validation candidate", range: "60+" },
];

function MonitoringPanel({ data }: { data: DashboardData }) {
  const daily = getObject(data.daily);
  const accumulation = getObject(data.accumulation);
  const latest = getObject(accumulation.latest_run);
  const coverage = getObject(data.coverage);
  const monitoring = getObject(data.monitoring);
  const classification = getObject(monitoring.classification_quality);
  const readiness = text(accumulation.readiness_label ?? latest.readiness_label, "insufficient_history");
  const warnings = asArray<string>(daily.warnings);
  const errors = asArray<string>(daily.errors);
  const counts = getObject(coverage.item_count_by_group);
  const stale = new Set(asArray<string>(coverage.stale_groups));
  const missing = new Set(asArray<string>(coverage.missing_data_groups));
  const groupIds = [...new Set([...Object.keys(counts), ...missing])];
  const maxCount = groupIds.reduce((m, id) => Math.max(m, numberValue(counts[id]) ?? 0), 0) || 1;
  const over = asArray<{ source_group?: string; share?: number }>(coverage.overrepresented_groups)[0];
  // The coverage report is optional. Without it, show each group's live sources from news_health.
  const health = getObject(daily.news_health);
  const sourceById = new Map(asArray<Record<string, unknown>>(health.sources).map((s) => [text(s.source_id), s]));
  const hasCoverage = groupIds.length > 0;
  type CoverageRow = { id: string; fill: number; value: string; flags: string[] };
  const coverageRows: CoverageRow[] = hasCoverage
    ? groupIds.map((id) => ({
        id,
        fill: (numberValue(counts[id]) ?? 0) / maxCount,
        value: formatCount(counts[id] ?? 0),
        flags: [...(missing.has(id) ? ["missing"] : []), ...(stale.has(id) ? ["stale"] : [])],
      }))
    : asArray<{ group?: string; sources?: string[]; uncovered?: boolean }>(health.groups).map((g) => {
        const ids = g.sources ?? [];
        const ok = ids.filter((id) => {
          const source = sourceById.get(id);
          return source?.status === "ok" && !source.dead;
        }).length;
        return {
          id: g.group ?? "",
          fill: ids.length ? ok / ids.length : 0,
          value: `${ok}/${ids.length}`,
          flags: g.uncovered ? ["uncovered"] : [],
        };
      });
  const unmapped = coverage.unmapped_pct ?? getNested(monitoring, "input_quality", "details_json", "unmapped_pct");

  return (
    <>
      <Head tab="monitoring" eyebrow="Monitoring · operating health" headline={monitoringHeadline(data)} />
      <Card title="Readiness" info={TOOLTIPS.readiness_label}>
        <ol className="track">
          {READINESS_STEPS.map((step) => (
            <li key={step.id} className={step.id === readiness ? "track-step active" : "track-step"}>
              {step.label}
              <span className="track-range">{step.range}</span>
            </li>
          ))}
        </ol>
        <p className="caption">
          {formatCount(accumulation.total_classified_items ?? latest.classified_items)} classified items ·{" "}
          {readinessMeaning(readiness)}
        </p>
      </Card>
      <Stats
        items={[
          { label: "Classify success", value: formatPct(classification.success_rate) },
          { label: "Unmapped", value: formatPct(unmapped) },
          { label: "Warnings", value: String(warnings.length) },
          { label: "Errors", value: String(errors.length) },
        ]}
      />
      <Card title="Source group coverage" info={TOOLTIPS.coverage_warnings}>
        {coverageRows.length ? (
          <>
            <ul className="coverage">
              {coverageRows.map((row) => (
                <li key={row.id}>
                  <span>{prettySectorId(row.id)}</span>
                  <span className="coverage-track">
                    <span className="coverage-bar" style={{ width: `${row.fill * 100}%` }} />
                  </span>
                  <span className="num">{row.value}</span>
                  <span className="coverage-flags">
                    {row.flags.map((flag) => (
                      <Tag key={flag} tone="warn">
                        {flag}
                      </Tag>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
            <p className="caption">
              {hasCoverage
                ? "Stored news items per source group."
                : "Sources reporting ok out of those configured, per group."}
            </p>
          </>
        ) : (
          <Missing />
        )}
        {over?.source_group && typeof over.share === "number" ? (
          <p className="caption">
            {prettySectorId(over.source_group)} holds {formatPct(over.share)} of stored items.
          </p>
        ) : null}
      </Card>
      <Card title="Latest run">
        <ul className="rows">
          <li><span>Last run (Taipei)</span><span className="num">{formatRunDate(daily.run_date, daily.run_id)}</span></li>
          <li><span>Status</span><span className="num">{text(daily.status)}</span></li>
          <li><span>Run id</span><span className="num">{text(daily.run_id)}</span></li>
          <li><span>Dashboard data exported</span><span className="num">{formatStamp(data.manifest?.generated_at)}</span></li>
          <li><span>Guardrail</span><span className="num">{text(getNested(daily, "step_statuses", "guardrail_status"))}</span></li>
          <li><span>Data source</span><span className="num">{data.source === "sample" ? "sample fixtures" : "exported outputs"}</span></li>
        </ul>
      </Card>
      {[...errors, ...warnings, ...asArray<string>(coverage.warnings)].length ? (
        <Card title="Warnings" info={TOOLTIPS.coverage_warnings}>
          <ul className="notes">
            {errors.map((item, i) => (
              <li key={`e${i}`} className="bad">{item}</li>
            ))}
            {[...warnings, ...asArray<string>(coverage.warnings)].map((item, i) => (
              <li key={`w${i}`}>{item}</li>
            ))}
          </ul>
        </Card>
      ) : null}
      <Card title="News sources we read" info={TOOLTIPS.news_sources}>
        <NewsSources data={data} />
      </Card>
      <Card title="News source health" info={TOOLTIPS.news_health}>
        <NewsSourceHealthTable data={data} />
      </Card>
    </>
  );
}

// ---------- History ----------

type RunFilter = "all" | "live" | "replay";

function HistoryPanel({ data }: { data: DashboardData }) {
  const [filter, setFilter] = useState<RunFilter>("all");
  const all = historyRuns(data.history);
  const filtered = all.filter((r) =>
    filter === "all" ? true : filter === "replay" ? r.run_mode === "replay" : r.run_mode !== "replay",
  );
  const filteredCount = filtered.length;
  const rows = filtered.slice(0, 30);
  const points = timelinePoints(data);
  const nber = getObject(data.nberBenchmark);
  const nberOk = nber.status === "ok";
  const recessions = asArray<{ start?: string; end?: string }>(nber.nber_recessions).flatMap((r) =>
    r.start && r.end ? [{ start: r.start, end: r.end }] : [],
  );
  const leads = asArray<{ lead_lag_months?: number | null }>(nber.recession_detection)
    .map((r) => r.lead_lag_months)
    .filter((v): v is number => typeof v === "number")
    .sort((a, b) => a - b);
  const medianLead = leads.length ? (leads[Math.floor((leads.length - 1) / 2)] + leads[Math.ceil((leads.length - 1) / 2)]) / 2 : null;
  const reportedMetrics = getObject(getObject(nber.label_metrics).reported);
  const sinceYear = points[0]?.date?.slice(0, 4);

  return (
    <>
      <Head
        tab="history"
        eyebrow="History · daily runs"
        headline={historyHeadline(data)}
        tags={
          <Pills
            options={[
              { id: "all", label: "All" },
              { id: "live", label: "Live" },
              { id: "replay", label: "Replay" },
            ]}
            value={filter}
            onChange={setFilter}
          />
        }
      />
      <Card title={`Reported regime${sinceYear ? ` since ${sinceYear}` : ""}`} info={TOOLTIPS.regime_timeline}>
        {points.length > 1 ? <RegimeRibbon points={points} recessions={nberOk ? recessions : []} /> : <Missing />}
      </Card>
      <Card flush>
        {rows.length ? (
          <table className="grid-table">
            <thead>
              <tr>
                <th className="label">Date</th>
                <th className="label">Mode</th>
                <th className="label">Regime · top sectors</th>
                <th className="label">Guard</th>
                <th className="num-col label">Warn</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <HistoryRow key={`${row.run_id}-${i}`} row={row} />
              ))}
            </tbody>
          </table>
        ) : (
          <div className="card-pad"><p className="muted">No archived daily runs found.</p></div>
        )}
      </Card>
      {filteredCount > rows.length ? (
        <p className="caption">
          Showing the latest {rows.length} of {filteredCount} runs.
        </p>
      ) : null}
      <Card title="NBER benchmark (revised data)">
        {nberOk ? (
          <>
            <div className="metrics">
              <div>
                <span className="label">AUROC</span>
                <span className="metric-value num">{formatScore(nber.auroc)}</span>
              </div>
              <div>
                <span className="label">Hit rate</span>
                <span className="metric-value num">{formatPct(reportedMetrics.recession_hit_rate)}</span>
              </div>
              <div>
                <span className="label">Median lead</span>
                <span className="metric-value num">
                  {medianLead === null ? "n/a" : `${Math.abs(medianLead)} mo ${medianLead <= 0 ? "early" : "late"}`}
                </span>
              </div>
            </div>
            <p className="caption">Compared with public NBER dates on revised data; not a point-in-time backtest.</p>
          </>
        ) : (
          <Missing />
        )}
      </Card>
      <Disclaimer>Replay runs are operating checks, not predictive backtests.</Disclaimer>
    </>
  );
}

// Several runs can land on one Taipei date, so show the UTC time from the run id.
function runTimeUtc(runId?: string): string | null {
  const match = runId?.match(/^\d{8}T(\d{2})(\d{2})\d{2}Z/);
  return match ? `${match[1]}:${match[2]} UTC` : null;
}

function HistoryRow({ row }: { row: HistoryRun }) {
  const replay = row.run_mode === "replay";
  return (
    <tr className={replay ? "replay" : undefined}>
      <td className="mono">
        {formatRunDate(row.run_date, row.run_id)}
        {runTimeUtc(row.run_id) ? <span className="muted"> {runTimeUtc(row.run_id)}</span> : null}
      </td>
      <td>{replay ? <Tag>replay</Tag> : text(row.run_mode, "daily")}</td>
      <td>
        {prettyRegime(row.macro_regime)}
        <span className="muted"> · {(row.top_combined_sectors ?? []).slice(0, 2).map(prettySectorId).join(", ") || "n/a"}</span>
      </td>
      <td>{row.guardrail_status === "passed" ? "passed" : <Tag tone="warn">{text(row.guardrail_status)}</Tag>}</td>
      <td className="num-col num">{text(row.warning_count, "0")}</td>
    </tr>
  );
}

function prettyDimension(id: string): string {
  return prettySectorId(id);
}

function ProgramSummary({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="summary-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="program-summary-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="program-summary-title">What This Program Does</h2>
          <button type="button" className="close-button" aria-label="Close summary" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="summary-copy">
          <p>
            This dashboard is a read-only view of a local economic diagnostic system. The
            Python backend gathers data, prepares reports, and exports JSON files. This website
            reads those exported files and turns them into a dashboard you can review each day.
          </p>
          <nav className="toc" aria-label="Guide contents">
            <a href="#guide-flow">How the data flows</a>
            <a href="#guide-tabs">What each tab shows</a>
            <a href="#guide-detail">Under the hood</a>
            <a href="#guide-glossary">Glossary</a>
          </nav>
          <div className="summary-section" id="guide-flow">
            <h3>How the data flows</h3>
            <ol className="flow-list">
              {DATA_FLOW.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          </div>
          <div className="summary-section">
            <h3>1. It checks the economic backdrop</h3>
            <p>
              The backend looks at economic data such as inflation, job conditions, growth,
              credit stress, interest rates, and market-related indicators. It compares the
              latest readings with recent history and turns them into a current macro picture.
            </p>
            <p>
              The goal is not to predict the future perfectly. The goal is to make the current
              backdrop easier to inspect: what is strong, what is weak, what is changing, and
              how confident the system is.
            </p>
          </div>
          <div className="summary-section">
            <h3>2. It labels the current macro regime</h3>
            <p>
              The system groups the backdrop into a broad regime label, such as reflation,
              slowdown, inflation pressure, or other macro states used by the backend. Think of
              the regime as a short name for the current economic weather.
            </p>
            <p>
              The dashboard also shows confidence. A low confidence number means the signals
              are mixed or weak. A higher confidence number means the data points more clearly
              toward one backdrop.
            </p>
          </div>
          <div className="summary-section">
            <h3>3. It maps the macro backdrop to sectors</h3>
            <p>
              Different sectors can react differently to the same backdrop. For example,
              energy, financials, utilities, real estate, and technology may be sensitive to
              different combinations of growth, inflation, rates, and credit conditions.
            </p>
            <p>
              The sector pages show which sectors have stronger or weaker diagnostic scores
              under the current macro setup. These are research signals only. They are not
              instructions to change holdings, choose securities, or make a market move.
            </p>
          </div>
          <div className="summary-section">
            <h3>4. It can classify news and events</h3>
            <p>
              The news layer takes articles or event text and turns them into structured
              information: macro themes, sector impacts, confidence, severity, and uncertainty.
              This helps separate a pile of headlines into a cleaner view of what themes are
              showing up.
            </p>
            <p>
              The AI step is used only to interpret unstructured text. After that, the scoring
              and aggregation are handled by the backend in a transparent, repeatable way.
            </p>
          </div>
          <div className="summary-section">
            <h3>5. It combines macro and news diagnostics</h3>
            <p>
              The combined view compares the macro-only sector picture with the news overlay.
              The news overlay is intentionally bounded, so a small amount of news should not
              completely overpower the macro backdrop.
            </p>
            <p>
              This is useful for seeing whether recent news confirms, softens, or slightly
              changes the sector picture. If news coverage is thin or uneven, the dashboard
              shows warnings instead of pretending the signal is stronger than it is.
            </p>
          </div>
          <div className="summary-section">
            <h3>6. It monitors data quality and daily runs</h3>
            <p>
              The monitoring pages show whether the system ran successfully, whether data was
              missing, whether source coverage is thin, and whether classification quality looks
              healthy. This is important because a dashboard is only as useful as the data behind
              it.
            </p>
            <p>
              The History page keeps a record of recent daily runs. It helps answer practical
              questions: Did the pipeline run? Did reports export? Did the dashboard data
              refresh? Are there enough repeated runs to start learning from the history?
            </p>
          </div>
          <div className="summary-section">
            <h3>7. What this website does not do</h3>
            <p>
              The website does not calculate the model itself. It does not call AI providers.
              It does not store API keys. It does not place market orders. It does not decide
              how to allocate money. It does not tell anyone what to own.
            </p>
            <p>
              It is a display layer for backend-generated diagnostics. The right way to use it
              is as a daily research dashboard: check the state, read the warnings, review the
              history, and decide whether the system itself is healthy enough to pay attention
              to.
            </p>
          </div>
          <div className="summary-section" id="guide-tabs">
            <h3>What each tab shows</h3>
            <dl className="guide-dl">
              {TAB_GUIDE.map((entry) => (
                <div key={entry.tab}>
                  <dt>{entry.tab}</dt>
                  <dd>{entry.read}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="summary-section" id="guide-detail">
            <h3>Under the hood (the gritty details)</h3>
            {PIPELINE_DETAIL.map((sec) => (
              <div key={sec.title} className="detail-block">
                <h4>{sec.title}</h4>
                <ul className="detail-list">
                  {sec.lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="summary-section" id="guide-glossary">
            <h3>Glossary</h3>
            <dl className="guide-dl">
              {GLOSSARY.map((entry) => (
                <div key={entry.term}>
                  <dt>{entry.term}</dt>
                  <dd>{entry.def}</dd>
                </div>
              ))}
            </dl>
          </div>
          <p className="summary-note">{GUIDE_DISCLAIMER}</p>
        </div>
      </section>
    </div>
  );
}

function NewsSources({ data }: { data: DashboardData }) {
  const payload = getObject(data.newsSources);
  const groups = asArray<NewsSourceGroup>(payload.groups).filter((g) => (g.sources?.length ?? 0) > 0);
  if (!groups.length) {
    return <p className="muted">Source list unavailable. Run the live pipeline and export dashboard data.</p>;
  }
  return (
    <div>
      <p className="muted" style={{ marginTop: 0 }}>
        Live RSS feeds the news layer pulls from (headlines used as diagnostic inputs only, not republished).
      </p>
      <div className="source-groups">
        {groups.map((g) => (
          <div key={g.group} className="source-group">
            <span className="source-group-label">{prettyDimension(g.group ?? "")}</span>
            {(g.sources ?? []).map((s, i) => (
              <a key={i} className="source-chip" href={s.url} target="_blank" rel="noopener noreferrer">
                {prettyDimension(s.name ?? "")}
              </a>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function NewsSourceHealthTable({ data }: { data: DashboardData }) {
  const daily = getObject(data.daily);
  const newsHealth = getObject(daily.news_health);
  const sources = asArray<Record<string, unknown>>(newsHealth.sources);
  if (!sources.length) {
    return <p className="muted">No per-source telemetry yet. Populated after the first live run with news.history_dir set.</p>;
  }
  const sorted = [...sources].sort((a, b) => {
    const deadA = a.dead ? 1 : 0;
    const deadB = b.dead ? 1 : 0;
    return deadB - deadA;
  });
  return (
    <table>
      <thead>
        <tr>
          <th>Source</th>
          <th>Status</th>
          <th>New items</th>
          <th>Last new item</th>
          <th>Bad-run streak</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((row, index) => (
          <tr key={`${text(row.source_id)}-${index}`} className={row.dead ? "row-dead" : undefined}>
            <td>{text(row.source_id)}</td>
            <td>{text(row.status)}{row.dead ? " (dead)" : ""}</td>
            <td>{formatCount(row.items_new)}</td>
            <td>{row.last_new_at ? formatStamp(row.last_new_at) : "never"}</td>
            <td>{formatCount(row.consecutive_bad_runs)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function InfoTip({ text: tip }: { text: string }) {
  return (
    <span className="infotip" tabIndex={0} role="button" aria-label={tip}>
      <span className="infotip-glyph" aria-hidden="true">?</span>
      <span className="infotip-bubble" role="tooltip">{tip}</span>
    </span>
  );
}

function readinessMeaning(label: string): string {
  if (label === "validation_candidate") {
    return "enough history for validation planning";
  }
  if (label === "monitor_ready") {
    return "enough history for monitoring";
  }
  if (label === "early_history") {
    return "early operating record";
  }
  return "more daily runs needed";
}
