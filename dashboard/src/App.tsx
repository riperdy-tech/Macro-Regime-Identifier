import { useEffect, useMemo, useState } from "react";
import { loadDashboardData } from "./data";
import type {
  DashboardData,
  HistoryRun,
  RankedSector,
  RegimeTimelinePoint,
  ValidationSummaryRow,
} from "./types";
import {
  asArray,
  combinedRows,
  formatPct,
  formatScore,
  getNested,
  getObject,
  historyRuns,
  scoreItems,
  sectorRows,
  text,
} from "./utils";
import {
  DATA_FLOW,
  GLOSSARY,
  GUIDE_DISCLAIMER,
  TAB_GUIDE,
  TAB_INTROS,
  TOOLTIPS,
} from "./help";

type TabId = "overview" | "macro" | "sectors" | "news" | "combined" | "monitoring" | "history";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "macro", label: "Macro" },
  { id: "sectors", label: "Sectors" },
  { id: "news", label: "News" },
  { id: "combined", label: "Combined" },
  { id: "monitoring", label: "Monitoring" },
  { id: "history", label: "History" },
];

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData().then((loaded) => {
      setData(loaded);
      setLoading(false);
    });
  }, []);

  const status = useMemo(() => dataStatus(data), [data]);

  if (loading) {
    return <Shell status="Loading data">Loading dashboard data...</Shell>;
  }

  if (!data || data.source === "empty") {
    return (
      <Shell status="No data">
        <section className="empty-state">
          <h2>Dashboard Data Unavailable</h2>
          <p>Run the backend daily pipeline and export dashboard data, then refresh this page.</p>
          <code>python -m macro_engine.cli export-dashboard-data</code>
        </section>
      </Shell>
    );
  }

  return (
    <Shell status={status}>
      <div className="tabbar" role="tablist" aria-label="Dashboard sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={tab.id === activeTab ? "tab active" : "tab"}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <main>
        <p className="tab-intro">{TAB_INTROS[activeTab]}</p>
        {activeTab === "overview" && <Overview data={data} />}
        {activeTab === "macro" && <MacroPanel data={data} />}
        {activeTab === "sectors" && <SectorPanel data={data} />}
        {activeTab === "news" && <NewsPanel data={data} />}
        {activeTab === "combined" && <CombinedPanel data={data} />}
        {activeTab === "monitoring" && <MonitoringPanel data={data} />}
        {activeTab === "history" && <HistoryPanel data={data} />}
      </main>
    </Shell>
  );
}

function Shell({ children, status }: { children: React.ReactNode; status: string }) {
  const [showSummary, setShowSummary] = useState(false);

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <p className="eyebrow">Read-only backend output viewer</p>
          <h1>Macro Diagnostic Dashboard</h1>
        </div>
        <div className="header-actions">
          <button type="button" className="summary-button" onClick={() => setShowSummary(true)}>
            How it works
          </button>
          <div className="status-pill">{status}</div>
        </div>
      </header>
      {showSummary ? <ProgramSummary onClose={() => setShowSummary(false)} /> : null}
      {children}
    </div>
  );
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

