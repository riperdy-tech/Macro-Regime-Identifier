from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.sectors.validation import load_sector_validation_config
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
) -> dict[str, Any]:
    if summary.empty:
        return {
            "valid": False,
            "reason": "no_validation_summary",
            "disclaimer": VALIDATION_DISCLAIMER,
        }
    valid_returns = returns[returns["valid"]].copy() if not returns.empty else pd.DataFrame()
    price_dates = pd.to_datetime(prices["date"], errors="coerce") if not prices.empty else pd.Series(dtype="datetime64[ns]")
    payload = {
        "valid": True,
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
        return f"# Sector ETF Proxy Validation\n\nNo valid validation summary.\n\n{payload['disclaimer']}\n"
    summary = "\n".join(
        "- {horizon}: observations {observation_count}, rank IC {rank_ic}, top-bottom spread {spread}, top hit rate {hit_rate}".format(
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

## Summary

{summary}

{payload["disclaimer"]}
"""


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
