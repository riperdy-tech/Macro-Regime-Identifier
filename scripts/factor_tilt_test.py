"""Registered test PREREG_factor_tilt_v1: does the market's state say which of Door 1's two factors to lean on?

Every definition follows the registration (stocks-workspace/docs/review_2026-11-12/PREREG_factor_tilt_v1.md, sha256
adeb5734bac0284595e1eddecf3461872ab91da8743678aeada6a0eef12f3e9d). Pure functions on arrays first, then a small driver.
The MRI store is opened read-only. The driver reads cached French factor files and refuses to run if one is missing.

Usage:
    python scripts/factor_tilt_test.py --factors-dir data/external --store data/macro_engine.duckdb \
        --prereg <path to PREREG_factor_tilt_v1.md> --out result.json [--rerun-reason "..."]

Choices the registration left open (fixed here before the run):
  * "Market return" for V and for the price index is the daily total market return Mkt-RF + RF (decimal), the
    `mkt_return` column the existing fetcher already caches. The index is the cumulative product of (1 + return),
    starting at 1; P uses `risk_state.price_signal` (strictly below the mean of the last 200 index values).
  * V: sample standard deviation (ddof 1) of the last 126 daily returns. The history is the same statistic at each
    earlier month-end day and the day itself; the quantile is numpy's linear interpolation (as in the risk dial);
    "at or above" is >=. V is absent until 120 month-ends of history exist (a month-end counts once its own 126-day
    standard deviation exists). The 1/2 and 3/4 variants change only the quantile.
  * A month-end is the last daily observation of a calendar month that is followed by an observation in a later
    month. The last calendar month in the daily file is therefore never a decision day or a history point (it may be
    incomplete), and a month m is in the sample only if month m-1's decision day exists.
  * Panic is absent when P or V is absent (the V warm-up months then leave the main sample).
  * M for month m is `macro_signal` of month m-1's reading (top third of the valid readings up to and including it,
    at least 36 readings), exactly as in the risk dial. A month with no M is out of the T3 sample only.
  * Main sample: months m >= 1963-07 with Mom, RMW and Panic all present. A month with any of them absent is left
    out (never filled); the counts of each reason are reported. The sample months are taken in order, so a hole
    would sit inside a Newey-West lag window; the result reports whether the sample is contiguous.
  * Halves: the sample's months in order, first n//2 and the rest (T3 uses its own sample's halves).
  * Post-publication sample: the main sample's months >= 2013-04; T1 only, decided on the coefficient's sign.
  * Newey-West: Bartlett, lag 2, no small-sample correction (risk_dial_test.newey_west_ols, imported).
  * Ratio: mean * 12 over (std ddof 1 * sqrt 12) of the monthly returns of the long-short portfolio. Worst drawdown is
    the deepest peak-to-trough fall (positive depth) of the compounded monthly equity curve.
  * A regression whose signal is constant in a sample part cannot be fitted: its numbers are null with a reason, and
    a verdict leg that needs them is not met. If a number a verdict leg needs is absent, no FAIL leg can fire on it
    either, so the verdict falls to MIXED and the absent numbers are listed beside it.
  * T1 regresses Mom on const + Panic. T3 regresses Mom on const + Panic + M.
  * B2 applies B1's rule (momentum weight halved) in months where Panic or M is 1.
  * "Mom alone 1927-01 to 1963-06": reported as the factor's own annualised mean, volatility, ratio and worst month
    over every month present in that range, plus T1 on the months of that range where Panic exists.
  * "Panic months per decade": counted by the decade of the outcome month, over the main sample and over every
    month with Panic present.
  * Turnover: changes of Panic between consecutive calendar months of the main sample, per year of sample.
  * Momentum weight "set to zero instead of halved": B1 with the momentum weight 0 and the RMW weight 1 in panic.
  * Newey-West needs no more than n > number of regressors + 2 observations; fewer is reported as absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# One owner of the signals shared with the daily dial, and of the Newey-West regression.
from macro_engine.risk_state import MIN_READINGS, macro_signal, price_signal, reading_series  # noqa: F401
from risk_dial_test import NW_LAG, newey_west_ols  # noqa: F401

PREREG_NAME = "PREREG_factor_tilt_v1"
PREREG_SHA256 = "adeb5734bac0284595e1eddecf3461872ab91da8743678aeada6a0eef12f3e9d"

W_RMW = 0.5625  # Door 1's 0.45 : 0.35 normalised
W_MOM = 0.4375
VOL_WINDOW = 126
V_QUANTILE = 2.0 / 3.0
V_MIN_MONTH_ENDS = 120
MAIN_START = pd.Period("1963-07", freq="M")
POST_START = pd.Period("2013-04", freq="M")
OLD_START = pd.Period("1927-01", freq="M")
OLD_END = pd.Period("1963-06", freq="M")

PASS_T = -2.0
FAIL_T = -1.0

DAILY_FILE = "ff_factors_daily.csv"
MOM_FILE = "ff_momentum_monthly.csv"
FIVE_FILE = "ff_5factors_monthly.csv"
PROVENANCE_FILES = ("ff_factors_provenance.json", "ff_extra_provenance.json")


# --------------------------------------------------------------------------- signals


def market_index(returns: np.ndarray) -> np.ndarray:
    """Daily total-return index, 1.0 before the first return."""
    return np.cumprod(1.0 + np.asarray(returns, dtype=float))


def price_state(returns: np.ndarray, i: int) -> float:
    """P at day i: 1.0 when the index is strictly below the mean of its last 200 values, else 0.0; NaN before 200."""
    return price_signal(market_index(returns), i)


def vol_126(returns: np.ndarray, i: int, window: int = VOL_WINDOW) -> float:
    """Sample standard deviation of the `window` daily returns ending at day i; NaN if fewer exist."""
    if i < window - 1:
        return float("nan")
    return float(np.std(np.asarray(returns[i - window + 1 : i + 1], dtype=float), ddof=1))


def vol_state(
    returns: np.ndarray,
    month_end_idx: np.ndarray,
    quantile: float = V_QUANTILE,
    min_month_ends: int = V_MIN_MONTH_ENDS,
) -> np.ndarray:
    """V at each month-end day: 1.0 when its 126-day standard deviation is >= the `quantile` of the same statistic over
    the month-ends up to and including it, else 0.0. NaN while fewer than `min_month_ends` month-ends have the
    statistic. Uses nothing after the day itself."""
    stds = np.array([vol_126(returns, int(i)) for i in month_end_idx], dtype=float)
    out = np.full(len(stds), np.nan)
    for k in range(len(stds)):
        hist = stds[: k + 1]
        hist = hist[np.isfinite(hist)]
        if not np.isfinite(stds[k]) or len(hist) < min_month_ends:
            continue
        out[k] = 1.0 if stds[k] >= np.quantile(hist, quantile) else 0.0
    return out


def month_ends(dates) -> tuple[np.ndarray, np.ndarray]:
    """Indices of each calendar month's last daily observation, leaving out the file's last (possibly incomplete)
    month. Returns (indices, month ordinals). Dates must be strictly increasing."""
    d = pd.DatetimeIndex(dates)
    if len(d) and not bool(d.is_monotonic_increasing and d.is_unique):
        raise ValueError("daily dates are not strictly increasing")
    ords = d.to_period("M").asi8
    idx = np.flatnonzero(ords[1:] != ords[:-1])
    return idx, ords[idx]


# --------------------------------------------------------------------------- the monthly sample


def build_monthly_sample(dates, returns, mom, rmw, readings, reading_months) -> dict:
    """Align everything per outcome month m, with the decision day the last trading day of month m-1.

    mom, rmw: pandas Series of decimal monthly returns indexed by month Period (NaN or missing = absent).
    readings, reading_months: the MRI's monthly reading on a gap-free month axis (as `reading_series` returns).
    Returns arrays aligned by outcome month: P, V, panic (and the 1/2 and 3/4 variants), M, mom, rmw."""
    returns = np.asarray(returns, dtype=float)
    if not np.all(np.isfinite(returns)):
        raise ValueError("daily market returns contain an absent value")
    idx, prev_ords = month_ends(dates)
    index = market_index(returns)
    p = np.array([price_signal(index, int(i)) for i in idx], dtype=float)
    v = {q: vol_state(returns, idx, q) for q in (V_QUANTILE, 0.5, 0.75)}

    macro = macro_signal(np.asarray(readings, dtype=float))
    macro_by = {int(mo.ordinal): float(x) for mo, x in zip(reading_months, macro)}
    mom_by = {int(k.ordinal): float(x) for k, x in mom.items()}
    rmw_by = {int(k.ordinal): float(x) for k, x in rmw.items()}

    def both(pp, vv):
        return np.where(np.isnan(pp) | np.isnan(vv), np.nan, pp * vv)

    out_months = [pd.Period(ordinal=int(o) + 1, freq="M") for o in prev_ords]
    return {
        "months": out_months,
        "P": p,
        "V": v[V_QUANTILE],
        "panic": both(p, v[V_QUANTILE]),
        "panic_q1_2": both(p, v[0.5]),
        "panic_q3_4": both(p, v[0.75]),
        "M": np.array([macro_by.get(int(o), np.nan) for o in prev_ords], dtype=float),
        "mom": np.array([mom_by.get(m.ordinal, np.nan) for m in out_months], dtype=float),
        "rmw": np.array([rmw_by.get(m.ordinal, np.nan) for m in out_months], dtype=float),
    }


# --------------------------------------------------------------------------- blends and statistics


def blend_weights(state: np.ndarray, mom_scale: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """(RMW weight, Mom weight) per month: 0.5625 : 0.4375, and in a state month the momentum weight multiplied by
    `mom_scale` (0.5 -> 0.21875) with the difference moved to RMW (0.78125)."""
    on = np.asarray(state, dtype=float) == 1.0
    w_mom = np.where(on, W_MOM * mom_scale, W_MOM)
    return 1.0 - w_mom, w_mom


def blend_returns(rmw: np.ndarray, mom: np.ndarray, state: np.ndarray, mom_scale: float = 0.5) -> np.ndarray:
    w_rmw, w_mom = blend_weights(state, mom_scale)
    return w_rmw * np.asarray(rmw, dtype=float) + w_mom * np.asarray(mom, dtype=float)


def series_stats(r: np.ndarray) -> dict:
    r = np.asarray(r, dtype=float)
    n = len(r)
    out: dict = {"months": n, "ann_mean": None, "ann_vol": None, "ratio": None, "worst_month": None,
                 "worst_drawdown": None}
    if n == 0:
        out["absent_reason"] = "no months"
        return out
    out["ann_mean"] = float(np.mean(r) * 12.0)
    out["worst_month"] = float(np.min(r))
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    out["worst_drawdown"] = float(np.max(1.0 - equity / peak))
    if n < 2:
        out["absent_reason"] = "fewer than 2 months, no standard deviation"
        return out
    vol = float(np.std(r, ddof=1) * np.sqrt(12.0))
    out["ann_vol"] = vol
    if vol > 0.0:
        out["ratio"] = out["ann_mean"] / vol
    else:
        out["absent_reason"] = "zero volatility, ratio undefined"
    return out


def parts_of(n: int) -> dict[str, slice]:
    half = n // 2
    return {"full": slice(0, n), "first_half": slice(0, half), "second_half": slice(half, n)}


def compare_rules(rmw, mom, states: dict[str, np.ndarray], mom_scale: float = 0.5) -> dict:
    """{part: {rule name: series_stats}} for blends of rmw and mom under each state array."""
    out = {}
    for part, s in parts_of(len(mom)).items():
        out[part] = {name: series_stats(blend_returns(rmw[s], mom[s], st[s], mom_scale)) for name, st in states.items()}
    return out


# --------------------------------------------------------------------------- regression


def regress_on(y: np.ndarray, signals: dict[str, np.ndarray]) -> dict:
    """OLS of y on a constant and the signals with Newey-West errors. Unfittable samples return numbers as None."""
    y = np.asarray(y, dtype=float)
    names = list(signals)
    n = len(y)
    k = 1 + len(names)
    out: dict = {"n": n}
    if n < k + 3:
        out["available"] = False
        out["reason"] = f"{n} observations for {k} regressors"
        return out
    X = np.column_stack([np.ones(n)] + [np.asarray(signals[s], dtype=float) for s in names])
    if np.linalg.matrix_rank(X) < k:
        out["available"] = False
        out["reason"] = "a signal is constant (or collinear) in this sample"
        return out
    fit = newey_west_ols(X, y)
    out["available"] = True
    for j, name in enumerate(["const"] + names):
        se = float(fit["se"][j])
        out[name] = {
            "coef": float(fit["beta"][j]),
            "se": se,
            "t": float(fit["beta"][j] / se) if se > 0.0 and np.isfinite(se) else None,
        }
    return out


def coef_t(reg: dict, name: str) -> dict:
    if reg.get("available") and name in reg:
        return {"coef": reg[name]["coef"], "t": reg[name]["t"]}
    return {"coef": None, "t": None}


# --------------------------------------------------------------------------- verdict


def _ok(x) -> bool:
    return x is not None and bool(np.isfinite(x))


def _negative(x) -> bool:
    return _ok(x) and x < 0.0


def _greater(a, b) -> bool:
    return _ok(a) and _ok(b) and a > b


def _less(a, b) -> bool:
    return _ok(a) and _ok(b) and a < b


def verdict(t1: dict, ratios: dict) -> dict:
    """t1: {"full","first_half","second_half","post"} -> {"coef","t"} (None where absent);
    ratios: {"full","first_half","second_half"} -> {"B0": ratio, "B1": ratio}.
    PASS / FAIL / MIXED exactly as registered."""
    f = t1["full"]
    legs = {
        "t1_main_negative_t_le_-2": _negative(f["coef"]) and _ok(f["t"]) and f["t"] <= PASS_T,
        "t1_first_half_negative": _negative(t1["first_half"]["coef"]),
        "t1_second_half_negative": _negative(t1["second_half"]["coef"]),
        "b1_over_b0_main": _greater(ratios["full"]["B1"], ratios["full"]["B0"]),
        "b1_over_b0_first_half": _greater(ratios["first_half"]["B1"], ratios["first_half"]["B0"]),
        "b1_over_b0_second_half": _greater(ratios["second_half"]["B1"], ratios["second_half"]["B0"]),
        "t1_post_publication_negative": _negative(t1["post"]["coef"]),
    }
    fail_legs = {
        "t1_main_t_above_-1": _ok(f["t"]) and f["t"] > FAIL_T,
        "b1_ratio_below_b0_main": _less(ratios["full"]["B1"], ratios["full"]["B0"]),
    }
    if all(legs.values()):
        label = "PASS"
    elif any(fail_legs.values()):
        label = "FAIL"
    else:
        label = "MIXED"
    absent = [k for k, val in (("t1_main_t", f["t"]), ("ratio_B0_main", ratios["full"]["B0"]),
                               ("ratio_B1_main", ratios["full"]["B1"])) if not _ok(val)]
    return {"verdict": label, "pass_legs": legs, "fail_legs": fail_legs, "absent_numbers": absent}


def t3_decision(m_t, ratios3: dict) -> dict:
    """Whether a shadow tilt would also use M: M's t <= -2.0 and B2's ratio above B1's in the main T3 sample and in
    both its halves. ratios3: {"full","first_half","second_half"} -> {"B1": ratio, "B2": ratio}."""
    legs = {
        "m_t_le_-2": _ok(m_t) and m_t <= PASS_T,
        "b2_over_b1_main": _greater(ratios3["full"]["B2"], ratios3["full"]["B1"]),
        "b2_over_b1_first_half": _greater(ratios3["first_half"]["B2"], ratios3["first_half"]["B1"]),
        "b2_over_b1_second_half": _greater(ratios3["second_half"]["B2"], ratios3["second_half"]["B1"]),
    }
    use = all(legs.values())
    return {
        "use_macro": use,
        "decision": "tilt uses market state and M" if use else "tilt is market state alone; M is stamped beside it",
        "legs": legs,
    }


# --------------------------------------------------------------------------- analysis on aligned arrays


def _select(sample: dict, mask: np.ndarray) -> dict:
    out = {k: v[mask] for k, v in sample.items() if k != "months"}
    out["months"] = [m for m, keep in zip(sample["months"], mask) if keep]
    return out


def _ratios(table: dict, a: str, b: str) -> dict:
    return {part: {a: table[part][a]["ratio"], b: table[part][b]["ratio"]} for part in table}


def _t1_block(mom: np.ndarray, panic: np.ndarray, months: list) -> dict:
    out = {part: regress_on(mom[s], {"Panic": panic[s]}) for part, s in parts_of(len(mom)).items()}
    post = np.array([m >= POST_START for m in months], dtype=bool)
    out["post"] = regress_on(mom[post], {"Panic": panic[post]})
    return out


def _contiguous(months: list) -> bool:
    return all((b - a).n == 1 for a, b in zip(months[:-1], months[1:]))


def decade_counts(months: list, panic: np.ndarray) -> dict:
    out: dict = {}
    for m, x in zip(months, panic):
        d = f"{m.year // 10 * 10}s"
        cell = out.setdefault(d, {"months": 0, "panic": 0})
        cell["months"] += 1
        cell["panic"] += int(x == 1.0)
    return out


def switches_per_year(months: list, state: np.ndarray) -> dict:
    pairs = [(a, b) for a, b, ma, mb in zip(state[:-1], state[1:], months[:-1], months[1:]) if (mb - ma).n == 1]
    n_sw = sum(1 for a, b in pairs if a != b)
    years = len(months) / 12.0
    return {"switches": n_sw, "years": years, "switches_per_year": (n_sw / years) if years > 0 else None}


def analyse(sample: dict, mom_series: pd.Series) -> dict:
    """sample: arrays from build_monthly_sample; mom_series: the Mom factor on its own month axis."""
    months_all = sample["months"]
    ords = np.array([m.ordinal for m in months_all])
    finite = {k: np.isfinite(sample[k]) for k in ("mom", "rmw", "panic", "M")}
    in_range = ords >= MAIN_START.ordinal
    main_mask = in_range & finite["mom"] & finite["rmw"] & finite["panic"]
    main = _select(sample, main_mask)
    mom, rmw, panic, months = main["mom"], main["rmw"], main["panic"], main["months"]
    n = len(mom)

    result: dict = {"sample": {
        "n_months": n,
        "first_month": str(months[0]) if n else None,
        "last_month": str(months[-1]) if n else None,
        "contiguous": _contiguous(months),
        "months_from_1963_07_left_out": {
            "mom_absent": int((in_range & ~finite["mom"]).sum()),
            "rmw_absent": int((in_range & ~finite["rmw"]).sum()),
            "panic_absent": int((in_range & ~finite["panic"]).sum()),
        },
        "panic_months": int((panic == 1.0).sum()),
    }}
    if n == 0:
        result["verdict"] = {"verdict": "MIXED", "absent_numbers": ["no months in the main sample"]}
        return result

    # T1 and T2 and the verdict
    t1 = _t1_block(mom, panic, months)
    t2 = compare_rules(rmw, mom, {"B0": np.zeros(n), "B1": panic})
    t1_nums = {k: coef_t(v, "Panic") for k, v in t1.items()}
    ratios = _ratios(t2, "B0", "B1")
    result["T1"] = {"regressions": t1, "numbers": t1_nums}
    result["T2"] = {"stats": t2, "ratios": ratios}
    result["verdict"] = verdict(t1_nums, ratios)

    # T3 on the months where M exists
    m3 = _select(main, finite["M"][main_mask])
    n3 = len(m3["mom"])
    if n3 == 0:
        result["T3"] = {"available": False, "reason": "no month with M in the main sample",
                        "decision": t3_decision(None, {p: {"B1": None, "B2": None} for p in parts_of(1)})}
    else:
        state_b2 = np.where((m3["panic"] == 1.0) | (m3["M"] == 1.0), 1.0, 0.0)
        reg3 = {part: regress_on(m3["mom"][s], {"Panic": m3["panic"][s], "M": m3["M"][s]})
                for part, s in parts_of(n3).items()}
        t3_stats = compare_rules(m3["rmw"], m3["mom"], {"B1": m3["panic"], "B2": state_b2})
        r3 = _ratios(t3_stats, "B1", "B2")
        m_t = coef_t(reg3["full"], "M")["t"]
        result["T3"] = {
            "available": True,
            "n_months": n3,
            "first_month": str(m3["months"][0]),
            "last_month": str(m3["months"][-1]),
            "regressions": reg3,
            "stats": t3_stats,
            "ratios": r3,
            "decision": t3_decision(m_t, r3),
        }

    # Reported, deciding nothing
    zero_w = compare_rules(rmw, mom, {"B0": np.zeros(n), "B1_momentum_weight_zero": panic}, mom_scale=0.0)
    variants = {}
    for name, key in (("v_quantile_1_2", "panic_q1_2"), ("v_quantile_3_4", "panic_q3_4")):
        alt = main[key]
        variants[name] = {
            "panic_months": int((alt == 1.0).sum()),
            "T1": _t1_block(mom, alt, months),
            "T2_ratios": _ratios(compare_rules(rmw, mom, {"B0": np.zeros(n), "B1": alt}), "B0", "B1"),
        }
    old_mask = np.array([OLD_START <= m <= OLD_END for m in mom_series.index], dtype=bool)
    old_vals = mom_series.to_numpy(dtype=float)[old_mask]
    old_vals = old_vals[np.isfinite(old_vals)]
    old_rows = (ords >= OLD_START.ordinal) & (ords <= OLD_END.ordinal) & finite["mom"] & finite["panic"]
    old = _select(sample, old_rows)
    result["reported_deciding_nothing"] = {
        "mom_alone_1927_01_to_1963_06": {
            "stats": series_stats(old_vals),
            "T1_on_months_with_panic": _t1_block(old["mom"], old["panic"], old["months"]) if len(old["mom"]) else None,
        },
        "panic_months_per_decade_main_sample": decade_counts(months, panic),
        "panic_months_per_decade_all_available": decade_counts(
            [m for m, ok in zip(months_all, finite["panic"]) if ok], sample["panic"][finite["panic"]]
        ),
        "worst_month_and_drawdown": {k: {"worst_month": t2["full"][k]["worst_month"],
                                         "worst_drawdown": t2["full"][k]["worst_drawdown"]} for k in ("B0", "B1")},
        "v_threshold_variants": variants,
        "momentum_weight_zero": {"stats": zero_w, "ratios": _ratios(zero_w, "B0", "B1_momentum_weight_zero")},
        "state_turnover": switches_per_year(months, panic),
    }
    return result


# --------------------------------------------------------------------------- strict JSON


def clean(obj):
    """Recursively make `obj` strict-JSON safe: numpy scalars to Python, NaN and inf to None."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [clean(v) for v in obj.tolist()]
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        x = float(obj)
        return x if np.isfinite(x) else None
    if isinstance(obj, pd.Period):
        return str(obj)
    return obj