function Overview({ data }: { data: DashboardData }) {
  const daily = getObject(data.daily);
  const macro = getObject(daily.macro);
  const accumulation = getObject(getObject(data.accumulation).latest_run);
  const monitoring = getObject(data.monitoring);
  const classification = getObject(monitoring.classification_quality);
  const missingFiles = data.manifest?.missing_files ?? [];
  return (
    <section className="grid two">
      <Metric label="Run status" value={text(daily.status)} detail={text(daily.run_id, "No run id")} />
      <Metric label="Archive path" value={text(daily.archive_path)} />
      <Metric label="Exported at" value={text(data.manifest?.generated_at)} detail={text(data.manifest?.data_status)} />
      <Metric
        label="Macro regime"
        value={text(macro.reported_regime)}
        detail={`confidence ${formatPct(macro.confidence)}`}
        info={TOOLTIPS.regime}
      />
      <Metric
        label="Latest macro date"
        value={text(macro.date)}
        detail={`raw leader ${text(macro.raw_dominant_regime)}`}
        info={TOOLTIPS.reported_vs_raw}
      />
      <Metric
        label="Accumulation"
        value={text(accumulation.quality_status)}
        detail={`classified ${text(accumulation.classified_items, "0")} items`}
      />
      <Metric
        label="Classification quality"
        value={formatPct(classification.success_rate)}
        detail={`retry ${formatPct(classification.retry_rate)} / repair ${formatPct(classification.repair_rate)}`}
        info={TOOLTIPS.classification_success}
      />
      <Metric
        label="Data source"
        value={data.source === "sample" ? "sample fixtures" : "exported outputs"}
        detail={missingFiles.length ? `${missingFiles.length} missing files` : "complete file set"}
        info={TOOLTIPS.data_source}
      />
      <Panel title="Data Status" info={TOOLTIPS.data_status}>
        <WarningList
          items={
            missingFiles.length
              ? missingFiles.map((filename) => `Missing ${filename}`)
              : ["Exported dashboard data is complete."]
          }
        />
      </Panel>
      <Panel title="Top Sector Diagnostics" info={TOOLTIPS.confidence_adjusted_score}>
        <RankingTable rows={sectorRows(data.sectors).slice(0, 5)} scoreKey="confidence_adjusted_score" />
      </Panel>
      <Panel title="Top News Themes" info={TOOLTIPS.news_themes}>
        <ScoreList items={scoreItems(getNested(data.newsScores, "top_positive_macro_themes")).slice(0, 5)} />
      </Panel>
      <Panel title="Combined Top Sectors" info={TOOLTIPS.combined_overlay}>
        <RankingTable rows={combinedRows(data.combined).slice(0, 5)} scoreKey="combined_score" />
      </Panel>
      <Panel title="Coverage Warnings" info={TOOLTIPS.coverage_warnings}>
        <WarningList items={asArray<string>(getObject(data.coverage).warnings)} />
      </Panel>
    </section>
  );
}

function MacroPanel({ data }: { data: DashboardData }) {
  const dailyMacro = getObject(getObject(data.daily).macro);
  const sectorPayload = getObject(data.sectors);
  const probabilities = getObject(getObject(data.daily).regime_probabilities);
  return (
    <section className="grid two">
      <Metric label="Reported regime" value={text(dailyMacro.reported_regime ?? sectorPayload.reported_macro_regime)} info={TOOLTIPS.regime} />
      <Metric label="Raw leader" value={text(dailyMacro.raw_dominant_regime ?? sectorPayload.raw_macro_leader)} info={TOOLTIPS.reported_vs_raw} />
      <Metric label="Confidence" value={formatPct(dailyMacro.confidence ?? sectorPayload.macro_confidence)} info={TOOLTIPS.confidence} />
      <Metric label="Macro date" value={text(dailyMacro.date ?? sectorPayload.date)} info={TOOLTIPS.macro_date} />
      <Panel title="Regime Timeline (1990 to present)" wide info={TOOLTIPS.regime_timeline}>
        <RegimeTimeline data={data} />
      </Panel>
      <Panel title="Regime Probabilities" info={TOOLTIPS.regime_probabilities}>
        <KeyValueTable values={probabilities} />
      </Panel>
      <Panel title="Warnings">
        <WarningList items={asArray<string>(getObject(data.daily).warnings)} />
      </Panel>
    </section>
  );
}

const REGIME_COLORS: Record<string, string> = {
  goldilocks: "#2e7d32",
  reflation: "#1565c0",
  stagflation: "#c62828",
  recession: "#6a1b9a",
  tightening: "#ef6c00",
};

function regimeColor(regime?: string | null): string {
  if (!regime) {
    return "#9e9e9e";
  }
  return REGIME_COLORS[regime] ?? "#607d8b";
}

