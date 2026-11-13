"""Tests for scripts/factor_tilt_test.py (registration PREREG_factor_tilt_v1), on synthetic data only."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import factor_tilt_test as ft  # noqa: E402


# ------------------------------------------------------------------ P


def test_price_state_needs_200_days():
    r = np.zeros(199)
    assert math.isnan(ft.price_state(r, 198))
    assert ft.price_state(np.zeros(200), 199) == 0.0  # defined at exactly 200


def test_price_state_equal_to_mean_is_not_below_and_zero_returns_are_values():
    r = np.zeros(200)  # index flat: close equals its 200-day mean
    assert ft.price_state(r, 199) == 0.0
    r[-1] = -0.01  # last close below the mean
    assert ft.price_state(r, 199) == 1.0
    r[-1] = 0.01
    assert ft.price_state(r, 199) == 0.0


def test_price_state_window_is_the_last_200_only():
    r = np.concatenate([np.full(50, 0.5), np.zeros(200)])  # a big run-up older than 200 days is outside the window
    assert ft.price_state(r, 249) == 0.0


# ------------------------------------------------------------------ V


def _month_ends(n: int) -> np.ndarray:
    return 125 + 21 * np.arange(n)  # first month-end has exactly 126 returns


def test_vol_126_boundary():
    r = np.arange(130, dtype=float) * 1e-4
    assert math.isnan(ft.vol_126(r, 124))
    assert ft.vol_126(r, 125) == pytest.approx(np.std(r[:126], ddof=1))


def test_vol_state_absent_with_fewer_than_120_month_ends():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, 125 + 21 * 130)
    v119 = ft.vol_state(r, _month_ends(119))
    assert np.isnan(v119).all()
    v120 = ft.vol_state(r, _month_ends(120))
    assert np.isnan(v120[:119]).all()
    assert np.isfinite(v120[119])


def test_vol_state_at_or_above_quantile_boundary():
    r = np.zeros(125 + 21 * 125)  # every std is exactly 0, equal to its own quantile
    v = ft.vol_state(r, _month_ends(125))
    assert (v[119:] == 1.0).all()  # "at or above" counts equality


def test_vol_state_rising_and_falling_volatility():
    n_me = 130
    scale = 0.001 * (1 + np.repeat(np.arange(n_me + 6), 21))[: 125 + 21 * n_me + 1]  # rising scale
    r = np.where(np.arange(len(scale)) % 2 == 0, 1.0, -1.0) * scale
    up = ft.vol_state(r, _month_ends(n_me))
    assert (up[119:] == 1.0).all()  # each new month-end is the highest so far
    down = ft.vol_state(r[::-1].copy(), _month_ends(n_me))
    assert (down[119:] == 0.0).all()  # each new month-end is the lowest so far


def test_vol_state_uses_nothing_after_the_decision_day():
    rng = np.random.default_rng(2)
    r = rng.normal(0, 0.01, 125 + 21 * 160)
    me = _month_ends(150)
    base = ft.vol_state(r, me)
    k = 135
    r2 = r.copy()
    r2[me[k] + 1 :] = rng.normal(0, 0.2, len(r2) - me[k] - 1)  # change everything after month-end k
    changed = ft.vol_state(r2, me)
    assert np.array_equal(base[: k + 1], changed[: k + 1], equal_nan=True)


# ------------------------------------------------------------------ month-ends and alignment


def test_month_ends_leave_out_the_last_month():
    dates = pd.bdate_range("2020-01-01", "2020-03-15")
    idx, ords = ft.month_ends(dates)
    assert [str(dates[i].date()) for i in idx] == ["2020-01-31", "2020-02-28"]  # March is the last, so not used
    assert list(ords) == [pd.Period("2020-01").ordinal, pd.Period("2020-02").ordinal]


def test_month_ends_refuse_unsorted_dates():
    with pytest.raises(ValueError):
        ft.month_ends(pd.DatetimeIndex(["2020-01-02", "2020-01-01"]))


def _synthetic_daily(seed: int = 7, start: str = "1926-07-01", end: str = "2020-12-31"):
    """Business days with a 72-month cycle: 60 calm months, 12 crisis months (falling, volatile)."""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    per = dates.to_period("M")
    mi = per.asi8 - per.asi8[0]
    crisis = (mi % 72) >= 60
    r = np.where(crisis, rng.normal(-0.002, 0.02, len(dates)), rng.normal(0.0004, 0.006, len(dates)))
    return dates, r


def _empty_monthly(first: str = "1927-01", last: str = "2020-12") -> pd.Series:
    idx = pd.period_range(first, last, freq="M")
    return pd.Series(np.nan, index=idx)


def _no_readings():
    months = pd.period_range("2000-01", "2000-02", freq="M")
    return np.array([np.nan, np.nan]), months


def test_a_months_outcome_never_enters_its_own_signal():
    dates, r = _synthetic_daily(end="1990-12-31")
    readings, rmonths = _no_readings()
    base = ft.build_monthly_sample(dates, r, _empty_monthly("1927-01", "1990-12"), _empty_monthly("1927-01", "1990-12"),
                                   readings, rmonths)
    # change the daily returns of 1980-06 and later, and the Mom/RMW values of 1980-06
    cut = np.flatnonzero(dates >= pd.Timestamp("1980-06-01"))[0]
    r2 = r.copy()
    r2[cut:] = r2[cut:] * -3.0 + 0.05
    mom = _empty_monthly("1927-01", "1990-12")
    mom[pd.Period("1980-06")] = 0.9
    other = ft.build_monthly_sample(dates, r2, mom, _empty_monthly("1927-01", "1990-12"), readings, rmonths)
    months = base["months"]
    j = months.index(pd.Period("1980-06"))  # month 1980-06's decision day is 1980-05-30, before the change
    for key in ("P", "V", "panic", "panic_q1_2", "panic_q3_4"):
        assert np.array_equal(base[key][: j + 1], other[key][: j + 1], equal_nan=True), key
    assert other["mom"][j] == 0.9  # the outcome is read, but only as the outcome


def test_macro_signal_for_month_m_uses_the_reading_of_month_m_minus_1():
    dates, r = _synthetic_daily(end="2000-12-31")
    months = pd.period_range("1990-01", "1999-12", freq="M")
    rng = np.random.default_rng(3)
    readings = rng.uniform(0.0, 1.0, len(months))
    mom = _empty_monthly("1927-01", "2000-12")
    s1 = ft.build_monthly_sample(dates, r, mom, mom, readings, months)
    k = int(np.flatnonzero(months == pd.Period("1995-06"))[0])
    readings2 = readings.copy()
    readings2[k] = 5.0  # make month 1995-06's own reading the largest ever
    s2 = ft.build_monthly_sample(dates, r, mom, mom, readings2, months)
    i_jun = s1["months"].index(pd.Period("1995-06"))
    i_jul = s1["months"].index(pd.Period("1995-07"))
    assert s1["M"][i_jun] == s2["M"][i_jun]  # outcome month 1995-06 reads 1995-05's reading: unchanged
    assert s2["M"][i_jul] == 1.0  # outcome month 1995-07 reads 1995-06's reading: now the maximum


def test_zero_return_is_a_value_in_the_sample():
    dates, r = _synthetic_daily(end="1999-12-31")
    mom = _empty_monthly("1927-01", "1999-12")
    rmw = _empty_monthly("1927-01", "1999-12")
    for p in pd.period_range("1963-07", "1999-12", freq="M"):
        mom[p] = 0.0
        rmw[p] = 0.0
    readings, rmonths = _no_readings()
    sample = ft.build_monthly_sample(dates, r, mom, rmw, readings, rmonths)
    res = ft.analyse(sample, mom)
    assert res["sample"]["n_months"] > 400  # zeros were kept, not treated as absent
    assert res["sample"]["months_from_1963_07_left_out"]["mom_absent"] == 0


# ------------------------------------------------------------------ blends


def test_blend_weights_exact():
    w_rmw, w_mom = ft.blend_weights(np.array([0.0, 1.0]))
    assert w_rmw[0] == 0.5625 and w_mom[0] == 0.4375
    assert w_rmw[1] == 0.78125 and w_mom[1] == 0.21875
    assert w_rmw[0] + w_mom[0] == 1.0 and w_rmw[1] + w_mom[1] == 1.0
    z_rmw, z_mom = ft.blend_weights(np.array([1.0]), mom_scale=0.0)
    assert z_rmw[0] == 1.0 and z_mom[0] == 0.0


def test_blend_returns_hand_computed():
    rmw = np.array([0.02, 0.02])
    mom = np.array([-0.10, 0.04])
    out = ft.blend_returns(rmw, mom, np.array([1.0, 0.0]))
    assert out[0] == pytest.approx(0.78125 * 0.02 + 0.21875 * -0.10)
    assert out[1] == pytest.approx(0.5625 * 0.02 + 0.4375 * 0.04)


def test_series_stats_hand_computed():
    r = np.array([0.1, -0.1, 0.0, 0.2])
    s = ft.series_stats(r)
    assert s["ann_mean"] == pytest.approx(0.05 * 12)
    assert s["ann_vol"] == pytest.approx(np.std(r, ddof=1) * math.sqrt(12))
    assert s["ratio"] == pytest.approx(s["ann_mean"] / s["ann_vol"])
    assert s["worst_month"] == -0.1
    assert s["worst_drawdown"] == pytest.approx(0.1)  # 1.1 -> 0.99 is a 10% fall


def test_series_stats_zero_volatility_is_absent_not_zero():
    s = ft.series_stats(np.zeros(5))
    assert s["ratio"] is None and "zero volatility" in s["absent_reason"]
    assert s["ann_mean"] == 0.0  # a zero mean is a value


# ------------------------------------------------------------------ regression


def test_regress_on_recovers_coefficient_and_flags_constant_signal():
    rng = np.random.default_rng(4)
    panic = (rng.random(300) < 0.2).astype(float)
    y = 0.01 - 0.05 * panic + rng.normal(0, 0.02, 300)
    reg = ft.regress_on(y, {"Panic": panic})
    assert reg["Panic"]["coef"] == pytest.approx(-0.05, abs=0.01)
    assert reg["Panic"]["t"] < -4
    const = ft.regress_on(y, {"Panic": np.zeros(300)})
    assert const["available"] is False and "constant" in const["reason"]
    assert ft.coef_t(const, "Panic") == {"coef": None, "t": None}


# ------------------------------------------------------------------ verdict branches


def _t1(full_t=-3.0, full_coef=-0.04, h1=-0.04, h2=-0.04, post=-0.04):
    return {
        "full": {"coef": full_coef, "t": full_t},
        "first_half": {"coef": h1, "t": None},
        "second_half": {"coef": h2, "t": None},
        "post": {"coef": post, "t": None},
    }


def _ratios(full=(0.5, 0.6), h1=(0.5, 0.6), h2=(0.5, 0.6)):
    return {p: {"B0": a, "B1": b} for p, (a, b) in (("full", full), ("first_half", h1), ("second_half", h2))}


def test_verdict_pass():
    assert ft.verdict(_t1(), _ratios())["verdict"] == "PASS"


def test_verdict_pass_boundary_t_exactly_minus_two():
    assert ft.verdict(_t1(full_t=-2.0), _ratios())["verdict"] == "PASS"
    assert ft.verdict(_t1(full_t=-1.99), _ratios())["verdict"] == "MIXED"


def test_verdict_fail_when_t1_weak():
    assert ft.verdict(_t1(full_t=-0.99, full_coef=-0.01), _ratios())["verdict"] == "FAIL"
    assert ft.verdict(_t1(full_t=0.5, full_coef=0.01), _ratios())["verdict"] == "FAIL"


def test_verdict_fail_when_b1_ratio_below_b0_in_main():
    assert ft.verdict(_t1(), _ratios(full=(0.6, 0.5)))["verdict"] == "FAIL"


def test_verdict_mixed_branches():
    assert ft.verdict(_t1(h1=0.01), _ratios())["verdict"] == "MIXED"  # a half with the wrong sign
    assert ft.verdict(_t1(post=0.01), _ratios())["verdict"] == "MIXED"  # post-publication sign wrong
    assert ft.verdict(_t1(), _ratios(h2=(0.6, 0.5)))["verdict"] == "MIXED"  # a half where B1 is below B0
    assert ft.verdict(_t1(), _ratios(h1=(0.5, 0.5)))["verdict"] == "MIXED"  # equal ratio is not "above"
    assert ft.verdict(_t1(full_t=-1.5), _ratios())["verdict"] == "MIXED"  # between -2 and -1


def test_verdict_absent_numbers_never_pass_and_are_listed():
    out = ft.verdict(_t1(full_t=None, full_coef=None), _ratios())
    assert out["verdict"] == "MIXED" and "t1_main_t" in out["absent_numbers"]


def test_t3_decision_branches():
    good = {p: {"B1": 0.5, "B2": 0.6} for p in ("full", "first_half", "second_half")}
    assert ft.t3_decision(-2.0, good)["use_macro"] is True
    assert ft.t3_decision(-1.9, good)["use_macro"] is False  # M's t too weak
    bad_half = {**good, "second_half": {"B1": 0.6, "B2": 0.5}}
    assert ft.t3_decision(-3.0, bad_half)["use_macro"] is False
    bad_main = {**good, "full": {"B1": 0.6, "B2": 0.5}}
    assert ft.t3_decision(-3.0, bad_main)["use_macro"] is False
    assert ft.t3_decision(None, good)["use_macro"] is False
    assert "market state alone" in ft.t3_decision(-1.0, good)["decision"]


# ------------------------------------------------------------------ end to end on seeded synthetic data


def _planted_factors(dates, r, effect: float, seed: int):
    readings = np.random.default_rng(seed + 100).uniform(0.0, 1.0, len(pd.period_range("1981-01", "2020-12", freq="M")))
    rmonths = pd.period_range("1981-01", "2020-12", freq="M")
    blank = _empty_monthly()
    pre = ft.build_monthly_sample(dates, r, blank, blank, readings, rmonths)  # signals do not depend on Mom/RMW
    rng = np.random.default_rng(seed)
    idx = pd.period_range("1927-01", "2020-12", freq="M")
    panic_by = {m: x for m, x in zip(pre["months"], pre["panic"])}
    mom_vals, rmw_vals = [], []
    for m in idx:
        pm = panic_by.get(m, np.nan)
        base = 0.005 + rng.normal(0, 0.03)
        mom_vals.append(base + (effect * pm if np.isfinite(pm) else 0.0))
        rmw_vals.append(0.004 + rng.normal(0, 0.02))
    return pd.Series(mom_vals, index=idx), pd.Series(rmw_vals, index=idx), readings, rmonths


def test_end_to_end_planted_effect_is_pass():
    dates, r = _synthetic_daily()
    mom, rmw, readings, rmonths = _planted_factors(dates, r, effect=-0.06, seed=11)
    daily = pd.DataFrame({"date": dates, "mkt_return": r})
    res = ft.run(daily, mom, rmw, readings, rmonths)
    assert res["sample"]["first_month"] == "1963-07"
    assert res["sample"]["panic_months"] > 30
    assert res["T1"]["numbers"]["full"]["t"] <= -2.0
    assert res["verdict"]["verdict"] == "PASS", res["verdict"]
    assert res["T3"]["available"] and res["T3"]["n_months"] > 200
    json.loads(ft.dumps(res))  # strict JSON: no NaN anywhere
    assert "NaN" not in ft.dumps(res)


def test_end_to_end_no_effect_is_not_pass():
    dates, r = _synthetic_daily()
    mom, rmw, readings, rmonths = _planted_factors(dates, r, effect=0.0, seed=12)
    daily = pd.DataFrame({"date": dates, "mkt_return": r})
    res = ft.run(daily, mom, rmw, readings, rmonths)
    assert res["verdict"]["verdict"] in ("FAIL", "MIXED")
    assert res["T3"]["decision"]["use_macro"] is False  # M is noise here
    assert "NaN" not in ft.dumps(res)


# ------------------------------------------------------------------ driver


def _write_cache(d: Path, daily_dates, r, mom, rmw):
    pd.DataFrame({"date": daily_dates.strftime("%Y-%m-%d"), "mkt_rf": r - 0.0001, "mkt_return": r}).to_csv(
        d / ft.DAILY_FILE, index=False)
    pd.DataFrame({"year_month": [str(p) for p in mom.index], "mom": mom.to_numpy()}).to_csv(d / ft.MOM_FILE, index=False)
    pd.DataFrame({"year_month": [str(p) for p in rmw.index], "rmw": rmw.to_numpy()}).to_csv(d / ft.FIVE_FILE, index=False)


def test_driver_refuses_when_a_cached_input_is_missing_and_names_it(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        ft.load_cached(tmp_path)
    for name in (ft.DAILY_FILE, ft.MOM_FILE, ft.FIVE_FILE):
        assert name in str(exc.value)


def test_driver_refuses_a_changed_registration(tmp_path):
    p = tmp_path / "prereg.md"
    p.write_text("not the registered text", encoding="utf-8")
    with pytest.raises(ValueError):
        ft.check_prereg(p)
    assert ft.check_prereg(p, hashlib.sha256(p.read_bytes()).hexdigest()) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_driver_end_to_end_with_files_and_store(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    dates, r = _synthetic_daily(end="2000-12-31")
    mom, rmw, _, _ = _planted_factors(*_synthetic_daily(), effect=-0.06, seed=5)
    mom, rmw = mom[:"2000-12"], rmw[:"2000-12"]
    cache = tmp_path / "ext"
    cache.mkdir()
    _write_cache(cache, dates, r, mom, rmw)
    store = tmp_path / "store.duckdb"
    con = duckdb.connect(str(store))
    con.execute("CREATE TABLE regime_scores (date DATE, regime_id VARCHAR, probability DOUBLE, valid BOOLEAN)")
    rng = np.random.default_rng(9)
    for p in pd.period_range("1985-01", "1999-12", freq="M"):
        for rid in ("recession", "stagflation"):
            con.execute("INSERT INTO regime_scores VALUES (?, ?, ?, TRUE)",
                        [p.to_timestamp(how="end").date(), rid, float(rng.uniform(0, 0.5))])
    con.close()
    prereg = tmp_path / "prereg.md"
    prereg.write_text("registered text", encoding="utf-8")
    monkeypatch.setattr(ft, "PREREG_SHA256", hashlib.sha256(prereg.read_bytes()).hexdigest())
    out = tmp_path / "result.json"
    rc = ft.main(["--factors-dir", str(cache), "--store", str(store), "--prereg", str(prereg), "--out", str(out),
                  "--rerun-reason", "unit test"])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["registration"] == "PREREG_factor_tilt_v1"
    assert doc["registration_sha256"] == ft.PREREG_SHA256
    assert doc["rerun_reason"] == "unit test"
    assert set(doc["input_sha256"]) >= {ft.DAILY_FILE, ft.MOM_FILE, ft.FIVE_FILE}
    assert doc["verdict"]["verdict"] in ("PASS", "FAIL", "MIXED")
    assert doc["T3"]["available"] is True
    assert ft.main(["--factors-dir", str(cache), "--store", str(store), "--prereg", str(prereg),
                    "--out", str(out)]) == 2  # refuses to overwrite
