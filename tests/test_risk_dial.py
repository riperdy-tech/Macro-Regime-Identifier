"""Tests for scripts/risk_dial_test.py (registration PREREG_risk_dial_v1), on synthetic series with
hand-computed answers."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import risk_dial_test as rd  # noqa: E402


# ------------------------------------------------------------------ P


def test_price_signal_boundary_equal_is_not_below():
    closes = np.full(200, 100.0)  # last close equals the 200-day mean
    assert rd.price_signal(closes, 199) == 0.0


def test_price_signal_below_and_above():
    closes = np.full(200, 100.0)
    closes[-1] = 99.0  # mean 99.995, close below it
    assert rd.price_signal(closes, 199) == 1.0
    closes[-1] = 101.0
    assert rd.price_signal(closes, 199) == 0.0


def test_price_signal_needs_200_closes():
    assert math.isnan(rd.price_signal(np.full(199, 100.0), 198))


def test_price_signal_uses_only_the_last_200():
    closes = np.concatenate([np.full(50, 1000.0), np.full(200, 100.0)])
    assert rd.price_signal(closes, 249) == 0.0  # the 1000s are outside the window


# ------------------------------------------------------------------ M


def test_macro_signal_none_before_36_readings():
    out = rd.macro_signal(np.arange(1.0, 41.0))
    assert np.isnan(out[:35]).all()
    assert out[35] == 1.0  # the 36th reading, the highest so far


def test_macro_signal_hand_threshold():
    # 35 readings 0..34 then 20: sorted history 0..19,20,20,21..34 (36 values);
    # 2/3 quantile sits at index 23.333 = 22 + 1/3 * (23 - 22) = 22.333; 20 is below it
    readings = np.concatenate([np.arange(35.0), [20.0]])
    assert rd.macro_signal(readings)[35] == 0.0
    # 30 is above it
    readings[35] = 30.0
    assert rd.macro_signal(readings)[35] == 1.0


def test_macro_signal_value_equal_to_threshold_counts_as_top():
    assert rd.macro_signal(np.full(36, 5.0))[35] == 1.0


def test_macro_signal_uses_no_future_reading():
    base = np.concatenate([np.arange(36.0) % 7, [3.0, 2.0, 5.0]])
    changed = base.copy()
    changed[-1] = 1000.0
    a, b = rd.macro_signal(base), rd.macro_signal(changed)
    assert np.array_equal(a[:-1], b[:-1], equal_nan=True)


def test_macro_signal_missing_reading_gives_nan_and_is_not_counted():
    readings = np.arange(40.0)
    readings[10] = np.nan
    out = rd.macro_signal(readings)
    assert np.isnan(out[10])
    assert np.isnan(out[:36]).all()  # 35 valid readings up to index 35, so still no signal
    assert out[36] == 1.0  # the 36th valid reading


def test_macro_signal_fraction_changes_threshold():
    # 35 readings 0..34 then 23: sorted 0..22,23,23,24..34; 2/3 quantile (index 23.33) = 23,
    # 3/4 quantile (index 26.25) = 25.25
    readings = np.concatenate([np.arange(35.0), [23.0]])
    assert rd.macro_signal(readings, 1.0 / 3.0)[35] == 1.0
    assert rd.macro_signal(readings, 0.25)[35] == 0.0


# ------------------------------------------------------------------ outcomes


def test_outcomes_toy_path():
    closes = np.concatenate([[100.0, 120.0, 60.0], np.full(60, 60.0), [90.0]])
    assert len(closes) == 64
    o = rd.outcomes(closes, 0)
    assert o["ret3"] == pytest.approx(-0.1)
    assert o["dd3"] == pytest.approx(0.5)  # 120 -> 60
    logs = [math.log(1.2), math.log(0.5)] + [0.0] * 60 + [math.log(1.5)]
    mean = sum(logs) / 63
    sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / 62)
    assert o["vol3"] == pytest.approx(sd * math.sqrt(252))


def test_outcomes_incomplete_window_is_none():
    assert rd.outcomes(np.full(63, 100.0), 0) is None
    assert rd.outcomes(np.full(64, 100.0), 0) is not None


def test_outcomes_flat_path():
    o = rd.outcomes(np.full(64, 100.0), 0)
    assert o == {"vol3": 0.0, "dd3": 0.0, "ret3": 0.0}


def test_outcomes_drawdown_is_peak_to_trough_in_order():
    # trough first, then a higher peak: no fall after the peak
    closes = np.concatenate([[100.0, 50.0], np.linspace(50.0, 200.0, 62)])
    assert rd.outcomes(closes, 0)["dd3"] == pytest.approx(0.5)


# ------------------------------------------------------------------ regression


def test_ols_two_regressors_hand_computed():
    # orthogonal design, X'X = 4I; y = [3,1,0,-1]
    # beta = [0.75, 1.25, 0.75]; residuals [0.25,-0.25,-0.25,0.25]; lag-0 variance of each = 0.25/16
    X = np.array([[1, 1, 1], [1, 1, -1], [1, -1, 1], [1, -1, -1]], dtype=float)
    y = np.array([3.0, 1.0, 0.0, -1.0])
    fit = rd.newey_west_ols(X, y, lag=0)
    assert fit["beta"] == pytest.approx([0.75, 1.25, 0.75])
    se = math.sqrt(0.25 / 16.0)
    assert fit["se"] == pytest.approx([se, se, se])
    assert fit["t"] == pytest.approx(np.array([0.75, 1.25, 0.75]) / se)


def test_newey_west_matches_explicit_formula():
    rng = np.random.default_rng(7)
    n = 40
    X = np.column_stack([np.ones(n), rng.integers(0, 2, n), rng.integers(0, 2, n)]).astype(float)
    y = rng.normal(size=n) + 0.5 * X[:, 1]
    fit = rd.newey_west_ols(X, y, lag=2)
    beta = np.linalg.solve(X.T @ X, X.T @ y)
    e = y - X @ beta
    bread = np.linalg.inv(X.T @ X)
    meat = np.zeros((3, 3))
    for t in range(n):
        meat += e[t] ** 2 * np.outer(X[t], X[t])
    for ell, w in ((1, 2 / 3), (2, 1 / 3)):
        for t in range(ell, n):
            term = e[t] * e[t - ell] * (np.outer(X[t], X[t - ell]) + np.outer(X[t - ell], X[t]))
            meat += w * term
    expected_se = np.sqrt(np.diag(bread @ meat @ bread))
    assert fit["se"] == pytest.approx(expected_se)
    assert fit["beta"] == pytest.approx(beta)


def test_regress_names():
    n = 12
    rng = np.random.default_rng(1)
    out = rd.regress(rng.normal(size=n), {"P": rng.integers(0, 2, n).astype(float), "M": np.arange(n) % 2.0})
    assert set(out) == {"n", "const", "P", "M"}
    assert out["n"] == n


# ------------------------------------------------------------------ portfolio


def test_rule_weights():
    p = np.array([1.0, 0.0, 0.0, 1.0])
    m = np.array([0.0, 1.0, 0.0, 1.0])
    w = rd.rule_weights(p, m)
    assert w["always_in"].tolist() == [1, 1, 1, 1]
    assert w["price_dial"].tolist() == [0.5, 1, 1, 0.5]
    assert w["combined_dial"].tolist() == [0.5, 0.5, 1, 0.5]


def test_portfolio_stats_hand_computed():
    # weighted returns: 1*0.1 = 0.1, 0.5*(-0.2) = -0.1
    s = rd.portfolio_stats(np.array([0.1, -0.2]), np.array([1.0, 0.5]))
    equity = [1.1, 0.99]
    assert s["ann_return"] == pytest.approx(0.99 ** 6 - 1.0)
    vol = math.sqrt(0.02) * math.sqrt(12.0)
    assert s["ann_vol"] == pytest.approx(vol)
    assert s["ratio"] == pytest.approx((0.99 ** 6 - 1.0) / vol)
    assert s["worst_drawdown"] == pytest.approx(1.0 - equity[1] / equity[0])
    assert s["ratio_arithmetic"] == pytest.approx(0.0)  # mean of 0.1, -0.1 is 0


def test_portfolio_drawdown_from_the_start_counts():
    s = rd.portfolio_stats(np.array([-0.1, 0.05, 0.05]), np.ones(3))
    assert s["worst_drawdown"] == pytest.approx(0.1)


# ------------------------------------------------------------------ verdicts


def _reg(m_coef, m_t):
    return {"M": {"coef": m_coef, "t": m_t}}


def _t2(comb_h1, comb_h2, price_h1, price_h2, comb_dd, price_dd):
    def stat(ratio, dd=0.0):
        return {"ratio": ratio, "worst_drawdown": dd}

    return {
        "price_dial": {"first_half": stat(price_h1), "second_half": stat(price_h2), "full": stat(0, price_dd)},
        "combined_dial": {"first_half": stat(comb_h1), "second_half": stat(comb_h2), "full": stat(0, comb_dd)},
    }


def test_t1_holds_rules():
    assert rd.t1_holds(_reg(0.02, 2.0), _reg(0.01, 0.5), _reg(0.03, 1.0))
    assert not rd.t1_holds(_reg(0.02, 1.99), _reg(0.01, 0.5), _reg(0.03, 1.0))
    assert not rd.t1_holds(_reg(0.02, 2.5), _reg(-0.01, 0.5), _reg(0.03, 1.0))
    assert not rd.t1_holds(_reg(0.02, 2.5), _reg(0.01, 0.5), _reg(-0.03, 1.0))
    assert not rd.t1_holds(_reg(-0.02, 3.0), _reg(0.01, 0.5), _reg(0.03, 1.0))


def test_t2_holds_rules():
    assert rd.t2_holds(_t2(0.5, 0.5, 0.4, 0.4, 0.2, 0.2))
    assert not rd.t2_holds(_t2(0.5, 0.4, 0.4, 0.4, 0.2, 0.2))  # tie in a half is not higher
    assert not rd.t2_holds(_t2(0.5, 0.5, 0.4, 0.4, 0.21, 0.2))  # deeper drawdown
    assert not rd.t2_holds(_t2(0.3, 0.5, 0.4, 0.4, 0.1, 0.2))  # lower in one half


def test_three_verdicts():
    assert rd.verdict(True, True) == "PASS"
    assert rd.verdict(False, True) == "FAIL"
    assert rd.verdict(False, False) == "FAIL"
    assert rd.verdict(True, False) == "MIXED"


# ------------------------------------------------------------------ driver on a synthetic store


def _make_store(path, spike_month=None):
    import duckdb
    import pandas as pd

    days = pd.bdate_range("2000-01-03", "2006-12-29")
    rng = np.random.default_rng(3)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(days))))
    months = pd.period_range("1998-01", "2006-12", freq="M")
    reading = 0.4 + 0.01 * (np.arange(len(months)) * 7 % 11)
    if spike_month is not None:
        reading[list(months).index(pd.Period(spike_month))] = 5.0
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sector_proxy_prices (ticker VARCHAR, date DATE, close DOUBLE)")
    con.execute("CREATE TABLE regime_scores (regime_id VARCHAR, date DATE, probability DOUBLE, valid BOOLEAN)")
    con.execute("CREATE TABLE shock_register (shock_id VARCHAR, date DATE, active BOOLEAN)")
    con.executemany(
        "INSERT INTO sector_proxy_prices VALUES ('SPY', ?, ?)",
        [(d.date(), float(c)) for d, c in zip(days, close)],
    )
    rows = []
    for mth, val in zip(months, reading):
        d = mth.to_timestamp().date()
        rows += [("recession", d, float(val), True), ("stagflation", d, 0.0, True), ("goldilocks", d, 0.1, True)]
    con.executemany("INSERT INTO regime_scores VALUES (?, ?, ?, ?)", rows)
    con.executemany(
        "INSERT INTO shock_register VALUES ('volatility_shock', ?, ?)",
        [(d.date(), i % 5 == 0) for i, d in enumerate(days)],
    )
    con.close()


def test_driver_uses_previous_month_reading_and_no_future(tmp_path):
    import pandas as pd

    _make_store(tmp_path / "a.duckdb")
    _make_store(tmp_path / "b.duckdb", spike_month="2004-06")
    out = []
    for name in ("a", "b"):
        spy, reg, vix = rd.load_inputs(str(tmp_path / f"{name}.duckdb"))
        sample, info, _ = rd.build_sample(spy, reg, vix)
        out.append((sample, info))
    (sa, ia), (sb, ib) = out
    assert ia["months"] == ib["months"]
    months = ia["months"]
    # the spike is the 2004-06 reading; it must only ever matter from month 2004-07
    first_changed = int(np.flatnonzero(sa["M"] != sb["M"])[0])
    assert months[first_changed] == str(pd.Period("2004-07"))
    assert sb["M"][first_changed] == 1.0
    # sample starts when 36 readings exist (first reading 1998-01 -> 36th is 2000-12 -> month 2001-01)
    # and 200 closes exist (month-end index >= 199)
    assert months[0] >= "2001-01"


def test_driver_end_to_end_refuses_overwrite(tmp_path, capsys):
    _make_store(tmp_path / "s.duckdb")
    out = tmp_path / "res.json"
    assert rd.main(["--store", str(tmp_path / "s.duckdb"), "--out", str(out)]) == 0
    assert out.exists()
    assert "VERDICT" in capsys.readouterr().out
    assert rd.main(["--store", str(tmp_path / "s.duckdb"), "--out", str(out)]) == 2
