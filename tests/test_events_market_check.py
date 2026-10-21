"""E2 market response (plan v2 §6, erratum v2.1, rulings 2026-10-22). Synthetic series in a temporary store only."""
from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine.events.market_check import (
    ALIGNED, DISPLAY_ONLY, MIXED, NO_SIGNAL, NOT_SCORED, OPPOSED, PENDING, RECORDED, UNAVAILABLE,
    MarketResponder, attach_market_response, load_e2_config, reference_index,
)
from macro_engine.storage.duckdb_store import DuckDBStore

CFG = load_e2_config()
DAYS = pd.bdate_range("2023-01-02", "2026-10-16")
ONSET_DAY = pd.Timestamp("2026-10-08")              # a Thursday, after the close below
ONSET = datetime(2026, 10, 8, 21, 0, tzinfo=timezone.utc)   # 17:00 New York: after that day's 16:00 close
AS_OF = date(2026, 10, 16)


def _noise(n, sd, seed):
    return np.random.default_rng(seed).normal(0.0, sd, n).cumsum()


def _step_after(days, base, jump, when=ONSET_DAY):
    out = base.copy()
    out[days > when] += jump
    return out


def _fred_rows(series_id, days, values):
    d = pd.DatetimeIndex(days)
    return pd.DataFrame({"series_id": series_id, "date": d, "value": values, "realtime_start": d, "realtime_end": d,
                         "source": "FRED", "fetched_at": pd.Timestamp("2026-10-17"), "frequency": "daily", "units": "pct"})


def _market_rows(series_id, days, values):
    return pd.DataFrame({"series_id": series_id, "date": pd.DatetimeIndex(days).date, "value": values,
                         "source": "test", "fetched_at": pd.Timestamp("2026-10-17"), "payload_sha256": "0" * 64})


@pytest.fixture()
def con(tmp_path):
    db = tmp_path / "e2.duckdb"
    store = DuckDBStore(db)
    store.initialize()
    n = len(DAYS)
    fred = [
        _fred_rows("DGS2", DAYS, 4.0 + _step_after(DAYS, _noise(n, 0.03, 1), 0.60)),            # +60 bp after onset
        _fred_rows("T10YIE", DAYS, 2.3 + _noise(n, 0.02, 2)),
        _fred_rows("VIXCLS", DAYS, 16.0 * np.exp(_noise(n, 0.03, 3))),
        _fred_rows("BAMLH0A0HYM2", DAYS, 3.0 + _noise(n, 0.02, 4)),
        _fred_rows("BAA10Y", DAYS, 1.6 + _noise(n, 0.02, 5)),
        _fred_rows("T10Y2Y", DAYS, np.full(n, 0.4)),                                            # constant: MAD = 0
    ]
    oil_days = DAYS[DAYS <= pd.Timestamp("2026-10-07")]                                         # FRED oil a week behind
    fred.append(_fred_rows("DCOILWTICO", oil_days, 80.0 * np.exp(_noise(len(oil_days), 0.02, 6))))
    spy = 500.0 * np.exp(_step_after(DAYS, _noise(n, 0.01, 7), 0.15))                           # SPY UP 15%: opposes tightening
    xlk = 200.0 * np.exp(_noise(n, 0.01, 8))
    crwv = 100.0 * np.exp(_step_after(DAYS, _noise(n, 0.015, 9), -0.30))                        # CRWV -30% vs XLK
    market = [_market_rows("SPY", DAYS, spy), _market_rows("XLK", DAYS, xlk), _market_rows("CRWV", DAYS, crwv)]
    cl_days = pd.bdate_range("2026-09-01", "2026-10-16")
    market.append(_market_rows("CL=F", cl_days, 75.0 * np.exp(_step_after(cl_days, _noise(len(cl_days), 0.01, 10), 0.2))))
    c = duckdb.connect(str(db))
    c.register("f", pd.concat(fred, ignore_index=True))
    c.execute("INSERT INTO raw_observations SELECT * FROM f")
    store_frame = pd.concat(market, ignore_index=True)
    c.register("m", store_frame)
    c.execute("INSERT INTO market_observations SELECT series_id, date, value, source, fetched_at, payload_sha256 FROM m")
    yield c
    c.close()


