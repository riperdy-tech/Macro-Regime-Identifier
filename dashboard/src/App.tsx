import { useEffect, useMemo, useState } from "react";
import { loadDashboardData } from "./data";
import type { DashboardData, RankedSector } from "./types";
import {
  asArray,
  combinedRows,
  formatPct,
  formatScore,
  getNested,
  getObject,
  scoreItems,
  sectorRows,
  text,
} from "./utils";

type TabId = "overview" | "macro" | "sectors" | "news" | "combined" | "monitoring";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "macro", label: "Macro" },
  { id: "sectors", label: "Sectors" },
  { id: "news", label: "News" },
  { id: "combined", label: "Combined" },
  { id: "monitoring", label: "Monitoring" },
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
        {activeTab === "overview" && <Overview data={data} />}
        {activeTab === "macro" && <MacroPanel data={data} />}
        {activeTab === "sectors" && <SectorPanel data={data} />}
        {activeTab === "news" && <NewsPanel data={data} />}
        {activeTab === "combined" && <CombinedPanel data={data} />}
        {activeTab === "monitoring" && <MonitoringPanel data={data} />}
      </main>
    </Shell>
  );
}

function Shell({ children, status }: { children: React.ReactNode; status: string }) {
  return (
    <div className="app">
      <header className="masthead">
        <div>
          <p className="eyebrow">Read-only backend output viewer</p>
          <h1>Macro Diagnostic Dashboard</h1>
        </div>
        <div className="status-pill">{status}</div>
      </header>
      {children}
    </div>
  );
}

function Overview({ data }: { data: DashboardData }) {
  const daily = getObject(data.daily);
  const macro = getObject(daily.macro);
  const accumulation = getObject(getObject(data.accumulation).latest_run);
  const monitoring = getObject(data.monitoring);
  const classification = getObject(monitoring.classification_quality);
  return (
    <section className="grid two">
      <Metric label="Run status" value={text(daily.status)} detail={text(daily.run_id, "No run id")} />
      <Metric
        label="Macro regime"
        value={text(macro.reported_regime)}
        detail={`confidence ${formatPct(macro.confidence)}`}
      />
      <Metric
        label="Latest macro date"
        value={text(macro.date)}
        detail={`raw leader ${text(macro.raw_dominant_regime)}`}
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
      />
      <Metric
        label="Data source"
        value={data.source === "sample" ? "sample fixtures" : "exported outputs"}
        detail={text(data.manifest?.data_status)}
      />
      <Panel title="Top Sector Diagnostics">
        <RankingTable rows={sectorRows(data.sectors).slice(0, 5)} scoreKey="confidence_adjusted_score" />
      </Panel>
      <Panel title="Top News Themes">
        <ScoreList items={scoreItems(getNested(data.newsScores, "top_positive_macro_themes")).slice(0, 5)} />
      </Panel>
      <Panel title="Combined Top Sectors">
        <RankingTable rows={combinedRows(data.combined).slice(0, 5)} scoreKey="combined_score" />
      </Panel>
      <Panel title="Coverage Warnings">
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
      <Metric label="Reported regime" value={text(dailyMacro.reported_regime ?? sectorPayload.reported_macro_regime)} />
      <Metric label="Raw leader" value={text(dailyMacro.raw_dominant_regime ?? sectorPayload.raw_macro_leader)} />
      <Metric label="Confidence" value={formatPct(dailyMacro.confidence ?? sectorPayload.macro_confidence)} />
      <Metric label="Macro date" value={text(dailyMacro.date ?? sectorPayload.date)} />
      <Panel title="Regime Probabilities">
        <KeyValueTable values={probabilities} />
      </Panel>
      <Panel title="Warnings">
        <WarningList items={asArray<string>(getObject(data.daily).warnings)} />
      </Panel>
    </section>
  );
}