def dumps(result: dict) -> str:
    return json.dumps(clean(result), indent=2, allow_nan=False)


# --------------------------------------------------------------------------- driver


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_prereg(path: str | Path, expected: str | None = None) -> str:
    """The registration the result is held against must be byte-identical to the registered one."""
    expected = PREREG_SHA256 if expected is None else expected
    got = sha256_file(Path(path))
    if got != expected:
        raise ValueError(f"{path} has sha256 {got}, not the registered {expected}")
    return got


def load_cached(factors_dir: str | Path) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict]:
    """Read the cached factor files. Raises FileNotFoundError naming every missing input."""
    d = Path(factors_dir)
    needed = [d / DAILY_FILE, d / MOM_FILE, d / FIVE_FILE]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        raise FileNotFoundError("cached input missing, fetch it first (fetch_ff_factors.py): " + ", ".join(missing))
    daily = pd.read_csv(d / DAILY_FILE)
    daily["date"] = pd.to_datetime(daily["date"])
    bad = daily["mkt_rf"].isna() | daily["mkt_return"].isna() | (daily["mkt_rf"] <= -0.9999)
    if bool(bad.any()):
        raise ValueError(f"{DAILY_FILE}: absent or missing-code market returns on " + ", ".join(
            str(x.date()) for x in daily.loc[bad, "date"].head(10)))
    mom_df = pd.read_csv(d / MOM_FILE)
    five_df = pd.read_csv(d / FIVE_FILE)
    mom = pd.Series(mom_df["mom"].to_numpy(dtype=float), index=pd.PeriodIndex(mom_df["year_month"], freq="M"))
    rmw = pd.Series(five_df["rmw"].to_numpy(dtype=float), index=pd.PeriodIndex(five_df["year_month"], freq="M"))
    hashes = {name: sha256_file(d / name) for name in (DAILY_FILE, MOM_FILE, FIVE_FILE)}
    for name in PROVENANCE_FILES:
        p = d / name
        hashes[name] = sha256_file(p) if p.exists() else None
    return daily, mom, rmw, hashes


