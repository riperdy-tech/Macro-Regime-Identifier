"""Registered test PREREG_risk_dial_v1: does the macro reading add to a price-rule risk dial?

Every definition follows the registration (stocks-workspace/docs/review_2026-11-12/PREREG_risk_dial_v1.md).
Pure functions on arrays first, then a small driver. The store is opened read-only.

Usage:
    python scripts/risk_dial_test.py --store data/macro_engine.duckdb --out result.json

Choices the registration left open (fixed here before the run):
  * "Month m" decisions are taken at the last SPY close of month m-1 (index i). The signals use data up
    to i; the outcomes use the 63 trading days after i (closes i+1..i+63; vol3 uses the 63 log returns
    from closes i..i+63; dd3 is the worst fall from a running peak over closes i..i+63).
  * dd3 is reported as a positive depth (1 - lowest close/running peak), so a positive coefficient means
    a deeper fall.
  * M: reading >= the 2/3 (or 3/4, 1/2) linear-interpolated quantile of all valid readings up to and
    including the reading's own month.
  * Newey-West: Bartlett weights, lag 2, no small-sample correction (the statsmodels HAC default).
  * T2: a month's return is SPY's close-to-close return from month m-1's last close to month m's last
    close; the annualised return is geometric, volatility is the monthly standard deviation (ddof 1)
    times sqrt(12), drawdown is the worst peak-to-trough of the compounded monthly equity curve
    (as a positive depth). The arithmetic-mean ratio is also shown and decides nothing.
  * Halves: the sample's months in order, first n//2 and the rest.
  * A month whose previous-month reading is absent has no M signal and is left out of the sample.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HORIZON = 63
MIN_READINGS = 36
NW_LAG = 2


# --------------------------------------------------------------------------- signals


def price_signal(closes: np.ndarray, i: int, window: int = 200) -> float:
    """P: 1.0 when closes[i] is strictly below the mean of closes[i-window+1..i], else 0.0.
    NaN when fewer than `window` closes exist up to and including i."""
    if i < window - 1:
        return float("nan")
    mean = float(np.mean(closes[i - window + 1 : i + 1]))
    return 1.0 if closes[i] < mean else 0.0


def macro_signal(readings: np.ndarray, top_fraction: float = 1.0 / 3.0, min_readings: int = MIN_READINGS) -> np.ndarray:
    """M per reading: 1.0 when the reading is in the top `top_fraction` of the valid readings up to and
    including itself (expanding window), else 0.0. NaN where the reading is NaN or fewer than
    `min_readings` valid readings exist up to and including it. Uses no later reading."""
    out = np.full(len(readings), np.nan)
    history: list[float] = []
    for j, value in enumerate(readings):
        if np.isnan(value):
            continue
        history.append(float(value))
        if len(history) < min_readings:
            continue
        threshold = float(np.quantile(np.asarray(history), 1.0 - top_fraction))
        out[j] = 1.0 if value >= threshold else 0.0
    return out


# --------------------------------------------------------------------------- outcomes


def outcomes(closes: np.ndarray, i: int, horizon: int = HORIZON) -> dict[str, float] | None:
    """vol3, dd3 (positive depth), ret3 over the `horizon` trading days after index i; None if incomplete."""
    if i + horizon >= len(closes) or i < 0:
        return None
    path = np.asarray(closes[i : i + horizon + 1], dtype=float)
    logret = np.diff(np.log(path))
    vol = float(np.std(logret, ddof=1) * np.sqrt(252.0))
    peak = np.maximum.accumulate(path)
    dd = float(np.max(1.0 - path / peak))
    ret = float(path[-1] / path[0] - 1.0)
    return {"vol3": vol, "dd3": dd, "ret3": ret}


# --------------------------------------------------------------------------- regression


def newey_west_ols(X: np.ndarray, y: np.ndarray, lag: int = NW_LAG) -> dict[str, np.ndarray]:
    """OLS with Newey-West (Bartlett) covariance. X must already hold its constant column."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, k = X.shape
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta
    u = X * resid[:, None]
    s = u.T @ u
    for ell in range(1, lag + 1):
        gamma = u[ell:].T @ u[:-ell]
        s += (1.0 - ell / (lag + 1.0)) * (gamma + gamma.T)
    cov = xtx_inv @ s @ xtx_inv
    se = np.sqrt(np.diag(cov))
    return {"beta": beta, "se": se, "t": beta / se, "n": n}


