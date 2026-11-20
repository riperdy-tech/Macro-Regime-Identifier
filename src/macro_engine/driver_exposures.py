"""Measured driver exposures: `outputs/driver_exposures.json` (WO_MD1, MRI review step 3).

How each of the 17 sector and sub-industry funds responds to three drivers, measured on returns: the market (SPY log
return, %), the 10-year yield (DGS10, basis points) and oil (DCOILWTICO log change, %). Per fund, ordinary least
squares of the fund's 5-session return relative to SPY on a constant and the drivers' 5-session changes, over the last
`WEEKS` non-overlapping weekly rows. Then what moved in 21 and 63 sessions and what the exposures say that implies for
each fund. The store is opened read-only; nothing existing is changed. The loader, the calendar alignment and the
z-score are `market_state`'s.

The exposures say which funds gain or lose WHEN a driver moves (out-of-sample, `EVIDENCE`); they have no forecasting
power. They are a risk annotation and an explanation, never a tilt. `history` carries the last `HISTORY` sessions of SPY
close, DGS10 (percent) and oil (dollars) so the screener can fit single stocks on its own prices.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from macro_engine.market_state import (
    SECTOR_FUNDS,
    STALE_DAYS,
    SUB_FUNDS,
    WINDOWS,
    _align,
    _atomic_write_text,
    _changes,
    _fmt,
    _iso,
    _load,
    _num,
    z_of_change,
)

SCHEMA = "mri.driver_exposures.v1"
FILENAME = "driver_exposures.json"
ARCHIVE_SUBDIR = "driver_exposures"
SAMPLE = 5  # sessions per weekly row
WEEKS = 260
MIN_WEEKS = 104
T_THRESHOLD = 2.5
MOVED_Z = 1.0
HISTORY = 420
TOP = 3

DRIVERS = ("market", "rates", "oil")
# driver -> (store series or None for SPY, change kind, plain name, unit of the change)
DRIVER_SPEC = {
    "market": (None, "log", "the market", "percent (log return)"),
    "rates": ("DGS10", "bp", "the 10-year yield", "basis points"),
    "oil": ("DCOILWTICO", "log", "oil", "percent (log change)"),
}
# sub-industry ticker -> sector id as in config/sectors.yaml
SUB_IDS = {
    "SMH": "semiconductors",
    "IGV": "software",
    "KBE": "banks",
    "XBI": "biotech",
    "XHB": "homebuilders",
    "XOP": "oil_gas_ep",
}
FUNDS = (*SECTOR_FUNDS, *SUB_FUNDS)

EVIDENCE = {
    "study": "WO_MD1",
    "measured": "2026-11-20 on stored prices; walk-forward: exposures fitted only on the weeks before each evaluation date; 11 sector funds; rank correlation across funds between beta x the driver changes that then happened and the relative returns that then happened",
    "tests": {
        "three_drivers_260w_next_63": {"rank_correlation": 0.39, "t": 10.7, "halves": [0.39, 0.39], "periods": 90},
        "three_drivers_260w_next_21": {"rank_correlation": 0.39, "t": 18.0, "halves": [0.36, 0.42], "periods": 271},
        "market_alone_when_moved_1sd": {"rank_correlation": 0.41, "t": 6.2},
        "rates_alone_when_moved_1sd": {"rank_correlation": 0.31, "t": 4.4, "halves": [0.26, 0.37]},
        "oil_alone_when_moved_1sd": {"rank_correlation": 0.37, "t": 5.1, "halves": [0.40, 0.33]},
        "credit_spread_fourth_driver": {"rank_correlation": [-0.06, -0.04], "result": "no out-of-sample hold: dropped"},
        "dollar_fifth_driver": {"rank_correlation": 0.11, "t": 1.2, "halves": [0.28, -0.06], "result": "dropped"},
        "window_156_weeks": {"rank_correlation": 0.36, "rates_halves": [0.10, 0.54], "result": "less stable: 260 taken"},
        "forecast_past_63_to_next_63": {"rank_correlation": -0.04, "t": -0.9, "result": "no forecasting power"},
    },
    "reading": "the exposures say which funds gain or lose when a driver moves, on data they were not fitted on; they do not say what happens next: a risk annotation and an explanation, never a tilt",
}


# --------------------------------------------------------------------------- registered definitions


def weekly_indices(n: int) -> np.ndarray:
    """Every SAMPLE-th row counted back from the newest of n rows (ascending); consecutive rows are SAMPLE apart."""
    return np.arange(n - 1, -1, -SAMPLE)[::-1]


def is_moved(z: float | None) -> bool:
    """A driver moved when |z| is at least MOVED_Z; an unknown z is not a move."""
    return z is not None and abs(z) >= MOVED_Z


def fit_ols(y: np.ndarray, x: np.ndarray) -> dict[str, Any] | None:
    """OLS of y on a constant and the columns of x. Returns beta and t per column (intercept left out), r2 and the
    row count; None when the design is rank deficient or has no degrees of freedom."""
    n, k = len(y), x.shape[1] + 1
    if n <= k:
        return None
    design = np.column_stack([np.ones(n), x])
    if np.linalg.matrix_rank(design) < k:
        return None
    xtx_inv = np.linalg.inv(design.T @ design)
    coef = xtx_inv @ design.T @ y
    resid = y - design @ coef
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    sigma2 = rss / (n - k)
    se = np.sqrt(sigma2 * np.diag(xtx_inv))
    with np.errstate(all="ignore"):
        t = np.where(se > 0, coef / se, np.nan)
    return {
        "beta": [float(b) for b in coef[1:]],
        "t": [_num(v) for v in t[1:]],
        "r2": (1.0 - rss / tss) if tss > 0 else None,
        "n": n,
    }


def implied(beta: float | None, change: float | None) -> float | None:
    """Points of relative return a driver's change implies for a fund. 0.0 is a value."""
    return None if beta is None or change is None else beta * change


