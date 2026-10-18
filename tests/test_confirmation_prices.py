"""E0.4 confirmation prices: instrument list, freshness and lag limits, with a mocked fetcher (no network)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from macro_engine.events import confirmation_prices as cp
from macro_engine.storage.duckdb_store import DuckDBStore

# 2026-10-16 is a Friday. NOW is Saturday 10-17 12:00 UTC: the latest closed session is Friday 10-16.
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _payload(symbol: str, dates: list[str], closes: list[float | None], adj: list[float | None] | None = None,
             first_trade: str | None = None) -> bytes:
    ts = [int(pd.Timestamp(f"{d} 16:00", tz="America/New_York").timestamp()) for d in dates]
    meta = {"exchangeTimezoneName": "America/New_York"}
    if first_trade:
        meta["firstTradeDate"] = int(pd.Timestamp(f"{first_trade} 09:30", tz="America/New_York").timestamp())
    indicators = {"quote": [{"close": closes}]}
    if adj is not None:
        indicators["adjclose"] = [{"adjclose": adj}]
    return json.dumps(
        {"chart": {"result": [{"meta": meta, "timestamp": ts, "indicators": indicators}], "error": None}}
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
    # the error did not stop the rest (empty stores with no listing date also trigger a backfill call: 2 per name)
    assert [c[0] for c in dict.fromkeys(c[0] for c in calls).items()] == ["SPY", "XLE", "KBE", "XLK", "CRWV"]

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


def test_lag_limits_are_weekday_keys():
    cfg = cp.load_config()
    assert cfg.default_max_lag == 3 and cfg.lag_limit("SPY") == 2
    keys = set(yaml.safe_load(cp.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
    assert {"default_max_lag_weekdays", "max_lag_weekdays"} <= keys
    assert not [k for k in keys if "trading_days" in k]


def _refresh_one(db, payload_by_range, calls, *, min_rows=5, sectors=None, tmp=None):
    def fetch(symbol, *, range_="3mo", timeout=20.0):
        calls.append((symbol, range_))
        return payload_by_range[range_]

    cfg = _cfg(base_instruments=("AAA",), min_history_rows=min_rows)
    return cp.refresh_confirmation_prices(db, cfg=cfg, fetcher=fetch, now=NOW, sleep=lambda s: None,
                                          sectors_path=sectors)


def _no_sectors(tmp_path):
    f = tmp_path / "none.yaml"
    f.write_text("sectors: []" + chr(10), encoding="utf-8")
    return f


def _stored(db, series):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return dict(con.execute("SELECT date, value FROM market_observations WHERE series_id = ?", [series]).fetchall())
    finally:
        con.close()


DAYS = ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def test_adjusted_close_is_stored_as_a_sibling_series(tmp_path):
    db, calls = tmp_path / "s.duckdb", []
    p = _payload("AAA", DAYS, [100, 101, 102, 103], adj=[99.0, 100.0, 101.0, 102.0], first_trade="2020-01-02")
    _refresh_one(db, {"3mo": p, "3y": p}, calls, min_rows=2, sectors=_no_sectors(tmp_path))
    assert _stored(db, "AAA")[date(2026, 10, 16)] == 103.0
    assert _stored(db, "AAA#adjclose")[date(2026, 10, 16)] == 102.0


def test_split_forces_a_full_repull(tmp_path):
    db, calls = tmp_path / "s.duckdb", []
    sec = _no_sectors(tmp_path)
    old = _payload("AAA", DAYS[:3], [100, 101, 102], adj=[100, 101, 102], first_trade="2020-01-02")
    _refresh_one(db, {"3mo": old, "3y": old}, calls, min_rows=2, sectors=sec)
    calls.clear()
    # 2:1 split: Yahoo restates every earlier close; the stored ones no longer match the overlap
    split = _payload("AAA", DAYS, [50, 50.5, 51, 51.5], adj=[50, 50.5, 51, 51.5], first_trade="2020-01-02")
    full = _payload("AAA", ["2026-10-12", *DAYS], [49.5, 50, 50.5, 51, 51.5], adj=[49.5, 50, 50.5, 51, 51.5],
                    first_trade="2020-01-02")
    rep = _refresh_one(db, {"3mo": split, "3y": full}, calls, min_rows=2, sectors=sec)
    assert calls == [("AAA", "3mo"), ("AAA", "3y")]
    assert rep["instruments"]["AAA"]["fetch"]["repull_reason"] == "close_changed_on_overlap"
    assert _stored(db, "AAA")[date(2026, 10, 13)] == 50.0 and _stored(db, "AAA")[date(2026, 10, 12)] == 49.5


def test_dividend_ratio_change_forces_a_full_repull_but_unchanged_history_does_not(tmp_path):
    db, calls = tmp_path / "s.duckdb", []
    sec = _no_sectors(tmp_path)
    first = _payload("AAA", DAYS[:3], [100, 101, 102], adj=[98.0, 99.0, 100.0], first_trade="2020-01-02")
    _refresh_one(db, {"3mo": first, "3y": first}, calls, min_rows=2, sectors=sec)
    calls.clear()
    same = _payload("AAA", DAYS, [100, 101, 102, 103], adj=[98.0, 99.0, 100.0, 101.0], first_trade="2020-01-02")
    _refresh_one(db, {"3mo": same, "3y": same}, calls, min_rows=2, sectors=sec)
    assert calls == [("AAA", "3mo")]  # same close, same adj/close ratio: nothing rescaled, no repull
    calls.clear()
    divd = _payload("AAA", DAYS, [100, 101, 102, 103], adj=[97.0, 98.0, 99.0, 100.0], first_trade="2020-01-02")
    rep = _refresh_one(db, {"3mo": divd, "3y": divd}, calls, min_rows=2, sectors=sec)
    assert calls == [("AAA", "3mo"), ("AAA", "3y")]
    assert rep["instruments"]["AAA"]["fetch"]["repull_reason"] == "adjclose_ratio_changed_on_overlap"


def test_young_instrument_with_complete_history_since_listing_is_not_repulled(tmp_path):
    db, calls = tmp_path / "s.duckdb", []
    sec = _no_sectors(tmp_path)
    p = _payload("AAA", DAYS, [10, 11, 12, 13], adj=[10, 11, 12, 13], first_trade="2026-10-13")  # listed this week
    rep = _refresh_one(db, {"3mo": p, "3y": p}, calls, min_rows=520, sectors=sec)
    assert calls == [("AAA", "3mo")]  # 4 rows << 520, but those are all the rows there are
    assert rep["instruments"]["AAA"]["fetch"]["history_complete_since_listing"] is True
    calls.clear()
    _refresh_one(db, {"3mo": p, "3y": p}, calls, min_rows=520, sectors=sec)
    assert calls == [("AAA", "3mo")]  # and the next run neither


def test_short_history_without_a_listing_explanation_is_backfilled(tmp_path):
    db, calls = tmp_path / "s.duckdb", []
    p = _payload("AAA", DAYS, [10, 11, 12, 13], adj=[10, 11, 12, 13], first_trade="2018-05-01")
    rep = _refresh_one(db, {"3mo": p, "3y": p}, calls, min_rows=520, sectors=_no_sectors(tmp_path))
    assert calls == [("AAA", "3mo"), ("AAA", "3y")]
    assert rep["instruments"]["AAA"]["fetch"]["repull_reason"] == "short_history"