function SectorPanel({ data }: { data: DashboardData }) {
  const rows = sectorRows(data.sectors);
  const top = rows[0];
  const bottom = rows[rows.length - 1];
  return (
    <section className="grid two">
      <Metric label="Top sector" value={text(top?.sector_id)} detail={formatScore(top?.confidence_adjusted_score)} />
      <Metric label="Lowest sector" value={text(bottom?.sector_id)} detail={formatScore(bottom?.confidence_adjusted_score)} />
      <Panel title="Sector Ranking" wide>
        <RankingTable rows={rows} scoreKey="confidence_adjusted_score" />
      </Panel>
      <Panel title="Top Sector Components">
        <ComponentList sector={top} />
      </Panel>
      <Panel title="Lowest Sector Components">
        <ComponentList sector={bottom} />
      </Panel>
    </section>
  );
}

function NewsPanel({ data }: { data: DashboardData }) {
  const report = getObject(data.newsScores);
  const monitoring = getObject(data.monitoring);
  const classification = getObject(monitoring.classification_quality);
  return (
    <section className="grid two">
      <Metric label="News score date" value={text(report.latest_news_scoring_date)} />
      <Metric label="Classification success" value={formatPct(classification.success_rate)} />
      <Metric label="Retry rate" value={formatPct(classification.retry_rate)} />
      <Metric label="Repair rate" value={formatPct(classification.repair_rate)} />
      <Panel title="Positive Macro Themes">
        <ScoreList items={scoreItems(report.top_positive_macro_themes)} />
      </Panel>
      <Panel title="Negative Macro Themes">
        <ScoreList items={scoreItems(report.top_negative_macro_themes)} />
      </Panel>
      <Panel title="Sector Diagnostic Tailwinds">
        <ScoreList items={scoreItems(report.top_sector_news_tailwinds)} />
      </Panel>
      <Panel title="Sector Diagnostic Headwinds">
        <ScoreList items={scoreItems(report.top_sector_news_headwinds)} />
      </Panel>
      <Panel title="Low Confidence Items" wide>
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
      <Metric label="Max rank change" value={text(overlay.max_rank_change, "0")} />
      <Metric label="News item count" value={text(overlay.news_item_count, "0")} />
      <Metric label="Overlay status" value={text(overlay.overlay_status)} />
      <Panel title="Combined Ranking" wide>
        <RankingTable rows={combinedRows(data.combined)} scoreKey="combined_score" />
      </Panel>
      <Panel title="Macro-only Top Sectors">
        <RankingTable
          rows={asArray<RankedSector>(overlay.macro_only_top_sectors_json)}
          scoreKey="confidence_adjusted_score"
        />
      </Panel>
      <Panel title="Rank Changes From News Overlay">
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
      <Metric label="Readiness label" value={text(accumulation.readiness_label, "insufficient_history")} />
      <Metric label="Source groups" value={text(coverage.source_group_count ?? accumulation.source_group_count)} />
      <Metric label="Unmapped share" value={formatPct(coverage.unmapped_pct)} />
      <Metric label="Old item share" value={formatPct(coverage.old_item_pct)} />
      <Metric label="Input quality" value={text(inputQuality.quality_status)} />
      <Metric label="Guardrail status" value={text(getNested(data.daily, "step_statuses", "guardrail_status"))} />
      <Panel title="Missing Groups">
        <WarningList items={asArray<string>(coverage.missing_data_groups)} />
      </Panel>
      <Panel title="Coverage Warnings">
        <WarningList items={asArray<string>(coverage.warnings)} />
      </Panel>
      <Panel title="Source Group Counts" wide>
        <KeyValueTable values={getObject(coverage.item_count_by_group)} />
      </Panel>
    </section>
  );
}

function Panel({
  children,
  title,
  wide = false,
}: {
  children: React.ReactNode;
  title: string;
  wide?: boolean;
}) {
  return (
    <section className={wide ? "panel wide" : "panel"}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
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
