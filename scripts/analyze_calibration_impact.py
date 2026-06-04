"""One-off, SANDBOXED demonstration: what would force-fitting the (provisional,
untrustworthy) backfill calibration do to sector news scores?

Nothing here changes production: it loads stored data, fits a simple bucket
calibrator from the backfill ledger, recomputes news sector scores with raw vs
calibrated confidence, and writes a side-by-side comparison so you can see the
effect yourself. The valve stays closed.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from macro_engine.news.config import load_news_scoring_config
from macro_engine.news.scoring import build_news_scores
from macro_engine.storage.duckdb_store import DuckDBStore

DB = "data/macro_engine.duckdb"
LEDGER = "outputs/confidence_calibration_ledger_backfill.parquet"
EDGES = [0.0, 0.3, 0.6, 0.8, 1.0]
LABELS = ["0.0-0.3", "0.3-0.6", "0.6-0.8", "0.8-1.0"]
HORIZON_COL = "relative_forward_1m_return"


def fit_bucket_calibrator(ledger: pd.DataFrame) -> dict[str, float]:
    """calibrated_confidence = empirical hit-rate of the bucket the raw
    confidence falls into. (No monotonic constraint - shows the raw signal,
    inversion included.)"""
    df = ledger[ledger["expected_sign"].notna()].copy()
    df[HORIZON_COL] = pd.to_numeric(df[HORIZON_COL], errors="coerce")
    df = df[df[HORIZON_COL].notna()]
    df["signed"] = df["expected_sign"] * df[HORIZON_COL]
    df["hit"] = (df["signed"] > 0).astype(float)
    df["bucket"] = pd.cut(df["confidence"].clip(0, 1), bins=EDGES, labels=LABELS, include_lowest=True)
    rates = df.groupby("bucket", observed=False)["hit"].mean()
    # Fallback 0.5 for empty buckets.
    return {lab: (float(rates[lab]) if lab in rates and pd.notna(rates[lab]) else 0.5) for lab in LABELS}


def calibrate(conf: float, table: dict[str, float]) -> float:
    c = min(1.0, max(0.0, float(conf)))
    for i in range(len(EDGES) - 1):
        if c <= EDGES[i + 1] or i == len(EDGES) - 2:
            return table[LABELS[i]]
    return table[LABELS[-1]]


def main() -> None:
    store = DuckDBStore(DB)
    store.initialize()
    news_items = store.read_table("news_items")
    classifications = store.read_table("news_classifications")
    theme_scores = store.read_table("news_theme_scores")
    sector_impacts = store.read_table("news_sector_impacts")
    config = load_news_scoring_config("config/news_scoring.yaml")

    ledger = pd.read_parquet(LEDGER)
    table = fit_bucket_calibrator(ledger)
    print("Bucket calibrator (raw confidence bucket -> calibrated confidence):")
    for lab in LABELS:
        print(f"  {lab}: {table[lab]:.3f}")

    # RAW run
    raw = build_news_scores(
        news_items=news_items, classifications=classifications,
        theme_scores=theme_scores, sector_impacts=sector_impacts, config=config,
    ).daily_sector_scores

    # CALIBRATED run: replace per-impact confidence with calibrated value
    cal_impacts = sector_impacts.copy()
    cal_impacts["confidence"] = cal_impacts["confidence"].map(lambda c: calibrate(c, table))
    cal = build_news_scores(
        news_items=news_items, classifications=classifications,
        theme_scores=theme_scores, sector_impacts=cal_impacts, config=config,
    ).daily_sector_scores

    if raw.empty or cal.empty:
        print("No daily sector scores produced (need resolvable dated impacts).")
        return

    # Aggregate per sector: mean adjusted_news_score across dates, then rank.
    def agg(df: pd.DataFrame, name: str) -> pd.DataFrame:
        g = (df.groupby("sector_id")["adjusted_news_score"].mean()
             .reset_index().rename(columns={"adjusted_news_score": name}))
        g[f"{name}_rank"] = g[name].rank(ascending=False, method="min").astype(int)
        return g

    comp = agg(raw, "raw").merge(agg(cal, "calibrated"), on="sector_id", how="outer")
    comp["score_delta"] = comp["calibrated"] - comp["raw"]
    comp["rank_delta"] = comp["raw_rank"] - comp["calibrated_rank"]  # + = moved up
    comp = comp.sort_values("raw_rank")

    pd.set_option("display.float_format", lambda v: f"{v:.4f}")
    print("\nSector news score: RAW vs FORCE-FIT CALIBRATED (mean across dates)\n")
    print(comp.to_string(index=False))

    out = Path("outputs/calibration_impact_comparison.md")
    lines = ["# Calibration Impact (SANDBOX - provisional backfill, not promoted)", "",
             "Calibrated confidence = empirical hit-rate of each raw-confidence bucket",
             "(from the provisional backfill ledger). Demonstration only.", "",
             "## Bucket calibrator", "",
             "| Raw bucket | Calibrated confidence |", "| --- | --- |"]
    for lab in LABELS:
        lines.append(f"| {lab} | {table[lab]:.3f} |")
    lines += ["", "## Sector news score: raw vs calibrated", "",
              "| Sector | raw | raw_rank | calibrated | cal_rank | score_delta | rank_delta |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in comp.to_dict(orient="records"):
        lines.append(
            f"| {r['sector_id']} | {r['raw']:.4f} | {int(r['raw_rank'])} | "
            f"{r['calibrated']:.4f} | {int(r['calibrated_rank'])} | "
            f"{r['score_delta']:+.4f} | {int(r['rank_delta']):+d} |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
