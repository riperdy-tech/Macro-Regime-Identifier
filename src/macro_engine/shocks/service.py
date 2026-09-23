"""MRI Layer 2 (MRI-12) — orchestrates a full shock register build and publishes
`outputs/shock_register.json` / `.md`. The `write-shock-register` CLI command and the
daily step in `daily.py` both call `build_and_write_shock_register`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

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
) -> tuple[Path, Path]:
    config = load_shocks_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()

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
        "| shock | value | severity | direction | intensity | state | onset | age (d) | stale | proxy |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload.get("shocks", []):
        lines.append(
            "| {shock_id} | {value} | {severity} | {direction} | {intensity} | {state} | {onset} | {age} | {stale} | {proxy} |".format(
                shock_id=row["shock_id"],
                value="n/a" if row["value"] is None else f"{row['value']:.4f}",
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
