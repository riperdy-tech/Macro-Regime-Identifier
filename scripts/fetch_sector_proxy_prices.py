#!/usr/bin/env python3
"""
Refresh the local sector ETF price panel from a public source.

Why this exists: `config/sector_validation.yaml` prefers the `stooq` provider, and CI uses it.
As of 2026-09-20 every stooq endpoint (`stooq.com`, `stooq.pl`, with and without a date range)
returns an HTML JavaScript bot-challenge instead of CSV, so the provider raises rather than
silently degrading. That is the correct failure mode for CI, but it leaves the local panel
unrefreshable — and an unrefreshable panel is worse than useless, because whatever is already
on disk keeps being treated as measured market data.

This script writes the SAME `ticker,date,close` CSV that MRI's existing `csv` provider already
reads (`sector_validation.load_proxy_prices`, `provider: csv`), so no provider code changes and
the committed `provider: stooq` preference is left alone. It is a data-acquisition step, not a
new dependency in the engine.

Source: Yahoo Finance public chart endpoint (no API key). Real market data.

Usage:
    python scripts/fetch_sector_proxy_prices.py                 # write the panel
    python scripts/fetch_sector_proxy_prices.py --start 2015-01-01
    python scripts/fetch_sector_proxy_prices.py --dry-run

Then load it into DuckDB (the anchors read the STORED panel):
    python -m macro_engine.cli ingest-sector-proxy-prices --config <csv-provider config>

Exit code: 0 on success, 1 if any ticker failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from macro_engine.sectors.validation import (  # noqa: E402
    load_sector_validation_config,
    load_yahoo_prices,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default="config/sector_validation.yaml")
    parser.add_argument("--start", default=None, help="override the configured start_date")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = parser.parse_args(argv)

    config = load_sector_validation_config(args.config)
    if args.start:
        config.price_provider.start_date = args.start
    csv_path = Path(config.price_provider.csv_path)

    # One implementation, shared with `provider: yahoo` in the engine — this script exists to
    # write the CSV, not to own a second copy of the fetch.
    config.price_provider.provider = "yahoo"
    panel = load_yahoo_prices(config)

    # Realism gate: refuse to overwrite a good panel with something that cannot be market data.
    # The synthetic file this replaced had 5.1% annualised SPY volatility and no 2020 drawdown.
    wide = panel.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
    returns = wide.pct_change().dropna(how="all")
    print(f"panel: {len(panel)} rows, {panel['ticker'].nunique()} tickers, "
          f"{panel['date'].min()} .. {panel['date'].max()}")
    if config.benchmark_ticker in returns.columns:
        vol = float(returns[config.benchmark_ticker].std() * (252 ** 0.5))
        print(f"{config.benchmark_ticker} realised annualised vol: {vol:.1%}")
        if not 0.08 <= vol <= 0.40:
            print(
                f"REFUSING to write: {config.benchmark_ticker} vol of {vol:.1%} is not a "
                "plausible broad-index figure, so this panel is not market data",
                file=sys.stderr,
            )
            return 1

    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    panel[["ticker", "date", "close"]].to_csv(csv_path, index=False)
    print(f"wrote {csv_path}")
    print(
        "next: set provider: yahoo in config/sector_validation.yaml and run\n"
        "  python -m macro_engine.cli ingest-sector-proxy-prices\n"
        "  (the anchors read the STORED panel, not this CSV directly)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
