"""E0.4 confirmation prices: daily Yahoo closes for the event market check (MRI_EVENTS_PLAN v2 section 9).

Reuses the oil-tail path (`ingest/yahoo_oil.py`: `fetch_chart_payload`, `parse_closed_bars`, payload sha256,
closed-sessions-only) and the same `market_observations` table, keyed by Yahoo symbol. Nothing here touches
`raw_observations`, so Layer 1 and the v4 risk-free inputs cannot see these rows.

Adjusted close. Yahoo returns `close` (split-adjusted only) and `adjclose` (split- and dividend-adjusted).
Both are stored: `<SYM>` is the close and `<SYM>#adjclose` its sibling series. Which one E2 should use is not
decided here. For the record: moves over a 2-session window straddling an ex-dividend date differ by the
dividend under `close` (a traded price) and not under `adjclose` (a total return); the 504-session robust-SD
baseline is a total-return series only under `adjclose`. Because every dividend or split rescales ALL earlier
`adjclose` values, a stored adjusted history is coherent only if it is re-pulled in full when that happens:
see `history_changed`.

Instruments: base list + the 11 GICS sector ETFs from config/sectors.yaml + caller-supplied named tickers.
Each instrument has its own lag limit. A freshness result is a *report* (`fresh` / `stale` / `missing`), never
a gate: E2 turns `stale` and `missing` into `data_unavailable` rather than silently using an old close.
"""
from __future__ import annotations

import hashlib
import json
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

ADJ_SUFFIX = "#adjclose"
CHANGE_TOLERANCE = 1e-4  # relative; a split or dividend moves close or adj/close by far more than this
LISTING_SLACK_DAYS = 7  # the first stored bar may trail Yahoo's first-trade date by a holiday/weekend


@dataclass(frozen=True)
class ConfirmationConfig:
    base_instruments: tuple[str, ...]
    default_max_lag: int  # weekdays
    max_lag: dict[str, int]  # weekdays
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
        default_max_lag=int(doc["default_max_lag_weekdays"]),
        max_lag={str(k): int(v) for k, v in (doc.get("max_lag_weekdays") or {}).items()},
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


def parse_adjclose_bars(payload: bytes, symbol: str, *, now: datetime) -> pd.DataFrame:
    """Closed-session adjusted closes as `<symbol>#adjclose` rows, through the same parser as the close (so
    the same closed-sessions-only rule). Empty if the payload carries no adjclose block."""
    doc = json.loads(payload)
    result = ((doc.get("chart") or {}).get("result") or [None])[0]
    adj = (((result or {}).get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose")
    if not adj:
        return pd.DataFrame(columns=["series_id", "date", "value", "source", "fetched_at", "payload_sha256"])
    result["indicators"]["quote"] = [{"close": adj}]
    frame = parse_closed_bars(json.dumps(doc).encode(), symbol + ADJ_SUFFIX, now=now)
    frame["payload_sha256"] = hashlib.sha256(payload).hexdigest()
    return frame


def first_trade_date(payload: bytes) -> date | None:
    """Yahoo's first-trade date for the instrument (its listing date as far as Yahoo knows), if present."""
    result = ((json.loads(payload).get("chart") or {}).get("result") or [None])[0] or {}
    meta = result.get("meta") or {}
    stamp = meta.get("firstTradeDate")
    if stamp is None:
        return None
    tz_name = meta.get("exchangeTimezoneName") or "America/New_York"
    return pd.Timestamp(stamp, unit="s", tz="UTC").tz_convert(tz_name).date()


def history_changed(
    db_path: str | Path, symbol: str, close: pd.DataFrame, adj: pd.DataFrame
) -> str | None:
    """Reason a full re-pull is required, else None. Compares the freshly fetched bars with what is stored on
    the dates both cover: a changed close is a split; a changed adjclose/close ratio is a split or a dividend
    (either rescales the whole earlier adjusted history). Stored closes with no stored adjclose at all mean the
    adjusted series was never loaded. Nothing stored yet is not a change."""
    if close.empty:
        return None
    start = min(close["date"])
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT series_id, date, value FROM market_observations WHERE series_id IN (?, ?) AND date >= ?",
            [symbol, symbol + ADJ_SUFFIX, start],
        ).fetchall()
    finally:
        con.close()
    old_close = {pd.Timestamp(d): v for s, d, v in rows if s == symbol}
    old_adj = {pd.Timestamp(d): v for s, d, v in rows if s != symbol}
    if not old_close:
        return None
    new_close = {pd.Timestamp(d): v for d, v in zip(close["date"], close["value"])}
    new_adj = {pd.Timestamp(d): v for d, v in zip(adj["date"], adj["value"])}
    if any(abs(new_close[d] / old_close[d] - 1) > CHANGE_TOLERANCE for d in old_close.keys() & new_close.keys()):
        return "close_changed_on_overlap"  # split
    if not old_adj and new_adj:
        return "adjclose_not_stored"
    for d in old_adj.keys() & new_adj.keys() & old_close.keys() & new_close.keys():
        if abs((new_adj[d] / new_close[d]) / (old_adj[d] / old_close[d]) - 1) > CHANGE_TOLERANCE:
            return "adjclose_ratio_changed_on_overlap"  # dividend or split
    return None


def stored_state(db_path: str | Path, symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per symbol: stored row count, newest and earliest date. Read-only."""
    state: dict[str, dict[str, Any]] = {}
    symbols = list(symbols)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for sym in symbols:
            n, newest, earliest = con.execute(
                "SELECT count(*), max(date), min(date) FROM market_observations "
                "WHERE series_id = ? AND value IS NOT NULL",
                [sym],
            ).fetchone()
            state[sym] = {"rows": int(n), "newest": newest, "earliest": earliest}
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
        range_ = cfg.daily_range
        try:
            payload = fetcher(sym, range_=range_)
            close = parse_closed_bars(payload, sym, now=now)
            adj = parse_adjclose_bars(payload, sym, now=now)
            reason = history_changed(db_path, sym, close, adj)
            listed = first_trade_date(payload)
            earliest = [d for d in (before[sym]["earliest"], min(close["date"]) if not close.empty else None) if d]
            # History is complete once we hold bars back to the listing date; a young instrument (CRWV) is then
            # never re-pulled for 3 years it does not have.
            complete = bool(
                listed and earliest
                and pd.Timestamp(min(earliest)) <= pd.Timestamp(listed) + pd.Timedelta(days=LISTING_SLACK_DAYS)
            )
            if reason is None and before[sym]["rows"] < cfg.min_history_rows and not complete:
                reason = "short_history"
            if reason is not None:
                range_ = cfg.backfill_range
                payload = fetcher(sym, range_=range_)
                close = parse_closed_bars(payload, sym, now=now)
                adj = parse_adjclose_bars(payload, sym, now=now)
            written = store.upsert_market_observations(close)
            store.upsert_market_observations(adj)
            outcomes[sym] = {"range": range_, "rows_fetched": int(written), "error": None, "repull_reason": reason,
                             "history_complete_since_listing": complete}
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the rest
            outcomes[sym] = {"range": range_, "rows_fetched": 0, "error": f"{type(exc).__name__}: {exc}",
                             "repull_reason": None, "history_complete_since_listing": None}

    report = freshness_report(db_path, symbols, cfg, now=now)
    for sym, out in outcomes.items():
        report["instruments"][sym].update(fetch=out)
    report["fetch_errors"] = sorted(s for s, o in outcomes.items() if o["error"])
    report["ok"] = report["ok"] and not report["fetch_errors"]
    return report
