import { useState, type PointerEvent as ReactPointerEvent } from "react";
import type { RegimeTimelinePoint } from "./types";
import { formatPct, prettyRegime } from "./utils";

// Hand-rolled SVG/CSS charts for the read-only dashboard. Colours come from CSS
// variables so the light and dark themes both apply.

export function regimeColor(regime?: string | null): string {
  return regime ? `var(--rg-${regime}, var(--rg-other))` : "var(--rg-other)";
}

export const REGIME_STACK = ["recession", "stagflation", "tightening", "reflation", "goldilocks"];


// ---------- Hover plumbing shared by the time-series charts ----------

function useHoverIndex(n: number) {
  const [index, setIndex] = useState<number | null>(null);
  const bind = {
    onPointerMove: (e: ReactPointerEvent<HTMLElement>) => {
      const box = e.currentTarget.getBoundingClientRect();
      if (box.width <= 0 || n < 1) {
        return;
      }
      const fraction = (e.clientX - box.left) / box.width;
      setIndex(Math.max(0, Math.min(n - 1, Math.round(fraction * (n - 1)))));
    },
    onPointerLeave: () => setIndex(null),
  };
  return { index, bind };
}

function pct(index: number, n: number): number {
  return n <= 1 ? 0 : (index / (n - 1)) * 100;
}