def load_readings(store: str | Path):
    import duckdb

    con = duckdb.connect(str(store), read_only=True)
    try:
        reg = con.execute(
            "SELECT date, regime_id, probability FROM regime_scores WHERE valid AND probability IS NOT NULL"
        ).fetchdf()
    finally:
        con.close()
    reg["date"] = pd.to_datetime(reg["date"])
    return reading_series(reg)


def run(daily: pd.DataFrame, mom: pd.Series, rmw: pd.Series, readings: np.ndarray, reading_months) -> dict:
    sample = build_monthly_sample(daily["date"], daily["mkt_return"].to_numpy(dtype=float), mom, rmw,
                                  readings, reading_months)
    res = analyse(sample, mom)
    res["sample"]["daily_first_date"] = str(daily["date"].iloc[0].date())
    res["sample"]["daily_last_date"] = str(daily["date"].iloc[-1].date())
    res["sample"]["mom_first_month"] = str(mom.index.min())
    res["sample"]["mom_last_month"] = str(mom.index.max())
    res["sample"]["rmw_first_month"] = str(rmw.index.min())
    res["sample"]["rmw_last_month"] = str(rmw.index.max())
    res["sample"]["readings_valid"] = int(np.isfinite(readings).sum())
    res["sample"]["readings_first_month"] = str(reading_months[0])
    res["sample"]["readings_last_month"] = str(reading_months[-1])
    res["sample"]["readings_sha256"] = hashlib.sha256(
        np.ascontiguousarray(np.nan_to_num(np.asarray(readings, dtype=float), nan=-1.0)).tobytes()
    ).hexdigest()
    return res