function RegimeTimeline({ data }: { data: DashboardData }) {
  const timeline = getObject(data.timeline);
  const points = asArray<RegimeTimelinePoint>(timeline.points);
  if (!points.length) {
    return <p className="muted">Regime timeline unavailable. Run the daily pipeline and export dashboard data.</p>;
  }
  const width = 960;
  const height = 60;
  const segWidth = width / points.length;
  const regimes = Array.from(new Set(points.map((point) => point.reported_regime ?? "unknown")));
  const ticks: { x: number; label: string }[] = [];
  let lastYear = "";
  points.forEach((point, index) => {
    const year = (point.date ?? "").slice(0, 4);
    if (year && year !== lastYear && Number(year) % 5 === 0) {
      ticks.push({ x: index * segWidth, label: year });
      lastYear = year;
    }
  });
  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height + 20}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label="Macro regime over time"
      >
        {points.map((point, index) => (
          <rect
            key={index}
            x={index * segWidth}
            y={0}
            width={segWidth + 0.5}
            height={height}
            fill={regimeColor(point.reported_regime)}
            opacity={point.valid === false ? 0.35 : 1}
          >
            <title>{`${point.date}: ${point.reported_regime ?? "unknown"}`}</title>
          </rect>
        ))}
        {ticks.map((tick, index) => (
          <text key={index} x={tick.x} y={height + 14} fontSize={10} fill="#666">
            {tick.label}
          </text>
        ))}
      </svg>
      <ul style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", listStyle: "none", padding: 0, margin: "0.5rem 0 0" }}>
        {regimes.map((regime) => (
          <li key={regime} style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.8rem" }}>
            <span
              style={{
                display: "inline-block",
                width: 12,
                height: 12,
                borderRadius: 2,
                background: regimeColor(regime),
              }}
            />
            {regime}
          </li>
        ))}
      </ul>
      <small className="muted">
        {text(timeline.start_date)} to {text(timeline.end_date)} · {text(timeline.point_count)} points
      </small>
    </div>
  );
}

function SectorPanel({ data }: { data: DashboardData }) {
  const rows = sectorRows(data.sectors);
  const top = rows[0];
  const bottom = rows[rows.length - 1];
  return (
    <section className="grid two">
      <Metric label="Top sector" value={text(top?.sector_id)} detail={formatScore(top?.confidence_adjusted_score)} info={TOOLTIPS.confidence_adjusted_score} />
      <Metric label="Lowest sector" value={text(bottom?.sector_id)} detail={formatScore(bottom?.confidence_adjusted_score)} info={TOOLTIPS.confidence_adjusted_score} />
      <Panel title="Sector Ranking" wide info={TOOLTIPS.sector_ranking}>
        <RankingTable rows={rows} scoreKey="confidence_adjusted_score" />
      </Panel>
      <Panel title="Top Sector Components" info={TOOLTIPS.sector_components}>
        <ComponentList sector={top} />
      </Panel>
      <Panel title="Lowest Sector Components" info={TOOLTIPS.sector_components}>
        <ComponentList sector={bottom} />
      </Panel>
      <Panel title="Signal Validation (does this work?)" wide info={TOOLTIPS.validation}>
        <ValidationScorecard data={data} />
      </Panel>
    </section>
  );
}