function monthYear(date?: string | null): string {
  if (!date) {
    return "";
  }
  const d = new Date(`${date.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? date
    : d.toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

// Evenly spread tick indices, always including the first and last point.
function tickIndices(n: number, count: number): number[] {
  if (n <= 1) {
    return [0];
  }
  const steps = Math.min(count, n) - 1;
  return Array.from(new Set(Array.from({ length: steps + 1 }, (_, i) => Math.round((i / steps) * (n - 1)))));
}

function XAxis({ points, count }: { points: { date?: string }[]; count: number }) {
  const n = points.length;
  return (
    <div className="xaxis" aria-hidden="true">
      {tickIndices(n, count).map((i) => (
        <span key={i} className={i === 0 ? "first" : i === n - 1 ? "last" : undefined} style={{ left: `${pct(i, n)}%` }}>
          {monthYear(points[i]?.date)}
        </span>
      ))}
    </div>
  );
}

function Tooltip({ at, n, children }: { at: number; n: number; children: React.ReactNode }) {
  return (
    <div className={pct(at, n) > 60 ? "tip flip" : "tip"} style={{ left: `${pct(at, n)}%` }} role="status">
      {children}
    </div>
  );
}

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

export const Z_RANGE = 2;

function place(value: number): number {
  return 50 + (Math.max(-Z_RANGE, Math.min(Z_RANGE, value)) / Z_RANGE) * 46;
}

export function quadrantOf(growth: number, inflation: number): string {
  if (growth >= 0) {
    return inflation < 0 ? "goldilocks" : "reflation";
  }
  return inflation < 0 ? "recession" : "stagflation";
}

export function QuadrantMap({
  growth,
  inflation,
  trail,
  regime,
}: {
  growth: number | null;
  inflation: number | null;
  trail: { date?: string; growth: number; inflation: number }[];
  regime: string | null;
}) {
  const active = regime ? QUADRANT_OF[regime] : undefined;
  const hasDot = growth !== null && inflation !== null;
  const past = trail.slice(0, -1);
  return (
    <div className="quadrant" role="img" aria-label="Growth by inflation quadrant map">
      {QUADRANTS.map((q) => (
        <div key={q.id} className={`quadrant-cell ${q.id}${q.id === active ? " active" : ""}`}>
          {q.label}
        </div>
      ))}
      {past.map((p, i) => (
        <span
          key={p.date ?? i}
          className="quadrant-trail-dot"
          style={{
            left: `${place(p.inflation)}%`,
            top: `${100 - place(p.growth)}%`,
            opacity: 0.15 + (0.45 * (i + 1)) / past.length,
          }}
          title={`${monthYear(p.date)}: growth ${p.growth.toFixed(2)}, inflation ${p.inflation.toFixed(2)}`}
        />
      ))}
      {hasDot ? (
        <span
          className={regime === "tightening" ? "quadrant-dot ring" : "quadrant-dot"}
          style={{ left: `${place(inflation)}%`, top: `${100 - place(growth)}%` }}
          title={`Now: growth ${growth.toFixed(2)}, inflation ${inflation.toFixed(2)}`}
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
  const { index, bind } = useHoverIndex(n);
  const width = 600;
  const height = 130;
  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * width);
  const y = (p: number) => (1 - p) * height;
  // Months without probabilities are gaps, not zeros: draw each run of readings on its own.
  const hasReading = points.map((p) => Object.values(p.probabilities ?? {}).reduce((t, v) => t + v, 0) > 0.0001);
  const segments: number[][] = [];
  hasReading.forEach((ok, i) => {
    if (!ok) {
      return;
    }
    const last = segments[segments.length - 1];
    if (last && last[last.length - 1] === i - 1) {
      last.push(i);
    } else {
      segments.push([i]);
    }
  });
  const bands = segments.flatMap((segment) => {
    const cum = segment.map(() => 0);
    return REGIME_STACK.map((regime) => {
      const top: string[] = [];
      const bottom: string[] = [];
      segment.forEach((i, k) => {
        const lower = cum[k];
        const upper = lower + (points[i].probabilities?.[regime] ?? 0);
        bottom.push(`${x(i).toFixed(1)},${y(lower).toFixed(1)}`);
        top.push(`${x(i).toFixed(1)},${y(upper).toFixed(1)}`);
        cum[k] = upper;
      });
      return { key: `${segment[0]}-${regime}`, regime, d: `M ${top.join(" L ")} L ${bottom.reverse().join(" L ")} Z` };
    });
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
  const hovered = index === null ? null : points[index];
  const hoveredRows = hovered && index !== null && hasReading[index] ? Object.entries(hovered.probabilities ?? {}).sort(([, a], [, b]) => b - a) : [];
  return (
    <div className="area-chart">
      <div className="plot-grid">
        <div className="yaxis" aria-hidden="true">
          {[100, 75, 50, 25, 0].map((v) => (
            <span key={v} style={{ top: `${100 - v}%` }}>
              {v}%
            </span>
          ))}
        </div>
        <div className="plot" {...bind}>
          <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Regime probabilities over time">
            {bands.map((b) => (
              <path key={b.key} d={b.d} style={{ fill: regimeColor(b.regime) }} />
            ))}
            {[25, 50, 75].map((v) => (
              <line key={v} x1={0} x2={width} y1={y(v / 100)} y2={y(v / 100)} className="gridline" vectorEffect="non-scaling-stroke" />
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
              />
            ))}
          </svg>
          {index !== null && hovered ? (
            <>
              <div className="cross" style={{ left: `${pct(index, n)}%` }} />
              <Tooltip at={index} n={n}>
                <strong>{monthYear(hovered.date)}</strong>
                {hoveredRows.map(([regime, value]) => (
                  <span key={regime} className="tip-row">
                    <span className="legend-dot" style={{ background: regimeColor(regime) }} />
                    {prettyRegime(regime)}
                    <span className="num">{formatPct(value)}</span>
                  </span>
                ))}
                {hoveredRows.length ? (
                  <span className="tip-foot">Reported: {prettyRegime(hovered.reported_regime)}</span>
                ) : (
                  <span className="tip-foot">No reading this month</span>
                )}
              </Tooltip>
            </>
          ) : null}
        </div>
      </div>
      <div className="plot-grid">
        <span />
        <XAxis points={points} count={5} />
      </div>
      <p className="caption">Band thickness is each regime's monthly probability; the strip beneath is the reported regime.</p>
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

const MINI_Z_MAX = 3.5;

export function MiniLine({ points }: { points: { date?: string; value?: number }[] }) {
  const values = points.filter((p): p is { date?: string; value: number } => typeof p.value === "number");
  const n = values.length;
  const { index, bind } = useHoverIndex(n);
  if (n < 2) {
    return <p className="muted">No data.</p>;
  }
  const width = 200;
  const height = 70;
  const mid = height / 2;
  const x = (i: number) => (i / (n - 1)) * width;
  const y = (v: number) => mid - (Math.max(-MINI_Z_MAX, Math.min(MINI_Z_MAX, v)) / MINI_Z_MAX) * (mid - 3);
  const hovered = index === null ? null : values[index];
  return (
    <div>
      <div className="plot-grid mini">
        <div className="yaxis" aria-hidden="true">
          {[3, 0, -3].map((v) => (
            <span key={v} style={{ top: `${((MINI_Z_MAX - v) / (2 * MINI_Z_MAX)) * 100}%` }}>
              {v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "0"}
            </span>
          ))}
        </div>
        <div className="plot" {...bind}>
          <svg className="miniline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img">
            {[3, -3].map((v) => (
              <line key={v} x1={0} y1={y(v)} x2={width} y2={y(v)} className="gridline" vectorEffect="non-scaling-stroke" />
            ))}
            <line x1={0} y1={mid} x2={width} y2={mid} className="miniline-zero" vectorEffect="non-scaling-stroke" />
            <polyline
              className="miniline-path"
              vectorEffect="non-scaling-stroke"
              points={values.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")}
            />
          </svg>
          {index !== null && hovered ? (
            <>
              <div className="cross" style={{ left: `${pct(index, n)}%` }} />
              <span className="hover-dot" style={{ left: `${pct(index, n)}%`, top: `${(y(hovered.value) / height) * 100}%` }} />
              <Tooltip at={index} n={n}>
                <strong>{monthYear(hovered.date)}</strong>
                <span className="num">
                  z {hovered.value > 0 ? "+" : hovered.value < 0 ? "−" : ""}
                  {Math.abs(hovered.value).toFixed(2)}
                </span>
              </Tooltip>
            </>
          ) : null}
        </div>
      </div>
      <div className="plot-grid mini">
        <span />
        <XAxis points={values} count={3} />
      </div>
    </div>
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
  const { index, bind } = useHoverIndex(n);
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
  const hovered = index === null ? null : points[index];
  const hoveredMonth = hovered?.date?.slice(0, 7) ?? "";
  const inRecession = recessions.some((r) => hoveredMonth >= r.start && hoveredMonth <= r.end);
  return (
    <div>
      <div className="plot" {...bind}>
        <svg className="ribbon" viewBox={`0 0 ${width} 44`} preserveAspectRatio="none" role="img" aria-label="Reported regime by month">
          {runs.map((run, i) => (
            <rect
              key={i}
              x={Math.max((run.start / Math.max(n - 1, 1)) * width - cell / 2, 0)}
              y={0}
              width={Math.min(run.len * cell, width)}
              height={26}
              style={{ fill: regimeColor(run.regime) }}
            />
          ))}
          {recessions.map((r, i) => (
            <rect key={`n${i}`} className="ribbon-nber" x={px(r.start)} y={30} width={Math.max(px(r.end) - px(r.start), 2)} height={6} />
          ))}
        </svg>
        {index !== null && hovered ? (
          <>
            <div className="cross" style={{ left: `${pct(index, n)}%` }} />
            <Tooltip at={index} n={n}>
              <strong>{monthYear(hovered.date)}</strong>
              <span className="tip-row">
                <span className="legend-dot" style={{ background: regimeColor(hovered.reported_regime) }} />
                {prettyRegime(hovered.reported_regime)}
                {typeof hovered.confidence === "number" ? <span className="num">{formatPct(hovered.confidence)}</span> : null}
              </span>
              {inRecession ? <span className="tip-foot">NBER recession</span> : null}
            </Tooltip>
          </>
        ) : null}
      </div>
      <XAxis points={points} count={7} />
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