def console_table(res: dict) -> str:
    s = res["sample"]
    lines = [f"Sample {s['first_month']} .. {s['last_month']}  n={s['n_months']}  panic months={s['panic_months']}"]
    if "T1" not in res:
        return f"{lines[0]}\nVERDICT: {res['verdict']['verdict']} (no months in the main sample)"
    t1 = res["T1"]["numbers"]
    for k in ("full", "first_half", "second_half", "post"):
        c = t1[k]
        lines.append(f"  T1 {k:12s} coef {c['coef']}  t {c['t']}")
    for k, v in res["T2"]["ratios"].items():
        lines.append(f"  T2 {k:12s} B0 {v['B0']}  B1 {v['B1']}")
    lines.append(f"VERDICT: {res['verdict']['verdict']}")
    lines.append(f"T3: {res['T3']['decision']['decision']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--factors-dir", required=True, help="cache directory holding the French factor files")
    ap.add_argument("--store", required=True, help="DuckDB file (opened read-only)")
    ap.add_argument("--prereg", required=True, help="path to PREREG_factor_tilt_v1.md (its sha256 is checked)")
    ap.add_argument("--out", required=True, help="JSON output path (refuses to overwrite)")
    ap.add_argument("--rerun-reason", default=None, help="why this is not the first run (recorded in the result)")
    args = ap.parse_args(argv)
    out_path = Path(args.out)
    if out_path.exists():
        print(f"refusing to overwrite {out_path}", file=sys.stderr)
        return 2
    try:
        prereg_hash = check_prereg(args.prereg)
        daily, mom, rmw, hashes = load_cached(args.factors_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2
    readings, reading_months = load_readings(args.store)
    res = run(daily, mom, rmw, readings, reading_months)
    res = {
        "registration": PREREG_NAME,
        "registration_sha256": prereg_hash,
        "rerun_reason": args.rerun_reason,
        "input_sha256": hashes,
        **res,
    }
    with open(out_path, "x", encoding="utf-8") as fh:
        fh.write(dumps(res))
    print(console_table(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
