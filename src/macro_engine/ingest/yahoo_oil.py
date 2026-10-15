"""Daily WTI closes from Yahoo's chart endpoint, for the oil shock's provisional tail only.

Operator ruling 2026-10-15: FRED's DCOILWTICO arrives about weekly, and a week of lag is not
acceptable for oil. This module is the one non-FRED numeric input in the engine. It writes
`market_observations` (never `raw_observations`), and only `shocks/register.py` reads it, to
chain daily returns onto the last FRED spot level until FRED publishes.

A bar is kept only once its session has closed (17:00 New York, when the CME electronic session
ends), so a live intraday price is never stored as a close. Every refresh records the sha256 of
the raw payload, per the standing approval for Yahoo daily closes (O5).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SOURCE = "yahoo_chart_v8"
SESSION_CLOSE_HOUR_NY = 17
_HEADERS = {"User-Agent": "Mozilla/5.0 (macro-regime-indicator; oil shock daily tail)"}


def fetch_chart_payload(symbol: str, *, range_: str = "3mo", timeout: float = 20.0) -> bytes:
    response = requests.get(
        CHART_URL.format(symbol=symbol),
        params={"range": range_, "interval": "1d"},
        headers=_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


def parse_closed_bars(payload: bytes, symbol: str, *, now: datetime | None = None) -> pd.DataFrame:
    """`(series_id, date, value, source, fetched_at, payload_sha256)` rows for every bar whose
    session has closed by `now`. Null closes are dropped, never filled."""
    now = now or datetime.now(timezone.utc)
    doc: dict[str, Any] = json.loads(payload)
    result = ((doc.get("chart") or {}).get("result") or [None])[0]
    if not result:
        error = (doc.get("chart") or {}).get("error")
        raise ValueError(f"yahoo chart returned no result for {symbol}: {error}")
    timestamps = result.get("timestamp") or []
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    tz_name = (result.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York"

    now_ny = pd.Timestamp(now).tz_convert("America/New_York")
    digest = hashlib.sha256(payload).hexdigest()
    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        bar_date = pd.Timestamp(ts, unit="s", tz="UTC").tz_convert(tz_name).date()
        session_close = pd.Timestamp(bar_date).tz_localize("America/New_York") + pd.Timedelta(hours=SESSION_CLOSE_HOUR_NY)
        if now_ny < session_close:
            continue
        rows.append(
            {
                "series_id": symbol,
                "date": bar_date,
                "value": float(close),
                "source": SOURCE,
                "fetched_at": now.replace(tzinfo=None) if now.tzinfo else now,
                "payload_sha256": digest,
            }
        )
    frame = pd.DataFrame(rows, columns=["series_id", "date", "value", "source", "fetched_at", "payload_sha256"])
    return frame.drop_duplicates(subset=["series_id", "date"], keep="last")