def _cand(cid="evt_a", themes=(("monetary_tightening", "positive"),), tickers=(), onset=ONSET):
    return {"candidate_id": cid, "onset": None if onset is None else onset.isoformat(),
            "themes": [{"theme_id": t, "direction": d} for t, d in themes],
            "entities_resolved": [{"key": tk, "tickers": [tk]} for tk in tickers]}


def _responder(con, **kw):
    return MarketResponder(con, AS_OF, stock_sectors={"CRWV": "Technology", "ODD": "Unknown"}, **kw)


def _theme(resp, theme_id):
    return next(c for c in resp["checks"] if c["theme_id"] == theme_id)


def test_reference_close_is_the_last_close_at_or_before_the_onset_instant():
    levels = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2026-10-07", "2026-10-08", "2026-10-09"]))
    after_close = datetime(2026, 10, 8, 21, 0, tzinfo=timezone.utc)     # 17:00 New York
    in_session = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)      # 11:00 New York
    assert levels.index[reference_index(levels, after_close, CFG)] == pd.Timestamp("2026-10-08")
    assert levels.index[reference_index(levels, in_session, CFG)] == pd.Timestamp("2026-10-07")
    assert reference_index(levels, datetime(2026, 10, 1, tzinfo=timezone.utc), CFG) is None


def test_mixed_when_the_primary_is_aligned_and_the_secondary_opposed(con):
    resp = _responder(con).respond(_cand())
    t = _theme(resp, "monetary_tightening")
    rates, equity = t["families"]
    assert rates["instrument"] == "DGS2" and rates["result"] == ALIGNED
    assert equity["instrument"] == "SPY" and equity["result"] == OPPOSED   # SPY rose; tightening expects it to fall
    assert t["result"] == MIXED


def test_easing_reads_the_same_rates_move_as_opposed(con):
    resp = _responder(con).respond(_cand(themes=(("monetary_easing", "positive"),)))
    t = _theme(resp, "monetary_easing")
    assert t["families"][0]["result"] == OPPOSED and t["families"][1]["result"] == ALIGNED
    assert t["result"] == MIXED


def test_quiet_primary_gives_no_signal(con):
    resp = _responder(con).respond(_cand(themes=(("inflation_pressure", "positive"),)))
    t = _theme(resp, "inflation_pressure")
    assert t["families"][0]["result"] == NO_SIGNAL
    assert t["families"][1]["result"] == ALIGNED          # DGS2 is the secondary family here and it did move up
    assert t["result"] == NO_SIGNAL                         # one aligned secondary alone is not enough


def test_constant_series_is_data_unavailable_mad_zero(con):
    t = _theme(_responder(con).respond(_cand(themes=(("yield_curve_steepening", "positive"),))), "yield_curve_steepening")
    assert t["result"] == UNAVAILABLE and t["reason"] == "primary_mad_zero"


def test_window_not_complete_is_pending_and_old_data_is_stale(con):
    late = datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc)     # one observation after P_ref exists, k = 2 needs two
    t = _theme(_responder(con).respond(_cand(themes=(("monetary_tightening", "positive"),), onset=late)),
               "monetary_tightening")
    assert t["result"] == PENDING
    stale = MarketResponder(con, date(2026, 10, 30), stock_sectors={})
    t2 = _theme(stale.respond(_cand(onset=late)), "monetary_tightening")
    assert t2["result"] == UNAVAILABLE and t2["reason"] == "primary_stale_series"


def test_short_history_is_data_unavailable(con):
    early = datetime(2023, 3, 1, 21, 0, tzinfo=timezone.utc)
    t = _theme(_responder(con).respond(_cand(onset=early)), "monetary_tightening")
    assert t["result"] == UNAVAILABLE and t["reason"] == "primary_insufficient_baseline"


