#!/usr/bin/env python3
"""
Build a UNIVERSE equity aggregate for the implied cost-of-equity solve.

Why this exists: the cost-of-capital anchor's implied ERP needs aggregate equity cash flows and
aggregate market capitalisation, and MRI holds no equity data. The intended source is an index
aggregate (S&P 500 earnings vs capitalisation). This script produces the next best thing from
the screener corpus, and labels it as what it is.

THE NAME MATTERS. The rate solved here is a **universe-implied** cost of equity, not a
market-implied one. The universe is a screener corpus, not an index. Publishing it under
`market_implied_*` would claim an authority it does not have, so the payload carries
`erp_basis: "universe"` and `market_implied_erp` stays null.

Three filters, each for a stated reason rather than tidiness:

1. **USD reporters only** (`FX is None`). This is a CORRECTNESS filter, not hygiene. The solve
   sums cash flows and compares them to market caps; mixing a TWD or EUR cash flow into a USD
   aggregate is meaningless. The corpus marks some foreign reporters `converted: True` and
   others `unreviewed_currency_mismatch` with conversion explicitly unproven, so no foreign
   reporter's fundamentals can be assumed to be USD.
2. **Share classes collapsed by CIK** (`cik_map.json`). GOOG ($4.04T) and GOOGL ($4.01T) are
   the same company and each figure is the TOTAL issuer capitalisation, so summing them
   double-counts Alphabet by ~$4T. The largest class is kept.
3. **Positive owner earnings only.** `NI + D&A − capex` below zero has no capitalised value to
   aggregate, and a negative "cash flow" would drag the solved rate toward nonsense.

Usage:
    python scripts/build_equity_aggregate.py
    python scripts/build_equity_aggregate.py --data-dir ... --out data/anchors/equity_aggregate.json
    python scripts/build_equity_aggregate.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

sys.path.insert(0, str(_REPO_ROOT / "src"))
from macro_engine import peer_paths  # noqa: E402  peer-repo locations

# Resolved, not hardcoded: see src/macro_engine/peer_paths.py. None when no screener
# checkout is reachable, in which case --data-dir is required.
DEFAULT_DATA_DIR = peer_paths.screener_data_dir()
DEFAULT_OUT = "data/anchors/equity_aggregate.json"

MIN_CONSTITUENTS = 30
MIN_COVERAGE_SHARE = 0.60
MAX_GROWTH = 0.35
MIN_GROWTH = -0.05


def _read_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _owner_earnings(fiscal_years: dict, latest: str):
    record = (fiscal_years or {}).get(latest)
    if not isinstance(record, dict):
        return None
    ni, da, capex = record.get("net_income"), record.get("da"), record.get("capex")
    if not all(isinstance(v, int | float) for v in (ni, da, capex)):
        return None
    return float(ni) + float(da) - float(capex)


def _revenue_cagr(fiscal_years: dict, max_years: int = 6):
    """Observed revenue CAGR over up to `max_years` fiscal years. None when too short."""
    years = sorted(int(y) for y in (fiscal_years or {}) if str(y).isdigit())
    points = []
    for year in years:
        value = (fiscal_years.get(str(year)) or {}).get("revenue")
        if isinstance(value, int | float) and value > 0:
            points.append((year, float(value)))
    if len(points) < 4:
        return None
    (y0, v0), (y1, v1) = points[max(0, len(points) - max_years)], points[-1]
    if y1 <= y0 or v0 <= 0:
        return None
    return (v1 / v0) ** (1 / (y1 - y0)) - 1


def build_aggregate(*, data_dir: Path, as_of: str | None = None):
    cik_map = (_read_json(data_dir / "cik_map.json") or {}).get("map") or {}
    fundamentals = _read_json(data_dir / "fundamentals_history.json")
    fundamentals = fundamentals.get("tickers") or fundamentals

    # ---- Pass 1: every name with a market cap, classified for the USD filter.
    universe: list[dict] = []
    excluded_fx: list[str] = []
    for path in sorted((data_dir / "financials").glob("*.json")):
        ticker = path.stem.upper()
        try:
            fin = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        cap = fin.get("Market_Cap")
        if not isinstance(cap, int | float) or cap <= 0:
            continue
        if fin.get("FX") is not None:
            # Non-USD statement currency. Excluded on correctness grounds, not tidiness: the
            # aggregate sums cash flows against USD market caps.
            excluded_fx.append(ticker)
            continue
        years = fundamentals.get(ticker) or {}
        latest = max(years) if years else None
        if latest is None:
            continue
        cash_flow = _owner_earnings(years, latest)
        growth = _revenue_cagr(years)
        universe.append(
            {
                "ticker": ticker,
                "market_cap": float(cap),
                "cash_flow": cash_flow,
                "growth": growth,
                "fiscal_year": latest,
                "cik": str(cik_map.get(ticker) or ""),
            }
        )

    total_cap_all = sum(item["market_cap"] for item in universe)

    # ---- Pass 2: collapse share classes by CIK (largest class wins).
    by_key: dict[str, dict] = {}
    class_collapsed = 0
    for item in universe:
        key = item["cik"] or f"ticker:{item['ticker']}"
        current = by_key.get(key)
        if current is None:
            by_key[key] = item
        else:
            class_collapsed += 1
            if item["market_cap"] > current["market_cap"]:
                by_key[key] = item
    collapsed = list(by_key.values())
    total_cap_collapsed = sum(item["market_cap"] for item in collapsed)

    # ---- Pass 3: usable constituents.
    constituents = []
    for item in collapsed:
        if item["cash_flow"] is None or item["cash_flow"] <= 0:
            continue
        growth = item["growth"]
        if growth is None:
            continue
        constituents.append(
            {
                "ticker": item["ticker"],
                "cash_flow": round(item["cash_flow"], 2),
                "market_cap": round(item["market_cap"], 2),
                "growth": round(min(max(growth, MIN_GROWTH), MAX_GROWTH), 6),
            }
        )
    constituents.sort(key=lambda c: -c["market_cap"])
    usable_cap = sum(c["market_cap"] for c in constituents)

    # Coverage is measured against the COLLAPSED corpus: the question is what fraction of the
    # universe's capitalisation the aggregate represents, and the pre-collapse figure contains
    # the share-class double-count that made the aggregate wrong in the first place.
    coverage = (usable_cap / total_cap_collapsed) if total_cap_collapsed else 0.0
    stats = {
        "names_with_market_cap": len(universe),
        "excluded_non_usd_reporters": len(excluded_fx),
        "share_classes_collapsed": class_collapsed,
        "corpus_market_cap_usd_t": round(total_cap_all / 1e12, 2),
        "collapsed_market_cap_usd_t": round(total_cap_collapsed / 1e12, 2),
        "constituents": len(constituents),
        "constituent_market_cap_usd_t": round(usable_cap / 1e12, 2),
        "coverage_share": round(coverage, 4),
    }
    aggregate = {
        "asof": as_of,
        "source": (
            "Screener corpus (public/data): market caps from financials/*.json, owner earnings "
            "NI+D&A-capex and revenue CAGR from fundamentals_history.json. US-dollar reporters "
            "only; share classes collapsed by CIK."
        ),
        "basis": "universe",
        "growth": None,  # per-constituent growth is supplied; no uniform rate is assumed
        "coverage_share": round(coverage, 4),
        "constituents": constituents,
        "universe_stats": stats,
    }
    return aggregate, stats, excluded_fx[:10]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not (data_dir / "financials").exists():
        print(f"screener corpus not found at {data_dir}", file=sys.stderr)
        return 1

    aggregate, stats, sample_excluded = build_aggregate(data_dir=data_dir, as_of=args.as_of)
    for key, value in stats.items():
        print(f"  {key:<32} {value}")
    print(f"  excluded non-USD (first 10)      {sample_excluded}")

    if stats["constituents"] < MIN_CONSTITUENTS:
        print(f"refusing to write: only {stats['constituents']} constituents", file=sys.stderr)
        return 1
    if stats["coverage_share"] < MIN_COVERAGE_SHARE:
        print(
            f"refusing to write: coverage {stats['coverage_share']:.1%} is below the "
            f"{MIN_COVERAGE_SHARE:.0%} floor — the aggregate would not represent the universe",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path} ({len(aggregate['constituents'])} constituents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
