import type { RegimeTimelinePoint } from "./types";
import { formatPct, prettyRegime } from "./utils";

// Hand-rolled SVG/CSS charts for the read-only dashboard. Colours come from CSS
// variables so the light and dark themes both apply.

export function regimeColor(regime?: string | null): string {
  return regime ? `var(--rg-${regime}, var(--rg-other))` : "var(--rg-other)";
}

export const REGIME_STACK = ["recession", "stagflation", "tightening", "reflation", "goldilocks"];

// ---------- Growth x inflation quadrant ----------

const QUADRANT_OF: Record<string, "tl" | "tr" | "bl" | "br"> = {
  goldilocks: "tl",
  reflation: "tr",
  recession: "bl",
  stagflation: "br",
};

const QUADRANTS: { id: "tl" | "tr" | "bl" | "br"; label: string }[] = [
  { id: "tl", label: "Goldilocks" },
  { id: "tr", label: "Reflation" },
  { id: "bl", label: "Recession" },
  { id: "br", label: "Stagflation" },
];

const Z_RANGE = 2.5;

function place(value: number): number {
  return 50 + (Math.max(-Z_RANGE, Math.min(Z_RANGE, value)) / Z_RANGE) * 46;
}

export function QuadrantMap({
  growth,
  inflation,
  trail,
  regime,
}: {
  growth: number | null;
  inflation: number | null;
  trail: { growth: number; inflation: number }[];
  regime: string | null;
}) {
  const active = regime ? QUADRANT_OF[regime] : undefined;
  const hasDot = growth !== null && inflation !== null;
  return (
    <div className="quadrant" role="img" aria-label="Growth by inflation quadrant map">
      {QUADRANTS.map((q) => (
        <div key={q.id} className={q.id === active ? "quadrant-cell active" : "quadrant-cell"}>
          {q.label}
        </div>
      ))}
      {trail.length > 1 ? (
        <svg className="quadrant-trail" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <polyline
            points={trail.map((p) => `${place(p.inflation).toFixed(2)},${(100 - place(p.growth)).toFixed(2)}`).join(" ")}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      ) : null}
      {hasDot ? (
        <span
          className={regime === "tightening" ? "quadrant-dot ring" : "quadrant-dot"}
          style={{ left: `${place(inflation)}%`, top: `${100 - place(growth)}%` }}
        />
      ) : null}
    </div>
  );
}

// ---------- Regime probability bar ----------