def regress(y: np.ndarray, signals: dict[str, np.ndarray]) -> dict:
    names = list(signals)
    X = np.column_stack([np.ones(len(y))] + [signals[k] for k in names])
    fit = newey_west_ols(X, y)
    out = {"n": int(fit["n"])}
    for idx, name in enumerate(["const"] + names):
        out[name] = {"coef": float(fit["beta"][idx]), "se": float(fit["se"][idx]), "t": float(fit["t"][idx])}
    return out


# --------------------------------------------------------------------------- portfolio


def rule_weights(p: np.ndarray, m: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "always_in": np.ones(len(p)),
        "price_dial": np.where(p == 1.0, 0.5, 1.0),
        "combined_dial": np.where((p == 1.0) | (m == 1.0), 0.5, 1.0),
    }


def portfolio_stats(monthly_returns: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    r = np.asarray(weights, dtype=float) * np.asarray(monthly_returns, dtype=float)
    n = len(r)
    equity = np.cumprod(1.0 + r)
    ann_ret = float(equity[-1] ** (12.0 / n) - 1.0)
    ann_vol = float(np.std(r, ddof=1) * np.sqrt(12.0))
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    depth = float(np.max(1.0 - equity / peak))
    return {
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "ratio": ann_ret / ann_vol,
        "worst_drawdown": depth,
        "ratio_arithmetic": float(np.mean(r) * 12.0 / ann_vol),
        "months": n,
    }


# --------------------------------------------------------------------------- verdict


def t1_holds(full: dict, h1: dict, h2: dict) -> bool:
    m = full["M"]
    return bool(m["coef"] > 0 and m["t"] >= 2.0 and h1["M"]["coef"] > 0 and h2["M"]["coef"] > 0)


def t2_holds(t2: dict) -> bool:
    price, comb = t2["price_dial"], t2["combined_dial"]
    return bool(
        comb["first_half"]["ratio"] > price["first_half"]["ratio"]
        and comb["second_half"]["ratio"] > price["second_half"]["ratio"]
        and comb["full"]["worst_drawdown"] <= price["full"]["worst_drawdown"]
    )


def verdict(t1_ok: bool, t2_ok: bool) -> str:
    if t1_ok and t2_ok:
        return "PASS"
    if not t1_ok:
        return "FAIL"
    return "MIXED"


# --------------------------------------------------------------------------- analysis on arrays


def analyse(
    sample: dict[str, np.ndarray],
    m_alt: dict[str, np.ndarray] | None = None,
) -> dict:
    """sample: aligned monthly arrays P, M, V, vol3, dd3, ret3, mret (V may be absent)."""
    n = len(sample["P"])
    half = n // 2
    parts = {"full": slice(0, n), "first_half": slice(0, half), "second_half": slice(half, n)}

    def reg(outcome: str, parts_key: str, extra: dict[str, np.ndarray] | None = None, m_key: np.ndarray | None = None):
        s = parts[parts_key]
        sig = {"P": sample["P"][s], "M": (sample["M"] if m_key is None else m_key)[s]}
        for name, arr in (extra or {}).items():
            sig[name] = arr[s]
        return regress(sample[outcome][s], sig)

    t1 = {k: reg("vol3", k) for k in parts}
    t1_dd = {k: reg("dd3", k) for k in parts}

    t2 = {}
    weights = rule_weights(sample["P"], sample["M"])
    for rule, w in weights.items():
        t2[rule] = {k: portfolio_stats(sample["mret"][s], w[s]) for k, s in parts.items()}

    cells = {}
    for p in (0.0, 1.0):
        for m in (0.0, 1.0):
            mask = (sample["P"] == p) & (sample["M"] == m)
            key = f"P{int(p)}_M{int(m)}"
            cells[key] = {"months": int(mask.sum())}
            for o in ("vol3", "dd3", "ret3"):
                cells[key][f"mean_{o}"] = float(sample[o][mask].mean()) if mask.any() else None

    out = {
        "n_months": n,
        "T1_vol3": t1,
        "T1_dd3": t1_dd,
        "T2": t2,
        "cells": cells,
        "counts": {
            "P_on": int((sample["P"] == 1).sum()),
            "M_on": int((sample["M"] == 1).sum()),
            "both": int(((sample["P"] == 1) & (sample["M"] == 1)).sum()),
            "neither": int(((sample["P"] == 0) & (sample["M"] == 0)).sum()),
        },
    }
    if "V" in sample:
        try:
            out["with_VIX_alarm_vol3"] = {k: reg("vol3", k, {"V": sample["V"]}) for k in parts}
        except np.linalg.LinAlgError:
            out["with_VIX_alarm_vol3"] = "singular: the alarm is constant in a sample part"
    out["T1_holds"] = t1_holds(t1["full"], t1["first_half"], t1["second_half"])
    out["T2_holds"] = t2_holds(t2)
    out["verdict"] = verdict(out["T1_holds"], out["T2_holds"])
    return out


# --------------------------------------------------------------------------- driver


def load_inputs(store: str):
    import duckdb
    import pandas as pd

    con = duckdb.connect(str(store), read_only=True)
    try:
        spy = con.execute(
            "SELECT date, close FROM sector_proxy_prices WHERE ticker = 'SPY' AND close IS NOT NULL ORDER BY date"
        ).fetchdf()
        reg = con.execute(
            "SELECT date, regime_id, probability FROM regime_scores WHERE valid AND probability IS NOT NULL"
        ).fetchdf()
        vix = con.execute(
            "SELECT date, active FROM shock_register WHERE shock_id = 'volatility_shock' ORDER BY date"
        ).fetchdf()
    finally:
        con.close()
    spy["date"] = pd.to_datetime(spy["date"])
    reg["date"] = pd.to_datetime(reg["date"])
    vix["date"] = pd.to_datetime(vix["date"])
    return spy, reg, vix


def build_sample(spy, reg, vix, top_fraction: float = 1.0 / 3.0):
    """Align the monthly arrays. Returns (sample, info)."""
    import pandas as pd

    closes = spy["close"].to_numpy(dtype=float)
    dates = spy["date"]
    months_spy = dates.dt.to_period("M")
    last_idx = {m: int(np.flatnonzero((months_spy == m).to_numpy())[-1]) for m in months_spy.unique()}

    wide = reg.pivot(index="date", columns="regime_id", values="probability")
    wide = wide[["recession", "stagflation"]].dropna()
    wide.index = wide.index.to_period("M")
    rs = (wide["recession"] + wide["stagflation"]).sort_index()
    all_months = pd.period_range(rs.index.min(), rs.index.max(), freq="M")
    series = rs.reindex(all_months).to_numpy(dtype=float)
    macro_by_month = dict(zip(all_months, macro_signal(series, top_fraction)))

    vix_m = vix.set_index("date")["active"].astype(float)

    rows = []
    skipped = []
    for m in sorted(last_idx):
        prev = m - 1
        if prev not in last_idx:
            continue
        i = last_idx[prev]
        if m not in last_idx:
            continue
        p = price_signal(closes, i)
        if np.isnan(p):
            continue
        if prev not in macro_by_month:
            continue
        mm = macro_by_month[prev]
        if np.isnan(mm):
            skipped.append(str(m))
            continue
        o = outcomes(closes, i)
        if o is None:
            continue
        vix_before = vix_m[vix_m.index <= dates.iloc[i]]
        v = float(vix_before.iloc[-1]) if len(vix_before) else float("nan")
        rows.append(
            {
                "month": m,
                "P": p,
                "M": float(mm),
                "V": v,
                "mret": float(closes[last_idx[m]] / closes[i] - 1.0),
                **o,
            }
        )
    sample = {k: np.array([r[k] for r in rows], dtype=float) for k in ("P", "M", "V", "vol3", "dd3", "ret3", "mret")}
    info = {
        "months": [str(r["month"]) for r in rows],
        "first_month": str(rows[0]["month"]),
        "last_month": str(rows[-1]["month"]),
        "months_without_signal_inside_range": skipped,
        "spy_first_date": str(dates.iloc[0].date()),
        "spy_last_date": str(dates.iloc[-1].date()),
        "reading_months": int(np.isfinite(series).sum()),
        "reading_first": str(all_months[0]),
        "reading_last": str(all_months[-1]),
    }
    return sample, info, (series, all_months, closes, dates, last_idx, rows, vix_m)


def run(store: str) -> dict:
    spy, reg, vix = load_inputs(store)
    sample, info, ctx = build_sample(spy, reg, vix)
    series, all_months, closes, dates, last_idx, rows, vix_m = ctx
    result = {"registration": "PREREG_risk_dial_v1", "sample": info}
    result["main"] = analyse(sample)
    result["main"]["half_split"] = {
        "first_half_months": [info["months"][0], info["months"][len(info["months"]) // 2 - 1]],
        "second_half_months": [info["months"][len(info["months"]) // 2], info["months"][-1]],
    }
    # Threshold variants for T1 only: replace M with the top-quarter and top-half signal.
    variants = {}
    for name, frac in (("top_quarter", 0.25), ("top_half", 0.5)):
        sig = dict(zip(all_months, macro_signal(series, frac)))
        alt = np.array([sig[r["month"] - 1] for r in rows], dtype=float)
        s2 = dict(sample)
        s2["M"] = alt
        full = analyse(s2)
        variants[name] = {
            "T1_vol3": full["T1_vol3"],
            "T1_holds": full["T1_holds"],
            "counts": full["counts"],
        }
    result["threshold_variants"] = variants
    return result


def console_table(res: dict) -> str:
    main = res["main"]
    lines = []
    s = res["sample"]
    lines.append(f"Sample {s['first_month']} .. {s['last_month']}  n={main['n_months']}")
    lines.append("T1 vol3 ~ P + M (Newey-West lag 2)")
    for k in ("full", "first_half", "second_half"):
        f = main["T1_vol3"][k]
        lines.append(
            f"  {k:12s} n={f['n']:3d}  P {f['P']['coef']:+.4f} (t {f['P']['t']:+.2f})  M {f['M']['coef']:+.4f} (t {f['M']['t']:+.2f})"
        )
    lines.append("T1 dd3 ~ P + M (depth)")
    for k in ("full", "first_half", "second_half"):
        f = main["T1_dd3"][k]
        lines.append(
            f"  {k:12s} P {f['P']['coef']:+.4f} (t {f['P']['t']:+.2f})  M {f['M']['coef']:+.4f} (t {f['M']['t']:+.2f})"
        )
    lines.append("T2   rule/sample: ann_return ann_vol ratio worst_dd")
    for rule, parts in main["T2"].items():
        for k, v in parts.items():
            lines.append(
                f"  {rule:14s} {k:12s} {v['ann_return']:+.4f} {v['ann_vol']:.4f} {v['ratio']:.3f} {v['worst_drawdown']:.4f}"
            )
    lines.append(f"T1 holds: {main['T1_holds']}   T2 holds: {main['T2_holds']}   VERDICT: {main['verdict']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True, help="DuckDB file (opened read-only)")
    ap.add_argument("--out", required=True, help="JSON output path (refuses to overwrite)")
    args = ap.parse_args(argv)
    out_path = Path(args.out)
    if out_path.exists():
        print(f"refusing to overwrite {out_path}", file=sys.stderr)
        return 2
    res = run(args.store)
    with open(out_path, "x", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(console_table(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
