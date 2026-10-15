"""MRI Layer 2 (MRI-12) — orchestrates a full shock register build and publishes
`outputs/shock_register.json` / `.md`. The `write-shock-register` CLI command and the
daily step in `daily.py` both call `build_and_write_shock_register`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

from macro_engine.ingest.yahoo_oil import fetch_chart_payload, parse_closed_bars
from macro_engine.news.config import load_news_themes_config
from macro_engine.shocks.config import load_shocks_config
from macro_engine.shocks.narrative import attach_narrative
from macro_engine.shocks.register import build_shock_register_artifact, build_shock_register_history
from macro_engine.storage.duckdb_store import DuckDBStore


def build_and_write_shock_register(
    *,
    config_path: str = "config/shocks.yaml",
    news_themes_config_path: str = "config/news_themes.yaml",
    db_path: str = "data/macro_engine.duckdb",
    output_dir: str = "outputs",
    as_of: str | None = None,
    start_date: str = "1990-01-01",
    end_date: str | None = None,
    persist_history: bool = True,
    attach_news: bool = True,
    mock_ai: bool = False,
    source_run_id: str | None = None,
    fetch_oil_tail: bool = True,
    oil_fetcher: Callable[[str], bytes] = fetch_chart_payload,
) -> tuple[Path, Path]:
    config = load_shocks_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()

    tail_reasons: list[str] = []
    tail_cfg = (config["shocks"].get("oil_shock") or {}).get("daily_tail")
    if tail_cfg and fetch_oil_tail:
        tail_reasons = _refresh_oil_tail(store, tail_cfg["symbol"], oil_fetcher)

    con = duckdb.connect(str(store.db_path), read_only=True)
    try:
        history = build_shock_register_history(con, config, start_date=start_date, end_date=end_date)
    finally:
        con.close()

    if persist_history:
        store.replace_shock_register(history)

    as_of_date = _resolve_as_of(as_of, history)
    built_at = datetime.now(timezone.utc)
    payload = build_shock_register_artifact(
        history, config, as_of_date, built_at=built_at, source_run_id=source_run_id
    )
    payload["reasons"] = payload["reasons"] + tail_reasons

    if attach_news:
        themes_cfg = load_news_themes_config(news_themes_config_path)
        payload = attach_narrative(
            payload,
            news_items=store.read_table("news_items"),
            news_theme_scores=store.read_table("news_theme_scores"),
            news_classifications=store.read_table("news_classifications"),
            shock_theme_map=config.get("narrative_themes", {}),
            known_theme_ids=set(themes_cfg.active_theme_ids),
            as_of=as_of_date,
            source_run_id=source_run_id,
            mock_ai=mock_ai,
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "shock_register.json"
    markdown_path = out_dir / "shock_register.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    markdown_path.write_text(register_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def _refresh_oil_tail(store: DuckDBStore, symbol: str, fetcher: Callable[[str], bytes]) -> list[str]:
    """Fetch and store the newest closed Yahoo bars for the oil tail. Never raises: the
    register is annotation-only, so a failed fetch leaves the stored closes as they were,
    the oil reading falls back to whatever is stored (or FRED alone, flagged stale), and the
    failure is named in the artifact's reasons."""
    try:
        frame = parse_closed_bars(fetcher(symbol), symbol)
        store.upsert_market_observations(frame)
    except Exception as exc:  # noqa: BLE001 -- any fetch/parse failure degrades, loudly
        print(f"shocks: oil tail fetch failed ({type(exc).__name__}: {exc})", flush=True)
        return [f"oil_tail_fetch_failed:{type(exc).__name__}"]
    if frame.empty:
        return ["oil_tail_fetch_empty"]
    return []


def _resolve_as_of(as_of: str | None, history: pd.DataFrame) -> date:
    if as_of is not None:
        return pd.Timestamp(as_of).date()
    if history.empty:
        # No trading calendar could be built (see register.py:_trading_calendar) -- the
        # register still degrades to a valid, dated artifact (every shock `no_data_on_date`)
        # rather than raising; "today" is the only defensible `asof` left to stamp it with.
        return datetime.now(timezone.utc).date()
    return pd.Timestamp(history["date"].max()).date()


def register_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MRI Shock Register (MRI-12)",
        "",
        f"- asof: {payload.get('asof')}",
        f"- built at: {payload.get('built_at')}",
        f"- taxonomy_version: {payload.get('taxonomy_version')}",
        "",
        payload.get("disclaimer", ""),
        "",
        "| shock | value | value date | source | severity | direction | intensity | state | episode direction | peak severity | peak value | onset | age (d) | lag (td) | stale | proxy |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload.get("shocks", []):
        lines.append(
            "| {shock_id} | {value} | {value_date} | {source} | {severity} | {direction} | {intensity} | {state} | {ep_dir} | {peak_sev} | {peak_val} | {onset} | {age} | {lag} | {stale} | {proxy} |".format(
                shock_id=row["shock_id"],
                value="n/a" if row["value"] is None else f"{row['value']:.4f}",
                value_date=row.get("value_date") or "-",
                source=(row.get("value_source") or "-") + (" (provisional)" if row.get("provisional") else ""),
                ep_dir=row.get("episode_direction") or "-",
                peak_sev="-" if row.get("peak_severity") is None else row["peak_severity"],
                peak_val="-" if row.get("peak_value") is None else f"{row['peak_value']:.4f}",
                lag="n/a" if row.get("lag_trading_days") is None else f"{row['lag_trading_days']}/{row.get('max_lag_trading_days')}",
                severity=row["severity"],
                direction=row["direction"],
                intensity="n/a" if row["intensity"] is None else f"{row['intensity']:.3f}",
                state=row["state"],
                onset=row.get("onset_date") or "-",
                age="n/a" if row.get("age_days") is None else row["age_days"],
                stale=row.get("stale_input"),
                proxy=row.get("proxy"),
            )
        )
    lines += ["", "## Risk flags", ""]
    risk_flags = payload.get("risk_flags") or []
    if not risk_flags:
        lines.append("None.")
    for flag in risk_flags:
        lines.append(
            f"- `{flag['id']}` (from `{flag['source_shock']}`): active={flag['active']}. {flag['meaning']}"
        )
    lines += ["", "## Reasons", ""]
    for reason in payload.get("reasons", []):
        lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