export function ProbabilityBar({ probabilities }: { probabilities: Record<string, number> }) {
  const entries = Object.entries(probabilities)
    .filter(([, v]) => typeof v === "number" && v > 0)
    .sort(([, a], [, b]) => b - a);
  if (!entries.length) {
    return <p className="muted">Not available in this export.</p>;
  }
  return (
    <div>
      <div className="probbar" role="img" aria-label="Regime probabilities">
        {entries.map(([regime, value], i) => (
          <span
            key={regime}
            className={i === 0 ? "probbar-seg leader" : `probbar-seg grey-${Math.min(i, 4)}`}
            style={{ flexGrow: value }}
            title={`${prettyRegime(regime)} ${formatPct(value)}`}
          />
        ))}
      </div>
      <ul className="probbar-legend">
        {entries.map(([regime, value], i) => (
          <li key={regime} className={i === 0 ? "leader" : undefined}>
            <span className={i === 0 ? "probbar-key leader" : `probbar-key grey-${Math.min(i, 4)}`} />
            {prettyRegime(regime)} <span className="num">{Math.round(value * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------- Probability history (stacked area) + reported band ----------

export function ProbabilityHistory({ points }: { points: RegimeTimelinePoint[] }) {
  const n = points.length;
  const width = 600;
  const height = 130;
  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * width);
  const y = (p: number) => (1 - p) * height;
  const cum = points.map(() => 0);
  const bands = REGIME_STACK.map((regime) => {
    const top: string[] = [];
    const bottom: string[] = [];
    points.forEach((point, i) => {
      const lower = cum[i];
      const upper = lower + (point.probabilities?.[regime] ?? 0);
      bottom.push(`${x(i).toFixed(1)},${y(lower).toFixed(1)}`);
      top.push(`${x(i).toFixed(1)},${y(upper).toFixed(1)}`);
      cum[i] = upper;
    });
    return { regime, d: `M ${top.join(" L ")} L ${bottom.reverse().join(" L ")} Z` };
  });
  const runs: { regime: string; start: number; len: number }[] = [];
  points.forEach((point, i) => {
    const regime = point.reported_regime ?? "unknown";
    const last = runs[runs.length - 1];
    if (last && last.regime === regime) {
      last.len += 1;
    } else {
      runs.push({ regime, start: i, len: 1 });
    }
  });
  const cell = n <= 1 ? width : width / (n - 1);
  return (
    <div className="area-chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Regime probabilities, last 24 months">
        {bands.map((b) => (
          <path key={b.regime} d={b.d} style={{ fill: regimeColor(b.regime) }} />
        ))}
      </svg>
      <svg className="area-band" viewBox={`0 0 ${width} 10`} preserveAspectRatio="none" role="img" aria-label="Reported regime per month">
        {runs.map((run, i) => (
          <rect
            key={i}
            x={Math.max(x(run.start) - cell / 2, 0)}
            y={0}
            width={Math.min(run.len * cell, width)}
            height={10}
            style={{ fill: regimeColor(run.regime) }}
          >
            <title>{prettyRegime(run.regime)}</title>
          </rect>
        ))}
      </svg>
      <div className="area-axis">
        <span>{points[0]?.date?.slice(0, 7)}</span>
        <span>{points[n - 1]?.date?.slice(0, 7)}</span>
      </div>
      <ul className="legend-row">
        {[...REGIME_STACK].reverse().map((regime) => (
          <li key={regime}>
            <span className="legend-dot" style={{ background: regimeColor(regime) }} />
            {prettyRegime(regime)}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------- Small-multiple line ----------

export function MiniLine({ points }: { points: { date?: string; value?: number }[] }) {
  const values = points.filter((p): p is { date?: string; value: number } => typeof p.value === "number");
  if (values.length < 2) {
    return <p className="muted">No data.</p>;
  }
  const zMax = 3.5;
  const width = 200;
  const height = 56;
  const mid = height / 2;
  const x = (i: number) => (i / (values.length - 1)) * width;
  const y = (v: number) => mid - (Math.max(-zMax, Math.min(zMax, v)) / zMax) * (mid - 3);
  return (
    <svg className="miniline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img">
      <line x1={0} y1={mid} x2={width} y2={mid} className="miniline-zero" vectorEffect="non-scaling-stroke" />
      <polyline
        className="miniline-path"
        vectorEffect="non-scaling-stroke"
        points={values.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")}
      />
    </svg>
  );
}

// ---------- Diverging bars ----------

export function DivergingBars({
  rows,
}: {
  rows: { key: string; label: string; value: number; note?: string }[];
}) {
  if (!rows.length) {
    return <p className="muted">Not available in this export.</p>;
  }
  const max = rows.reduce((m, r) => Math.max(m, Math.abs(r.value)), 0) || 1;
  return (
    <ul className="diverge">
      {rows.map((row) => (
        <li key={row.key}>
          <span className="diverge-label">{row.label}</span>
          <span className="diverge-track" aria-hidden="true">
            <span
              className={row.value >= 0 ? "diverge-bar pos" : "diverge-bar neg"}
              style={{ width: `${(Math.abs(row.value) / max) * 50}%` }}
            />
          </span>
          <span className="num diverge-value">
            {row.value > 0 ? "+" : row.value < 0 ? "−" : ""}
            {Math.abs(row.value).toFixed(2)}
          </span>
          {row.note ? <span className="diverge-note">{row.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

// ---------- Slope chart ----------

export type SlopeRow = { id: string; label: string; from: number; to: number; thin: boolean };

export function SlopeChart({ rows }: { rows: SlopeRow[] }) {
  if (!rows.length) {
    return <p className="muted">Not available in this export.</p>;
  }
  const step = 26;
  const top = 18;
  const width = 640;
  const left = 190;
  const right = width - 190;
  const height = top * 2 + step * (rows.length - 1);
  const maxMove = rows.reduce((m, r) => Math.max(m, Math.abs(r.from - r.to)), 0);
  const yOf = (rank: number) => top + (rank - 1) * step;
  return (
    <svg className="slope" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Macro-only rank to combined rank">
      {rows.map((r) => {
        const move = Math.abs(r.from - r.to);
        const cls = r.thin ? "slope-line thin" : move > 0 && move === maxMove ? "slope-line peak" : "slope-line";
        return (
          <g key={r.id} className={cls}>
            <line x1={left} y1={yOf(r.from)} x2={right} y2={yOf(r.to)} />
            <circle cx={left} cy={yOf(r.from)} r={3} />
            <circle cx={right} cy={yOf(r.to)} r={3} />
            <text x={left - 10} y={yOf(r.from) + 4} textAnchor="end">
              {r.from} {r.label}
            </text>
            <text x={right + 10} y={yOf(r.to) + 4}>
              {r.to} {r.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------- Regime ribbon with recession ticks ----------

export function RegimeRibbon({
  points,
  recessions,
}: {
  points: RegimeTimelinePoint[];
  recessions: { start: string; end: string }[];
}) {
  const n = points.length;
  const width = 1000;
  const startT = Date.parse(points[0]?.date ?? "");
  const endT = Date.parse(points[n - 1]?.date ?? "");
  const span = endT - startT;
  const px = (iso: string) =>
    span > 0 ? Math.max(0, Math.min(width, ((Date.parse(`${iso}-01`) - startT) / span) * width)) : 0;
  const cell = n <= 1 ? width : width / (n - 1);
  const runs: { regime: string; start: number; len: number }[] = [];
  points.forEach((point, i) => {
    const regime = point.reported_regime ?? "unknown";
    const last = runs[runs.length - 1];
    if (last && last.regime === regime) {
      last.len += 1;
    } else {
      runs.push({ regime, start: i, len: 1 });
    }
  });
  const years: { x: number; label: string }[] = [];
  let lastYear = "";
  points.forEach((point, i) => {
    const year = (point.date ?? "").slice(0, 4);
    if (year && Number(year) % 10 === 0 && year !== lastYear) {
      years.push({ x: n <= 1 ? 0 : (i / (n - 1)) * width, label: year });
      lastYear = year;
    }
  });
  return (
    <div>
      <svg className="ribbon" viewBox={`0 0 ${width} 44`} preserveAspectRatio="none" role="img" aria-label="Reported regime by month">
        {runs.map((run, i) => (
          <rect
            key={i}
            x={Math.max((run.start / Math.max(n - 1, 1)) * width - cell / 2, 0)}
            y={0}
            width={Math.min(run.len * cell, width)}
            height={26}
            style={{ fill: regimeColor(run.regime) }}
          >
            <title>{prettyRegime(run.regime)}</title>
          </rect>
        ))}
        {recessions.map((r, i) => (
          <rect key={`n${i}`} className="ribbon-nber" x={px(r.start)} y={30} width={Math.max(px(r.end) - px(r.start), 2)} height={6} />
        ))}
      </svg>
      <div className="area-axis">
        {years.length ? years.map((y) => <span key={y.label}>{y.label}</span>) : null}
      </div>
      <ul className="legend-row">
        {[...REGIME_STACK].reverse().map((regime) => (
          <li key={regime}>
            <span className="legend-dot" style={{ background: regimeColor(regime) }} />
            {prettyRegime(regime)}
          </li>
        ))}
        {recessions.length ? (
          <li>
            <span className="legend-tick" />
            NBER recession
          </li>
        ) : null}
      </ul>
    </div>
  );
}