function ValidationScorecard({ data }: { data: DashboardData }) {
  const v = getObject(data.validation);
  const rows = asArray<ValidationSummaryRow>(v.summary);
  if (!rows.length) {
    return <p className="muted">Validation unavailable. Run the sector validation step.</p>;
  }
  const bestIc = rows.reduce(
    (m, r) => Math.max(m, typeof r.rank_ic_spearman === "number" ? Math.abs(r.rank_ic_spearman) : 0),
    0,
  );
  const verdict =
    bestIc >= 0.1
      ? "Some forward signal: scores show modest rank skill on sector returns."
      : "No measurable forward edge (rank IC ~0, hit-rate ~50%). Treat this as a descriptive macro lens, not a return predictor.";
  return (
    <div>
      <p className="muted" style={{ marginTop: 0 }}>{verdict}</p>
      <table>
        <thead>
          <tr>
            <th>Horizon</th>
            <th>Rank IC</th>
            <th>Hit rate (top)</th>
            <th>Top−Bottom spread</th>
            <th>Obs</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.horizon}-${i}`}>
              <td>{text(r.horizon)}</td>
              <td>{formatScore(r.rank_ic_spearman)}</td>
              <td>{formatPct(r.hit_rate_top_positive)}</td>
              <td>{formatScore(r.top_minus_bottom_spread)}</td>
              <td>{text(r.observation_count, "0")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <small className="muted">
        Sector scores {text(v.score_start_date)}–{text(v.score_end_date)} vs forward ETF-proxy
        returns {text(v.price_start_date)}–{text(v.price_end_date)}. Diagnostic only, not a trading
        backtest; cost/slippage not modeled.
      </small>
    </div>
  );
}

function NewsPanel({ data }: { data: DashboardData }) {
  const report = getObject(data.newsScores);
  const monitoring = getObject(data.monitoring);
  const classification = getObject(monitoring.classification_quality);
  return (
    <section className="grid two">
      <Metric label="News score date" value={text(report.latest_news_scoring_date)} />
      <Metric label="Classification success" value={formatPct(classification.success_rate)} info={TOOLTIPS.classification_success} />
      <Metric label="Retry rate" value={formatPct(classification.retry_rate)} info={TOOLTIPS.retry_rate} />
      <Metric label="Repair rate" value={formatPct(classification.repair_rate)} info={TOOLTIPS.repair_rate} />
      <Panel title="Positive Macro Themes" info={TOOLTIPS.news_themes}>
        <ScoreList items={scoreItems(report.top_positive_macro_themes)} />
      </Panel>
      <Panel title="Negative Macro Themes" info={TOOLTIPS.news_themes}>
        <ScoreList items={scoreItems(report.top_negative_macro_themes)} />
      </Panel>
      <Panel title="Sector Diagnostic Tailwinds" info={TOOLTIPS.news_themes}>
        <ScoreList items={scoreItems(report.top_sector_news_tailwinds)} />
      </Panel>
      <Panel title="Sector Diagnostic Headwinds" info={TOOLTIPS.news_themes}>
        <ScoreList items={scoreItems(report.top_sector_news_headwinds)} />
      </Panel>
      <Panel title="Low Confidence Items" wide info={TOOLTIPS.low_confidence_items}>
        <ItemTable items={asArray<Record<string, unknown>>(report.low_confidence_items)} />
      </Panel>
    </section>
  );
}

function CombinedPanel({ data }: { data: DashboardData }) {
  const combined = getObject(data.combined);
  const monitoring = getObject(data.monitoring);
  const overlay = getObject(monitoring.overlay_monitoring);
  return (
    <section className="grid two">
      <Metric label="Diagnostic date" value={text(combined.diagnostic_date ?? overlay.diagnostic_date)} />
      <Metric label="Max rank change" value={text(overlay.max_rank_change, "0")} info={TOOLTIPS.overlay_rank_change} />
      <Metric label="News item count" value={text(overlay.news_item_count, "0")} info={TOOLTIPS.news_item_count} />
      <Metric label="Overlay status" value={text(overlay.overlay_status)} info={TOOLTIPS.combined_overlay} />
      <Panel title="Combined Ranking" wide info={TOOLTIPS.combined_overlay}>
        <RankingTable rows={combinedRows(data.combined)} scoreKey="combined_score" />
      </Panel>
      <Panel title="Macro-only Top Sectors" info={TOOLTIPS.confidence_adjusted_score}>
        <RankingTable
          rows={asArray<RankedSector>(overlay.macro_only_top_sectors_json)}
          scoreKey="confidence_adjusted_score"
        />
      </Panel>
      <Panel title="Rank Changes From News Overlay" info={TOOLTIPS.overlay_rank_change}>
        <ChangeList items={asArray<Record<string, unknown>>(overlay.sectors_changed_by_news_json)} />
      </Panel>
    </section>
  );
}

function MonitoringPanel({ data }: { data: DashboardData }) {
  const accumulation = getObject(getObject(data.accumulation).latest_run);
  const coverage = getObject(data.coverage);
  const monitoring = getObject(data.monitoring);
  const inputQuality = getObject(monitoring.input_quality);
  return (
    <section className="grid two">
      <Metric label="Readiness label" value={text(accumulation.readiness_label, "insufficient_history")} info={TOOLTIPS.readiness_label} />
      <Metric
        label="Readiness meaning"
        value={readinessMeaning(text(accumulation.readiness_label, "insufficient_history"))}
        info={TOOLTIPS.readiness_label}
      />
      <Metric label="Source groups" value={text(coverage.source_group_count ?? accumulation.source_group_count)} />
      <Metric label="Unmapped share" value={formatPct(coverage.unmapped_pct)} info={TOOLTIPS.unmapped_share} />
      <Metric label="Old item share" value={formatPct(coverage.old_item_pct)} info={TOOLTIPS.old_item_share} />
      <Metric label="Input quality" value={text(inputQuality.quality_status)} info={TOOLTIPS.input_quality} />
      <Metric label="Guardrail status" value={text(getNested(data.daily, "step_statuses", "guardrail_status"))} info={TOOLTIPS.guardrail} />
      <Panel title="Missing Groups" info={TOOLTIPS.coverage_warnings}>
        <WarningList items={asArray<string>(coverage.missing_data_groups)} />
      </Panel>
      <Panel title="Coverage Warnings" info={TOOLTIPS.coverage_warnings}>
        <WarningList items={asArray<string>(coverage.warnings)} />
      </Panel>
      <Panel title="Source Group Counts" wide info={TOOLTIPS.coverage_warnings}>
        <KeyValueTable values={getObject(coverage.item_count_by_group)} />
      </Panel>
    </section>
  );
}

function HistoryPanel({ data }: { data: DashboardData }) {
  const history = getObject(data.history);
  const rows = historyRuns(data.history).slice(0, 20);
  const latest = rows[0];
  const confidenceValues = rows
    .map((row) => row.macro_confidence)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const avgConfidence =
    confidenceValues.length > 0
      ? confidenceValues.reduce((total, value) => total + value, 0) / confidenceValues.length
      : null;
  return (
    <section className="grid two">
      <Metric label="History status" value={text(history.history_status, "empty")} />
      <Metric label="Recorded runs" value={text(history.total_runs, "0")} info={TOOLTIPS.recorded_runs} />
      <Metric label="Latest run" value={text(latest?.run_date)} detail={text(latest?.run_id)} />
      <Metric label="Average macro confidence" value={avgConfidence === null ? "n/a" : formatPct(avgConfidence)} info={TOOLTIPS.avg_confidence} />
      <Panel title="History Readiness" wide info={TOOLTIPS.history_readiness}>
        {rows.length < 2 ? (
          <p className="muted">
            Run-over-run trend deltas need at least two recorded daily runs. For full
            regime history, see the Regime Timeline (1990 to present) panel above.
          </p>
        ) : (
          <TrendCards rows={rows} />
        )}
      </Panel>
      <Panel title="Recent Daily Runs" wide>
        <HistoryTable rows={rows} />
      </Panel>
    </section>
  );
}

function TrendCards({ rows }: { rows: HistoryRun[] }) {
  const latestReadiness = text(rows[0]?.readiness_label, "insufficient_history");
  const latestSuccess = rows[0]?.classification_success_rate;
  const maxRankChange = rows.reduce(
    (currentMax, row) => Math.max(currentMax, typeof row.max_overlay_rank_change === "number" ? row.max_overlay_rank_change : 0),
    0,
  );
  return (
    <div className="trend-grid">
      <Metric label="Latest readiness" value={latestReadiness} detail={readinessMeaning(latestReadiness)} />
      <Metric label="Latest classification success" value={formatPct(latestSuccess)} />
      <Metric label="Largest overlay rank change" value={text(maxRankChange)} />
    </div>
  );
}

function HistoryTable({ rows }: { rows: HistoryRun[] }) {
  if (!rows.length) {
    return <p className="muted">No archived daily runs found.</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Mode</th>
          <th>Status</th>
          <th>Macro</th>
          <th>Confidence</th>
          <th>Combined top</th>
          <th>Readiness</th>
          <th>Guardrail</th>
          <th>Warnings</th>
          <th>Errors</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={`${row.run_id}-${index}`}>
            <td>
              <strong>{text(row.run_date)}</strong>
              <small className="block">{text(row.run_id)}</small>
            </td>
            <td>
              {text(row.run_mode, "daily")}
              {row.replay_date ? <small className="block">replay {row.replay_date}</small> : null}
            </td>
            <td>{text(row.status)}</td>
            <td>{text(row.macro_regime)}</td>
            <td>{formatPct(row.macro_confidence)}</td>
            <td>{(row.top_combined_sectors ?? []).join(", ") || "n/a"}</td>
            <td>{text(row.readiness_label, "insufficient_history")}</td>
            <td>{text(row.guardrail_status)}</td>
            <td>{text(row.warning_count, "0")}</td>
            <td>{text(row.error_count, "0")}</td>
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

function Panel({
  children,
  title,
  wide = false,
  info,
}: {
  children: React.ReactNode;
  title: string;
  wide?: boolean;
  info?: string;
}) {
  return (
    <section className={wide ? "panel wide" : "panel"}>
      <h2>
        {title}
        {info ? <InfoTip text={info} /> : null}
      </h2>
      {children}
    </section>
  );
}

function Metric({
  label,
  value,
  detail,
  info,
}: {
  label: string;
  value: string;
  detail?: string;
  info?: string;
}) {
  return (
    <div className="metric">
      <span>
        {label}
        {info ? <InfoTip text={info} /> : null}
      </span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function RankingTable({
  rows,
  scoreKey,
}: {
  rows: RankedSector[];
  scoreKey: keyof RankedSector;
}) {
  if (!rows.length) {
    return <p className="muted">Data unavailable.</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Rank</th>
          <th>Sector</th>
          <th>Score</th>
          <th>News Items</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={`${row.sector_id}-${index}`}>
            <td>{row.rank ?? index + 1}</td>
            <td>{row.label ?? row.sector_id}</td>
            <td>{formatScore(row[scoreKey])}</td>
            <td>{row.news_item_count ?? "n/a"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ScoreList({ items }: { items: { id?: string; score?: number; item_count?: number }[] }) {
  if (!items.length) {
    return <p className="muted">Data unavailable.</p>;
  }
  return (
    <ul className="score-list">
      {items.map((item, index) => (
        <li key={`${item.id}-${index}`}>
          <span>{item.id}</span>
          <strong>{formatScore(item.score)}</strong>
          <small>{item.item_count ?? 0} items</small>
        </li>
      ))}
    </ul>
  );
}

function ComponentList({ sector }: { sector?: RankedSector }) {
  const supporting = asArray<Record<string, unknown>>(getObject(sector).top_supporting_components).slice(0, 5);
  const opposing = asArray<Record<string, unknown>>(getObject(sector).top_opposing_components).slice(0, 5);
  return (
    <div className="component-grid">
      <div>
        <h3>Supporting</h3>
        <ContributionList rows={supporting} />
      </div>
      <div>
        <h3>Opposing</h3>
        <ContributionList rows={opposing} />
      </div>
    </div>
  );
}

function ContributionList({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) {
    return <p className="muted">Data unavailable.</p>;
  }
  return (
    <ul className="compact-list">
      {rows.map((row, index) => (
        <li key={`${row.component_id}-${index}`}>
          <span>{text(row.component_id)}</span>
          <strong>{formatScore(row.contribution)}</strong>
        </li>
      ))}
    </ul>
  );
}

function ItemTable({ items }: { items: Record<string, unknown>[] }) {
  if (!items.length) {
    return <p className="muted">Data unavailable.</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Severity</th>
          <th>Confidence</th>
        </tr>
      </thead>
      <tbody>
        {items.slice(0, 8).map((item, index) => (
          <tr key={`${item.news_id}-${index}`}>
            <td>{text(item.title)}</td>
            <td>{formatScore(item.severity)}</td>
            <td>{formatPct(item.confidence)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ChangeList({ items }: { items: Record<string, unknown>[] }) {
  if (!items.length) {
    return <p className="muted">No rank changes recorded.</p>;
  }
  return (
    <ul className="compact-list">
      {items.map((item, index) => (
        <li key={`${item.sector_id}-${index}`}>
          <span>{text(item.sector_id)}</span>
          <strong>{text(item.rank_change)}</strong>
        </li>
      ))}
    </ul>
  );
}

function WarningList({ items }: { items: string[] }) {
  if (!items.length) {
    return <p className="muted">None.</p>;
  }
  return (
    <ul className="warning-list">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  );
}

function KeyValueTable({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (!entries.length) {
    return <p className="muted">Data unavailable.</p>;
  }
  return (
    <table>
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <td>{key}</td>
            <td>{typeof value === "number" ? formatScore(value) : text(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function dataStatus(data: DashboardData | null): string {
  if (!data?.manifest) {
    return "No data";
  }
  const source = data.source === "sample" ? "sample" : "exported";
  return `${source} / ${data.manifest.data_status ?? "unknown"}`;
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