def test_non_positive_direction_is_not_scored_and_display_only_themes_say_so(con):
    resp = _responder(con).respond(_cand(themes=(("monetary_tightening", "negative"), ("growth_slowdown", "positive"))))
    assert _theme(resp, "monetary_tightening")["result"] == NOT_SCORED
    assert _theme(resp, "monetary_tightening")["reason"] == "direction_negative"
    assert _theme(resp, "growth_slowdown")["result"] == DISPLAY_ONLY


def test_same_family_instrument_is_recorded_not_counted(con):
    t = _theme(_responder(con).respond(_cand(themes=(("credit_stress", "positive"),))), "credit_stress")
    primary = t["families"][0]
    assert primary["instrument"] == "BAMLH0A0HYM2"
    assert [e["instrument"] for e in primary["same_family_recorded"]] == ["BAA10Y"]
    assert all(e["counted"] is False for e in primary["same_family_recorded"])
    assert len(t["families"]) == 2   # credit + vol; BAA10Y is not a third family


def test_oil_past_fred_uses_the_chained_tail_and_is_provisional(con):
    t = _theme(_responder(con).respond(_cand(themes=(("energy_supply_shock", "positive"),))), "energy_supply_shock")
    oil = t["families"][0]
    assert oil["instrument"] == "WTI" and oil["provisional"] is True
    assert oil["result"] == ALIGNED
    assert t["provisional"] is True


def test_company_check_is_an_equity_reaction_only(con):
    r = _responder(con)
    stress = r.respond(_cand(themes=(("credit_stress", "positive"),), tickers=("CRWV",)))["company_checks"][0]
    assert stress["recorded_as"] == "equity_reaction_only" and stress["bond_data"] == "unavailable"
    assert stress["benchmark"] == "XLK" and stress["expected_sign"] == -1 and stress["result"] == ALIGNED
    plain = r.respond(_cand(cid="evt_b", themes=(("geopolitical_risk", "positive"),), tickers=("CRWV",)))
    assert plain["company_checks"][0]["result"] == RECORDED and plain["company_checks"][0]["expected_sign"] is None
    odd = r.respond(_cand(cid="evt_c", tickers=("ODD",)))["company_checks"][0]
    assert odd["result"] == UNAVAILABLE and odd["reason"] == "no_sector_benchmark"


def test_missing_onset_is_said_not_guessed(con):
    resp = _responder(con).respond(_cand(onset=None))
    assert resp["checks"] == [] and resp["reason"] == "published_at_missing"


def test_candidates_sharing_a_key_share_one_check(con):
    doc = {"candidates": [_cand("evt_a"), _cand("evt_b")]}
    out = attach_market_response(con, doc, AS_OF, stock_sectors={})
    key = _theme(out["candidates"][0]["market_response"], "monetary_tightening")["families"][0]["check_key"]
    assert key == "DGS2|2026-10-08|2"
    assert out["market_checks"][key]["shared_with"] == ["evt_a", "evt_b"]
    assert out["market_response_method"]["window_sessions"] == 2


def test_an_old_negative_level_outside_the_baseline_does_not_block_a_check(con):
    con.execute("INSERT INTO raw_observations VALUES ('DCOILWTICO', DATE '2023-01-03', -37.63, DATE '2023-01-03', "
                "DATE '2023-01-03', 'FRED', TIMESTAMP '2026-10-17', 'daily', 'pct') ON CONFLICT DO NOTHING")
    con.execute("UPDATE raw_observations SET value = -37.63 WHERE series_id = 'DCOILWTICO' AND date = DATE '2023-01-03'")
    t = _theme(_responder(con).respond(_cand(themes=(("energy_supply_shock", "positive"),))), "energy_supply_shock")
    assert t["families"][0]["result"] == ALIGNED   # the 2023 value is more than 504 observations before P_ref
