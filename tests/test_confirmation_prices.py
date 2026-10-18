"""E0.4 confirmation prices: instrument list, freshness and lag limits, with a mocked fetcher (no network)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from macro_engine.events import confirmation_prices as cp
from macro_engine.storage.duckdb_store import DuckDBStore

# 2026-10-16 is a Friday. NOW is Saturday 10-17 12:00 UTC: the latest closed session is Friday 10-16.
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _payload(symbol: str, dates: list[str], closes: list[float | None]) -> bytes:
    ts = [int(pd.Timestamp(f"{d} 16:00", tz="America/New_York").timestamp()) for d in dates]
    return json.dumps(
        {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": ts,
                               "indicators": {"quote": [{"close": closes}]}}], "error": None}}
    ).encode()


def _cfg(**over) -> cp.ConfirmationConfig:
    base = dict(base_instruments=("SPY", "XLE", "KBE"), default_max_lag=3, max_lag={"SPY": 2}, min_history_rows=5,
                backfill_range="3y", daily_range="3mo", pause_seconds=0.0)
    base.update(over)
    return cp.ConfirmationConfig(**base)


def _sectors(tmp_path: Path) -> Path:
    f = tmp_path / "sectors.yaml"
    f.write_text(
        "sectors:\n"
        "  - {sector_id: energy, proxy_ticker: XLE, enabled: true}\n"
        "  - {sector_id: technology, proxy_ticker: XLK, enabled: true}\n"
        "  - {sector_id: off, proxy_ticker: XLZ, enabled: false}\n"
        "  - {sector_id: semis, proxy_ticker: SMH, enabled: true, parent_sector_id: technology}\n",
        encoding="utf-8",
    )
    return f


def test_instrument_list_dedupes_and_excludes_subindustries_and_disabled(tmp_path):
    out = cp.instrument_list(_cfg(), ["crwv", "SPY", " dell "], _sectors(tmp_path))
    assert out == ["SPY", "XLE", "KBE", "XLK", "CRWV", "DELL"]


def test_real_config_has_eleven_sector_etfs_plus_base():
    assert len(cp.sector_etfs()) == 11
    syms = cp.instrument_list(cp.load_config())
    assert {"SPY", "XLE", "KBE", "XLK", "XLC"} <= set(syms)
    assert len(syms) == len(set(syms)) == 13  # SPY, KBE + 11 sectors (XLE is in both lists)


@pytest.mark.parametrize(
    "now_utc,expected",
    [
        (datetime(2026, 10, 17, 12, 0, tzinfo=UTC), date(2026, 10, 16)),  # Saturday -> Friday
        (datetime(2026, 10, 19, 12, 0, tzinfo=UTC), date(2026, 10, 16)),  # Monday morning NY -> Friday
        (datetime(2026, 10, 19, 22, 30, tzinfo=UTC), date(2026, 10, 19)),  # Monday after 17:00 NY -> Monday
        (datetime(2026, 10, 20, 0, 30, tzinfo=UTC), date(2026, 10, 19)),  # 20:30 NY Monday
    ],
)
def test_latest_closed_session(now_utc, expected):
    assert cp.latest_closed_session(now_utc) == expected


def test_weekday_lag_zero_is_a_value_and_spans_weekend():
    assert cp.weekday_lag(date(2026, 10, 16), date(2026, 10, 16)) == 0
    assert cp.weekday_lag(date(2026, 10, 15), date(2026, 10, 16)) == 1
    assert cp.weekday_lag(date(2026, 10, 16), date(2026, 10, 19)) == 1  # Fri -> Mon is one weekday


def _fetcher(by_symbol: dict[str, bytes | Exception], calls: list):
    def fetch(symbol: str, *, range_: str = "3mo", timeout: float = 20.0) -> bytes:
        calls.append((symbol, range_))
        r = by_symbol[symbol]
        if isinstance(r, Exception):
            raise r
        return r

    return fetch


def test_refresh_stores_closes_flags_stale_and_missing_and_continues_after_error(tmp_path):
    db = tmp_path / "s.duckdb"
    calls: list = []
    fetch = _fetcher(
        {
            "SPY": _payload("SPY", ["2026-10-15", "2026-10-16"], [600.0, 601.5]),       # fresh (lag 0)
            "XLE": _payload("XLE", ["2026-10-13", "2026-10-14"], [90.0, 91.0]),           # lag 2, limit 2 -> fresh
            "KBE": _payload("KBE", ["2026-10-08"], [55.0]),                              # lag 6 > 2 -> stale
            "XLK": RuntimeError("yahoo down"),                                           # fetch error
            "CRWV": _payload("CRWV", [], []),                                            # empty -> missing
        },
        calls,
    )
    rep = cp.refresh_confirmation_prices(
        db, ["CRWV"], cfg=_cfg(max_lag={"SPY": 2, "XLE": 2, "KBE": 2}), fetcher=fetch, now=NOW,
        sleep=lambda s: None, sectors_path=_sectors(tmp_path),
    )
    inst = rep["instruments"]
    assert inst["SPY"]["status"] == "fresh" and inst["SPY"]["lag"] == 0
    assert inst["XLE"]["status"] == "fresh" and inst["XLE"]["lag"] == 2
    assert inst["KBE"]["status"] == "stale" and inst["KBE"]["lag"] == 6
    assert inst["XLK"]["status"] == "missing" and "yahoo down" in inst["XLK"]["fetch"]["error"]
    assert inst["CRWV"]["status"] == "missing"
    assert rep["ok"] is False and rep["fetch_errors"] == ["XLK"]
    assert rep["expected_session"] == "2026-10-16"
    assert [c[0] for c in calls] == ["SPY", "XLE", "KBE", "XLK", "CRWV"]  # the error did not stop the rest

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT series_id, count(*) FROM market_observations GROUP BY 1 ORDER BY 1").fetchall()
    assert rows == [("KBE", 1), ("SPY", 2), ("XLE", 2)]
    assert con.execute("SELECT count(*) FROM raw_observations").fetchone()[0] == 0  # never touches FRED rows
    con.close()


def test_per_instrument_lag_limit_decides_the_status(tmp_path):
    db = tmp_path / "s.duckdb"
    store = DuckDBStore(db)
    store.initialize()
    frame = pd.DataFrame({"series_id": ["AAA", "BBB"], "date": [date(2026, 10, 14)] * 2, "value": [1.0, 2.0],
                          "source": "t", "fetched_at": datetime(2026, 10, 15), "payload_sha256": "x"})
    store.upsert_market_observations(frame)
    cfg = _cfg(max_lag={"AAA": 1, "BBB": 5})
    rep = cp.freshness_report(db, ["AAA", "BBB"], cfg, now=NOW)  # both 2 weekdays behind
    assert rep["instruments"]["AAA"]["status"] == "stale"
    assert rep["instruments"]["BBB"]["status"] == "fresh"
    assert rep["not_fresh"] == ["AAA"] and rep["ok"] is False


def test_history_triggers_backfill_range_only_when_short(tmp_path):
    db = tmp_path / "s.duckdb"
    store = DuckDBStore(db)
    store.initialize()
    dates = pd.bdate_range("2026-01-01", periods=6)
    store.upsert_market_observations(pd.DataFrame({
        "series_id": "SPY", "date": [d.date() for d in dates], "value": 1.0, "source": "t",
        "fetched_at": datetime(2026, 10, 15), "payload_sha256": "x"}))
    calls: list = []
    fetch = _fetcher({s: _payload(s, ["2026-10-16"], [1.0]) for s in ("SPY", "XLE", "KBE")}, calls)
    cp.refresh_confirmation_prices(db, cfg=_cfg(min_history_rows=5), fetcher=fetch, now=NOW,
                                   sleep=lambda s: None, sectors_path=_sectors(tmp_path))
    ranges = {sym: rng for sym, rng in calls}
    assert ranges["SPY"] == "3mo"      # 6 stored rows >= 5
    assert ranges["XLE"] == "3y"       # nothing stored yet: backfill


def test_intraday_bar_is_not_stored_as_a_close(tmp_path):
    db = tmp_path / "s.duckdb"
    intraday_now = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)  # 14:00 New York on Friday: session still open
    fetch = _fetcher({s: _payload(s, ["2026-10-16"], [10.0]) for s in ("SPY", "XLE", "KBE")}, [])
    cp.refresh_confirmation_prices(db, cfg=_cfg(), fetcher=fetch, now=intraday_now, sleep=lambda s: None,
                                   sectors_path=_sectors(tmp_path))
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT count(*) FROM market_observations WHERE series_id='SPY'").fetchone()[0] == 0
    con.close()
