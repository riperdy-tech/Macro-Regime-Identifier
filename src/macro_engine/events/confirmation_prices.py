"""E0.4 confirmation prices: daily Yahoo closes for the event market check (MRI_EVENTS_PLAN v2 section 9).

Reuses the oil-tail path (`ingest/yahoo_oil.py`: `fetch_chart_payload`, `parse_closed_bars`, payload sha256,
closed-sessions-only) and the same `market_observations` table, keyed by Yahoo symbol. Nothing here touches
`raw_observations`, so Layer 1 and the v4 risk-free inputs cannot see these rows.

Instruments: base list + the 11 GICS sector ETFs from config/sectors.yaml + caller-supplied named tickers.
Each instrument has its own lag limit. A freshness result is a *report* (`fresh` / `stale` / `missing`), never
a gate: E2 turns `stale` and `missing` into `data_unavailable` rather than silently using an old close.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import duckdb
import numpy as np
import pandas as pd
import yaml

from macro_engine.ingest.yahoo_oil import fetch_chart_payload, parse_closed_bars
from macro_engine.peer_paths import REPO_ROOT
from macro_engine.storage.duckdb_store import DuckDBStore

DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "confirmation_prices.yaml"
DEFAULT_SECTORS_PATH = REPO_ROOT / "config" / "sectors.yaml"

Fetcher = Callable[..., bytes]


@dataclass(frozen=True)
class ConfirmationConfig:
    base_instruments: tuple[str, ...]
    default_max_lag: int
    max_lag: dict[str, int]
    min_history_rows: int
    backfill_range: str
    daily_range: str
    pause_seconds: float

    def lag_limit(self, symbol: str) -> int:
        return int(self.max_lag.get(symbol, self.default_max_lag))


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> ConfirmationConfig:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ConfirmationConfig(
        base_instruments=tuple(str(s) for s in doc["base_instruments"]),
        default_max_lag=int(doc["default_max_lag_trading_days"]),
        max_lag={str(k): int(v) for k, v in (doc.get("max_lag_trading_days") or {}).items()},
        min_history_rows=int(doc["min_history_rows"]),
        backfill_range=str(doc["backfill_range"]),
        daily_range=str(doc["daily_range"]),
        pause_seconds=float(doc["pause_seconds"]),
    )


def sector_etfs(sectors_path: str | Path = DEFAULT_SECTORS_PATH) -> list[str]:
    """Proxy tickers of the parentless (GICS-level) enabled sectors."""
    doc = yaml.safe_load(Path(sectors_path).read_text(encoding="utf-8")) or {}
    return [
        str(s["proxy_ticker"])
        for s in doc.get("sectors", [])
        if s.get("enabled", True) and s.get("proxy_ticker") and not s.get("parent_sector_id")
    ]


def instrument_list(
    cfg: ConfirmationConfig,
    named_tickers: Iterable[str] = (),
    sectors_path: str | Path = DEFAULT_SECTORS_PATH,
) -> list[str]:
    """Base + sector ETFs + named tickers, upper-cased, de-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for sym in [*cfg.base_instruments, *sector_etfs(sectors_path), *named_tickers]:
        sym = str(sym).strip().upper()
        if sym:
            seen.setdefault(sym, None)
    return list(seen)


def latest_closed_session(now: datetime) -> date:
    """Latest weekday whose US close (17:00 New York, as in the oil tail) has passed at `now`."""
    ny = pd.Timestamp(now).tz_convert("America/New_York")
    day = ny.normalize()
    if ny < day + pd.Timedelta(hours=17):
        day -= pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return day.date()


def weekday_lag(newest: date, expected: date) -> int:
    """Weekdays between the newest stored close and the latest closed session (0 = current)."""
    if newest >= expected:
        return 0
    return int(np.busday_count(newest, expected))


def stored_state(db_path: str | Path, symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per symbol: stored row count and newest date. Read-only."""
    state: dict[str, dict[str, Any]] = {}
    symbols = list(symbols)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for sym in symbols:
            n, newest = con.execute(
                "SELECT count(*), max(date) FROM market_observations WHERE series_id = ? AND value IS NOT NULL",
                [sym],
            ).fetchone()
            state[sym] = {"rows": int(n), "newest": newest}
    finally:
        con.close()
    return state


def freshness_report(
    db_path: str | Path,
    symbols: Iterable[str],
    cfg: ConfirmationConfig,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    expected = latest_closed_session(now)
    state = stored_state(db_path, symbols)
    per: dict[str, Any] = {}
    for sym, st in state.items():
        limit = cfg.lag_limit(sym)
        if st["newest"] is None:
            per[sym] = {"status": "missing", "rows": 0, "newest": None, "lag": None, "limit": limit}
            continue
        lag = weekday_lag(pd.Timestamp(st["newest"]).date(), expected)
        per[sym] = {
            "status": "stale" if lag > limit else "fresh",
            "rows": st["rows"],
            "newest": str(pd.Timestamp(st["newest"]).date()),
            "lag": lag,
            "limit": limit,
        }
    bad = sorted(s for s, v in per.items() if v["status"] != "fresh")
    return {"expected_session": str(expected), "ok": not bad, "not_fresh": bad, "instruments": per}


def refresh_confirmation_prices(
    db_path: str | Path,
    named_tickers: Iterable[str] = (),
    *,
    cfg: ConfirmationConfig | None = None,
    fetcher: Fetcher = fetch_chart_payload,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    sectors_path: str | Path = DEFAULT_SECTORS_PATH,
) -> dict[str, Any]:
    """Pull and store closed daily bars for every instrument, then report freshness. One failing symbol is
    recorded and the rest continue; this function does not raise on a fetch/parse error."""
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    symbols = instrument_list(cfg, named_tickers, sectors_path)
    store = DuckDBStore(db_path)
    store.initialize()
    before = stored_state(db_path, symbols)

    outcomes: dict[str, Any] = {}
    for i, sym in enumerate(symbols):
        if i:
            sleep(cfg.pause_seconds)
        range_ = cfg.backfill_range if before[sym]["rows"] < cfg.min_history_rows else cfg.daily_range
        try:
            frame = parse_closed_bars(fetcher(sym, range_=range_), sym, now=now)
            written = store.upsert_market_observations(frame)
            outcomes[sym] = {"range": range_, "rows_fetched": int(written), "error": None}
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the rest
            outcomes[sym] = {"range": range_, "rows_fetched": 0, "error": f"{type(exc).__name__}: {exc}"}

    report = freshness_report(db_path, symbols, cfg, now=now)
    for sym, out in outcomes.items():
        report["instruments"][sym].update(fetch=out)
    report["fetch_errors"] = sorted(s for s, o in outcomes.items() if o["error"])
    report["ok"] = report["ok"] and not report["fetch_errors"]
    return report
