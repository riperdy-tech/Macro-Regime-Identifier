from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.sectors.validation import (
    assess_price_panel,
    load_sector_validation_config,
)
from macro_engine.storage.duckdb_store import DuckDBStore

VALIDATION_DISCLAIMER = (
    "This is a diagnostic validation using sector ETF proxies. It is not investment "
    "advice, market action guidance, execution guidance, or instructions for changing "
    "holdings. Proxy tickers are validation references only. Cost, slippage, and "
    "execution constraints are not modeled."
)


def write_sector_validation_report(
    *,
    config_path: str | Path = "config/sector_validation.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_sector_validation_config(config_path)
    store = DuckDBStore(db_path)
    payload = build_sector_validation_report(
        returns=store.read_table("sector_validation_returns"),
        summary=store.read_table("sector_validation_summary"),
        prices=store.read_table("sector_proxy_prices"),
        benchmark_ticker=config.benchmark_ticker,
    )
    markdown = sector_validation_markdown(payload)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sector_validation.json"
    markdown_path = output_dir / "sector_validation.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_sector_validation_report(
    *,
    returns: pd.DataFrame,
    summary: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark_ticker: str = "SPY",
) -> dict[str, Any]:
    """Assemble the validation report, gated on the price panel being market data.

    The gate is the point of this function. Every number below -- rank IC, top-bottom spread,
    hit rate -- is a statement about how sector scores related to REALISED returns. Computed
    from a generated panel those statistics are not weak evidence, they are fabricated
    evidence wearing the same clothes as measured evidence, and the file is a required
    dashboard artifact so its mere existence reads as `data_status: complete`.

    So a panel that fails falsification is reported, with its measurements, as NOT valid.
    The file still exists and `data_status` is unaffected; the payload simply stops asserting
    a result it cannot support. RS2's `mos_cut()` returning None is the same discipline.
    """
    if summary.empty:
        return {
            "valid": False,
            "reason": "no_validation_summary",
            "disclaimer": VALIDATION_DISCLAIMER,
        }
    panel = assess_price_panel(prices, benchmark_ticker=benchmark_ticker)
    valid_returns = returns[returns["valid"]].copy() if not returns.empty else pd.DataFrame()
    price_dates = pd.to_datetime(prices["date"], errors="coerce") if not prices.empty else pd.Series(dtype="datetime64[ns]")
    payload = {
        "valid": panel.market_observed,
        "reason": None if panel.market_observed else "unverified_price_provenance",
        "price_panel": panel.as_dict(),
        "price_start_date": None if price_dates.empty else str(price_dates.min().date()),
        "price_end_date": None if price_dates.empty else str(price_dates.max().date()),
        "score_start_date": None
        if valid_returns.empty
        else str(pd.to_datetime(valid_returns["score_date"]).min().date()),
        "score_end_date": None
        if valid_returns.empty
        else str(pd.to_datetime(valid_returns["score_date"]).max().date()),
        "observation_count": int(len(valid_returns)),
        "summary": summary.to_dict(orient="records"),
        "invalid_return_count": int(len(returns) - len(valid_returns)) if not returns.empty else 0,
        "disclaimer": VALIDATION_DISCLAIMER,
    }
    return _json_safe(payload)


def sector_validation_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        panel = payload.get("price_panel") or {}
        reasons = "\n".join(f"- {reason}" for reason in panel.get("reasons") or []) or "- (none recorded)"
        return f"""# Sector ETF Proxy Validation

NOT PUBLISHED AS A RESULT: {payload.get("reason") or "no valid validation summary"}

The validation statistics were computed, but on a price panel that failed falsification as
market data, so they are withheld rather than asserted.

Panel checks:
{_checks_block(panel.get("checks") or {})}

Reasons:
{reasons}

{payload["disclaimer"]}
"""
    summary = "\n".join(
        # C1 (MRI_S1_APPROVAL.md S6): one row per (cross_section, horizon) now, not one per
        # horizon -- name the cross-section so gics_11/subindustry_6/pooled_17 rows for the
        # same horizon are not shown as unlabeled duplicates.
        "- {cross_section} {horizon}: observations {observation_count}, rank IC {rank_ic}, top-bottom spread {spread}, top hit rate {hit_rate}".format(
            cross_section=row.get("cross_section") or "pooled_17",
            horizon=row["horizon"],
            observation_count=row["observation_count"],
            rank_ic=_fmt(row["rank_ic_spearman"]),
            spread=_fmt(row["top_minus_bottom_spread"]),
            hit_rate=_fmt(row["hit_rate_top_positive"]),
        )
        for row in payload["summary"]
    )
    return f"""# Sector ETF Proxy Validation

Mode: diagnostic validation, not an implementable performance test
Price date range: {payload["price_start_date"]} to {payload["price_end_date"]}
Score date range: {payload["score_start_date"]} to {payload["score_end_date"]}
Valid observations: {payload["observation_count"]}
Invalid return rows: {payload["invalid_return_count"]}
Price panel: {_checks_block(payload.get("price_panel", {}).get("checks") or {})}

## Summary

{summary}

{payload["disclaimer"]}
"""


def _checks_block(checks: dict[str, Any]) -> str:
    if not checks:
        return "  (none)"
    return "\n".join(f"  {key}: {value}" for key, value in sorted(checks.items()))


def _fmt(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.4f}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