def total(parts: list[float | None]) -> float | None:
    """The sum of the parts that exist; None when none does."""
    have = [p for p in parts if p is not None]
    return float(sum(have)) if have else None


# --------------------------------------------------------------------------- the artifact


def _empty(now: datetime, spy_last, reasons: list[str]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "as_of": spy_last.isoformat() if spy_last else None,
        "built_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "shadow",
        "changes": "additive artifact; no score, anchor, table or other output reads it or is changed by it",
        "summary": None,
        "drivers": None,
        "funds": None,
        "most_helped": [],
        "most_hurt": [],
        "history": None,
        "evidence": json.loads(json.dumps(EVIDENCE)),
        "degraded": True,
        "reasons": reasons,
    }


def build_driver_exposures(db_path: str | Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    build_day = now.astimezone(timezone.utc).date()
    prices, fred = _load(db_path)
    reasons: list[str] = []

    spy_s = prices[prices["ticker"] == "SPY"].set_index("date")["close"]
    calendar = spy_s.index
    closes = spy_s.to_numpy(dtype=float)
    n = len(closes)
    spy_last = calendar[-1].date() if n else None
    if n == 0:
        reasons.append("spy_missing: no SPY closes in the store")
        return _empty(now, spy_last, reasons)
    if (build_day - spy_last).days > STALE_DAYS:
        reasons.append(f"spy_stale: last SPY close {spy_last} is {(build_day - spy_last).days} days before {build_day}")
        return _empty(now, spy_last, reasons)
    i = n - 1

    # ---- aligned series and their changes
    level: dict[str, np.ndarray] = {"market": closes}
    for name in ("rates", "oil"):
        sid = DRIVER_SPEC[name][0]
        part = fred[fred["series_id"] == sid]
        if len(part) == 0:
            reasons.append(f"driver_missing: {name} ({sid}) has no observations in the store")
            level[name] = np.full(n, np.nan)
        else:
            level[name] = _align(part.set_index("date")["value"], calendar)[0].to_numpy(dtype=float)
    kind = {name: DRIVER_SPEC[name][1] for name in DRIVERS}
    wk_change = {name: _changes(level[name], SAMPLE, kind[name]) for name in DRIVERS}

    idx = weekly_indices(n)[-WEEKS:]
    active = []
    for name in DRIVERS:
        have = int(np.isfinite(wk_change[name][idx]).sum())
        if have >= MIN_WEEKS:
            active.append(name)
        elif not any(r.startswith(f"driver_missing: {name} ") for r in reasons):
            reasons.append(f"driver_short: {name} has {have} usable weekly changes, fewer than {MIN_WEEKS}")
    for name in DRIVERS:
        if name not in active:
            reasons.append(f"driver_dropped: {name} has no exposure for any fund; the regression runs on {', '.join(active) or 'nothing'}")

    # ---- what moved
    drivers: dict[str, Any] = {}
    change_at: dict[int, dict[str, float | None]] = {w: {} for w in WINDOWS}
    z_at: dict[int, dict[str, float | None]] = {w: {} for w in WINDOWS}
    for name in DRIVERS:
        windows: dict[str, Any] = {}
        for w in WINDOWS:
            series = _changes(level[name], w, kind[name])
            change, z = _num(series[i]), z_of_change(series)
            change_at[w][name], z_at[w][name] = change, z
            windows[str(w)] = {"change": change, "z": z, "moved": is_moved(z)}
        drivers[name] = {"series": DRIVER_SPEC[name][0] or "SPY", "unit": DRIVER_SPEC[name][3], "windows": windows}

    # ---- the funds
    spy_lr = {w: _changes(closes, w, "log") for w in WINDOWS}
    spy_wk = wk_change["market"]
    funds: dict[str, Any] = {}
    for tk in FUNDS:
        sector_id = SECTOR_FUNDS[tk][0] if tk in SECTOR_FUNDS else SUB_IDS[tk]
        entry: dict[str, Any] = {
            "sector_id": sector_id,
            "exposures": {name: {"beta": None, "t": None} for name in DRIVERS},
            "r2": None,
            "n_weeks": None,
            "first_week": None,
            "last_week": None,
            "reason": None,
            "readings": {},
        }
        funds[tk] = entry
        part = prices[prices["ticker"] == tk]
        if len(part) == 0:
            entry["reason"] = f"fund_missing: no {tk} closes in the store"
            reasons.append(entry["reason"])
            continue
        fund_level = _align(part.set_index("date")["close"], calendar)[0].to_numpy(dtype=float)
        rel_wk = _changes(fund_level, SAMPLE, "log") - spy_wk
        if active:
            x_all = np.column_stack([wk_change[name][idx] for name in active])
            ok = np.isfinite(rel_wk[idx]) & np.isfinite(x_all).all(axis=1)
        else:
            ok = np.zeros(len(idx), dtype=bool)
        entry["n_weeks"] = int(ok.sum())
        fit = None
        if entry["n_weeks"] < MIN_WEEKS or not active:
            entry["reason"] = f"fund_short: {tk} has {entry['n_weeks']} usable weekly rows, fewer than {MIN_WEEKS}"
        else:
            fit = fit_ols(rel_wk[idx][ok], x_all[ok])
            if fit is None:
                entry["reason"] = f"fit_singular: {tk} design has no unique least-squares solution"
        if entry["reason"]:
            reasons.append(entry["reason"])
        if fit is not None:
            for name, b, t in zip(active, fit["beta"], fit["t"]):
                entry["exposures"][name] = {"beta": b, "t": t}
            entry["r2"] = fit["r2"]
            entry["first_week"] = _iso(calendar[idx[ok][0]])
            entry["last_week"] = _iso(calendar[idx[ok][-1]])
        for w in WINDOWS:
            realised = _num(_changes(fund_level, w, "log")[i] - spy_lr[w][i])
            parts = {name: implied(entry["exposures"][name]["beta"], change_at[w][name]) for name in DRIVERS}
            tot = total(list(parts.values()))
            exposed = []
            for name in DRIVERS:
                e = entry["exposures"][name]
                if e["t"] is not None and abs(e["t"]) >= T_THRESHOLD and is_moved(z_at[w][name]):
                    exposed.append(
                        {"driver": name, "beta": e["beta"], "t": e["t"], "change": change_at[w][name], "z": z_at[w][name], "implied": parts[name]}
                    )
            exposed.sort(key=lambda r: abs(r["implied"]), reverse=True)
            entry["readings"][str(w)] = {
                "implied": parts,
                "implied_total": tot,
                "realised": realised,
                "unexplained": None if tot is None or realised is None else realised - tot,
                "exposed": exposed,
            }

    # ---- most helped and hurt, 21 sessions, sector funds only
    helped: list[dict[str, Any]] = []
    hurt: list[dict[str, Any]] = []
    w0 = WINDOWS[0]
    if any(drivers[name]["windows"][str(w0)]["moved"] for name in DRIVERS):
        ranked = [(tk, funds[tk]["readings"][str(w0)]["implied_total"]) for tk in SECTOR_FUNDS]
        ranked = sorted((r for r in ranked if r[1] is not None), key=lambda r: r[1])
        row = lambda tk, v: {"fund": tk, "sector_id": funds[tk]["sector_id"], "implied_total": v}  # noqa: E731
        helped = [row(tk, v) for tk, v in ranked[::-1][:TOP]]
        hurt = [row(tk, v) for tk, v in ranked[:TOP]]
    else:
        reasons.append(f"most_helped_empty: no driver moved by {MOVED_Z} standard deviation or more in {w0} sessions")

    degraded = not any(f["r2"] is not None for f in funds.values())
    if degraded:
        reasons.append("no_exposures: no fund has enough usable weekly rows for a fit")

    # ---- the history block for the screener
    h = slice(max(0, n - HISTORY), n)
    history = {
        "dates": [d.date().isoformat() for d in calendar[h]],
        "spy_close": [_num(v) for v in closes[h]],
        "dgs10": [_num(v) for v in level["rates"][h]],
        "oil": [_num(v) for v in level["oil"][h]],
    }

    doc = _empty(now, spy_last, reasons)
    doc.update(
        {
            "summary": _summary(drivers, helped, hurt, funds),
            "drivers": drivers,
            "funds": funds,
            "most_helped": helped,
            "most_hurt": hurt,
            "history": history,
            "degraded": degraded,
        }
    )
    return doc


def _summary(drivers: dict[str, Any], helped: list, hurt: list, funds: dict[str, Any]) -> str | None:
    w = str(WINDOWS[0])
    said = []
    for name in DRIVERS:
        win = drivers[name]["windows"][w]
        if win["moved"]:
            ch = win["change"]
            word = "rose" if ch > 0 else "fell"
            what = f"{abs(ch):.0f} basis points" if name == "rates" else f"{abs(ch):.1f}%"
            said.append(f"{DRIVER_SPEC[name][2]} {word} {what}")
    if not said:
        return f"Over {w} sessions no driver moved by a full standard deviation."
    sentence = f"Over {w} sessions {' and '.join(said) if len(said) < 3 else ', '.join(said[:-1]) + ' and ' + said[-1]}."
    if helped and hurt:
        names = [SECTOR_FUNDS[r["fund"]][1] for r in helped]
        worst = hurt[0]["fund"]
        sentence += f" Measured sensitivities put {', '.join(names[:-1])} and {names[-1]} ahead and {SECTOR_FUNDS[worst][1]} behind"
        reading = funds[worst]["readings"][w]
        if reading["realised"] is not None:
            sentence += (
                f"; they explain {_fmt(reading['implied_total'])} of {SECTOR_FUNDS[worst][1]}'s "
                f"{_fmt(reading['realised'])} points against the index"
            )
        sentence += "."
    return sentence


def write_driver_exposures(
    db_path: str | Path, outputs_dir: str | Path = "outputs", now: datetime | None = None
) -> tuple[Path, Path]:
    """Write `<outputs>/driver_exposures.json` and `<outputs>/archive/<build day>/driver_exposures/driver_exposures.json`."""
    doc = build_driver_exposures(db_path, now)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"  # a NaN is a bug here, never a value
    out = Path(outputs_dir)
    main_path = out / FILENAME
    archive_path = out / "archive" / doc["built_at"][:10] / ARCHIVE_SUBDIR / FILENAME
    _atomic_write_text(main_path, text)
    _atomic_write_text(archive_path, text)
    return main_path, archive_path
