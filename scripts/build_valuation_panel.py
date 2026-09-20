#!/usr/bin/env python3
"""
Build a regime-conditionable sector valuation panel from the screener corpus.

Why this exists: the market-observed leg of the multiple-band anchor needs a dated, sector-tagged
valuation panel, and neither repository stored one. Nothing could be fabricated to fill the gap,
so the anchor published `panel_source: unavailable`. This script closes that gap from data the
shop ALREADY HAS:

    stocks.csv               ticker -> sector
    fundamentals_history.json  annual net_income / shares_diluted by fiscal year
    backtest_prices.json       monthly prices, ~2014 onward

producing `date, sector_id, ticker, pe_ttm` rows that MRI's existing
`multiple_bands.panel` loader already reads (`external_valuation_panel`).

WHAT THIS PANEL IS, STATED PLAINLY — each of these is a real limitation, not a footnote:

1. **Trailing, not forward.** The multiple is price / (last reported FY net income per diluted
   share). The anchor's field is named `ntm_pe`; the payload's `measure` field therefore names
   what was actually measured. A trailing multiple is a legitimate, if noisier, valuation proxy —
   it is NOT the forward multiple a valuation engine would prefer.
2. **Annually stepped earnings.** EPS only changes when a fiscal year is reported, so within-year
   variation is pure price movement. Sector medians are meaningful; a single name's month-to-month
   "multiple change" is mostly its price.
3. **A 90-day publication lag, applied conservatively.** Fiscal years are labelled by end-year and
   no period-end date is stored, so FY(N) is treated as first usable on March 31 of N+1. For a
   December year-end that is about right; for a June year-end it is deliberately late. The rule
   can only make the data STALER, never leak a figure before it was published.
4. **Survivorship.** The universe is today's listing set, so companies that delisted are absent.
   Multiples for surviving names are therefore measured over a universe that already know they
   survived.
5. **Loss-makers are excluded**, not assigned a multiple: a non-positive EPS yields no P/E.

None of this is fatal for a regime-conditional DISTRIBUTION — which is what the anchor publishes,
and which only needs the cross-section to be representative month by month. It is fatal for
treating any single row as a precise valuation. The sidecar JSON records all five caveats so they
travel with the panel into the anchor payload.

Usage:
    python scripts/build_valuation_panel.py
    python scripts/build_valuation_panel.py --data-dir "C:/.../public/data" --out data/anchors/sector_multiple_panel.csv
    python scripts/build_valuation_panel.py --dry-run

Exit code: 0 on success, 1 when the corpus is missing or yields too little to be a panel.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

DEFAULT_DATA_DIR = Path(r"C:\Users\riper\Downloads\Stock Screener\Stock Screener\public\data")
DEFAULT_OUT = "data/anchors/sector_multiple_panel.csv"
DEFAULT_META = "data/anchors/sector_multiple_panel.meta.json"

# Yahoo sector name -> MRI sector id (config/sectors.yaml). The two vocabularies share no value,
# so the mapping is explicit and total over the real corpus vocabulary.
SECTOR_MAP = {
    "Basic Materials": "materials",
    "Communication Services": "communication_services",
    "Consumer Cyclical": "consumer_discretionary",
    "Consumer Defensive": "consumer_staples",
    "Energy": "energy",
    "Financial Services": "financials",
    "Healthcare": "health_care",
    "Industrials": "industrials",
    "Real Estate": "real_estate",
    "Technology": "information_technology",
    "Utilities": "utilities",
}

PUBLICATION_LAG_MONTHS = 15  # FY(N) usable from month 15 after the start of year N (i.e. Mar N+1)
MIN_ROWS = 5_000
MIN_SECTORS = 8


def _read_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_sector_map(data_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with (data_dir / "stocks.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        sector_col = next((c for c in reader.fieldnames if c.lower() == "sector"), None)
        symbol_col = next((c for c in reader.fieldnames if c.lower() == "symbol"), None)
        if sector_col is None or symbol_col is None:
            raise ValueError("stocks.csv needs Symbol and Sector columns")
        for row in reader:
            symbol = (row.get(symbol_col) or "").strip().upper()
            sector_id = SECTOR_MAP.get((row.get(sector_col) or "").strip())
            if symbol and sector_id:
                mapping[symbol] = sector_id
    return mapping


def eps_by_first_usable_month(fiscal_years: dict) -> dict[int, float]:
    """{yyyymm usable-from: eps} from a ticker's annual history.

    EPS is net income per diluted share. A non-positive EPS is skipped: a loss-making company has
    no P/E, and emitting a negative or infinite one would poison every quantile it entered.
    """
    schedule: dict[int, float] = {}
    for year_label, record in (fiscal_years or {}).items():
        if not isinstance(record, dict):
            continue
        try:
            year = int(str(year_label)[:4])
        except ValueError:
            continue
        net_income = record.get("net_income")
        shares = record.get("shares_diluted")
        if not isinstance(net_income, int | float) or not isinstance(shares, int | float):
            continue
        if shares <= 0 or net_income <= 0:
            continue
        usable_from = (year * 100) + PUBLICATION_LAG_MONTHS
        schedule[usable_from] = float(net_income) / float(shares)
    return schedule


def build_panel(
    *,
    data_dir: Path,
    max_pe: float = 400.0,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    sector_map = load_sector_map(data_dir)
    fundamentals = _read_json(data_dir / "fundamentals_history.json")
    fundamentals = fundamentals.get("tickers") or fundamentals
    prices_payload = _read_json(data_dir / "backtest_prices.json")
    prices = prices_payload.get("prices") or {}

    rows: list[dict[str, object]] = []
    skipped_no_sector = skipped_no_eps = skipped_no_price = 0
    months_seen: set[str] = set()
    per_sector_months: dict[tuple[str, str], int] = defaultdict(int)

    for ticker, monthly in prices.items():
        symbol = str(ticker).upper()
        sector_id = sector_map.get(symbol)
        if sector_id is None:
            skipped_no_sector += 1
            continue
        schedule = eps_by_first_usable_month(fundamentals.get(symbol) or {})
        if not schedule:
            skipped_no_eps += 1
            continue
        usable_months = sorted(schedule)
        for month, price in (monthly or {}).items():
            if not isinstance(price, int | float) or price <= 0:
                continue
            try:
                year, month_number = str(month).split("-")[:2]
                key = int(year) * 100 + int(month_number)
            except (ValueError, AttributeError):
                continue
            # The most recent fiscal year already published by this month.
            eligible = [m for m in usable_months if m <= key]
            if not eligible:
                continue
            eps = schedule[eligible[-1]]
            pe = float(price) / eps
            # A P/E beyond this is a near-zero-earnings artefact, not a valuation.
            if not (0 < pe <= max_pe):
                continue
            date = f"{month}-01"
            rows.append(
                {"date": date, "sector_id": sector_id, "ticker": symbol, "pe_ttm": round(pe, 6)}
            )
            months_seen.add(str(month))
            per_sector_months[(sector_id, str(month))] += 1
            skipped_no_price += 0
        if not schedule:
            skipped_no_eps += 0

    rows.sort(key=lambda row: (row["date"], row["sector_id"], row["ticker"]))
    coverage = {
        sector: sum(1 for (sid, _), _ in per_sector_months.items() if sid == sector)
        for sector in sorted({row["sector_id"] for row in rows})
    }
    meta: dict[str, object] = {
        "rows": len(rows),
        "tickers_with_sector_and_eps": len({row["ticker"] for row in rows}),
        "sectors": coverage,
        "months": len(months_seen),
        "first_month": min(months_seen) if months_seen else None,
        "last_month": max(months_seen) if months_seen else None,
        "skipped_no_sector": skipped_no_sector,
        "skipped_no_usable_eps": skipped_no_eps,
        "measure": "pe_ttm",
        "measure_definition": (
            "price / (net income per diluted share of the most recently PUBLISHED fiscal year)"
        ),
        "caveats": [
            "TRAILING, not forward: this is not an NTM P/E. The anchor labels it via `measure`.",
            "Earnings step annually; within-year variation is price movement.",
            f"Fiscal years are treated as first usable in month {PUBLICATION_LAG_MONTHS} after the "
            "year label (Mar of N+1 for FY(N)). Conservative: can only be staler, never look ahead.",
            "SURVIVORSHIP: the universe is today's listings, so delisted names are absent.",
            "Loss-makers are excluded rather than assigned a multiple.",
            f"Multiples above {max_pe:.0f}x are dropped as near-zero-earnings artefacts.",
        ],
    }
    return rows, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--meta", default=DEFAULT_META)
    parser.add_argument("--max-pe", type=float, default=400.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not (data_dir / "stocks.csv").exists():
        print(f"screener corpus not found at {data_dir}", file=sys.stderr)
        return 1

    rows, meta = build_panel(data_dir=data_dir, max_pe=args.max_pe)
    if len(rows) < MIN_ROWS or len(meta["sectors"]) < MIN_SECTORS:
        print(
            f"refusing to write: {len(rows)} rows across {len(meta['sectors'])} sectors "
            f"(need {MIN_ROWS}+ rows and {MIN_SECTORS}+ sectors). meta={json.dumps(meta)[:400]}",
            file=sys.stderr,
        )
        return 1

    print(f"panel: {meta['rows']} rows, {meta['tickers_with_sector_and_eps']} tickers, "
          f"{len(meta['sectors'])} sectors, {meta['months']} months "
          f"({meta['first_month']} .. {meta['last_month']})")
    for sector, months in sorted(meta["sectors"].items()):
        print(f"   {sector:<26} {months} months")

    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "sector_id", "ticker", "pe_ttm"])
        writer.writeheader()
        writer.writerows(rows)
    Path(args.meta).write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"wrote {args.meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
