"""The shadow risk dial: `outputs/risk_state.json` (PREREG_risk_dial_v1, verdict MIXED; WO_RD2).

The dial is the price rule alone: half when SPY's latest close is strictly below the mean of its last 200 closes, else
full. The macro reading (recession plus stagflation probability against its expanding top third) is stamped beside it as
context. The file changes nothing: no book, size, buy, sell or hold reads it. The store is opened read-only.

`price_signal` and `macro_signal` are the registered definitions; scripts/risk_dial_test.py imports them from here.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

MIN_READINGS = 36
PRICE_WINDOW = 200
STALE_DAYS = 5
SCHEMA = "mri.risk_state.v1"
FILENAME = "risk_state.json"
ARCHIVE_SUBDIR = "risk_state"
EVIDENCE = {"prereg": "PREREG_risk_dial_v1", "verdict": "MIXED", "n_months": 320}


# --------------------------------------------------------------------------- registered definitions


def price_signal(closes: np.ndarray, i: int, window: int = PRICE_WINDOW) -> float:
    """P: 1.0 when closes[i] is strictly below the mean of closes[i-window+1..i], else 0.0.
    NaN when fewer than `window` closes exist up to and including i."""
    if i < window - 1:
        return float("nan")
    mean = float(np.mean(closes[i - window + 1 : i + 1]))
    return 1.0 if closes[i] < mean else 0.0


def top_threshold(history, top_fraction: float = 1.0 / 3.0) -> float:
    """The linear-interpolated (1 - top_fraction) quantile of the readings in `history`."""
    return float(np.quantile(np.asarray(history, dtype=float), 1.0 - top_fraction))


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
        out[j] = 1.0 if value >= top_threshold(history, top_fraction) else 0.0
    return out


def reading_series(reg):
    """Monthly recession-plus-stagflation probability from `regime_scores` rows (date, regime_id, probability),
    on a gap-free month axis (a month without a valid reading is NaN). Returns (values, months)."""
    import pandas as pd

    wide = reg.pivot(index="date", columns="regime_id", values="probability")
    wide = wide[["recession", "stagflation"]].dropna()
    wide.index = pd.to_datetime(wide.index).to_period("M")
    rs = (wide["recession"] + wide["stagflation"]).sort_index()
    months = pd.period_range(rs.index.min(), rs.index.max(), freq="M")
    return rs.reindex(months).to_numpy(dtype=float), months


# --------------------------------------------------------------------------- the state


def _load(db_path: str | Path):
    import duckdb
    import pandas as pd

    con = duckdb.connect(str(db_path), read_only=True)
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
    for frame in (spy, reg, vix):
        frame["date"] = pd.to_datetime(frame["date"])
    return spy, reg, vix


def build_risk_state(db_path: str | Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    build_day = now.astimezone(timezone.utc).date()
    spy, reg, vix = _load(db_path)
    reasons: list[str] = []

    closes = spy["close"].to_numpy(dtype=float)
    dates = [d.date() for d in spy["date"]]
    n = len(closes)
    spy_last: date | None = dates[-1] if n else None

    dial: dict[str, Any] = {"state": None, "rule": "price_200d", "since": None}
    price_rule: dict[str, Any] | None = None
    if n == 0:
        reasons.append("spy_missing: no SPY closes in the store")
    elif (build_day - spy_last).days > STALE_DAYS:
        reasons.append(f"spy_stale: last SPY close {spy_last} is {(build_day - spy_last).days} days before {build_day}")
    elif n < PRICE_WINDOW:
        reasons.append(f"spy_short: {n} closes, fewer than {PRICE_WINDOW}")
    else:
        i = n - 1
        p = price_signal(closes, i)
        below = p == 1.0
        price_rule = {
            "spy_close": float(closes[i]),
            "mean_200": float(np.mean(closes[i - PRICE_WINDOW + 1 : i + 1])),
            "below": below,
            "closes_used": PRICE_WINDOW,
        }
        dial["state"] = "half" if below else "full"
        j = i
        while j - 1 >= PRICE_WINDOW - 1 and price_signal(closes, j - 1) == p:
            j -= 1
        dial["since"] = dates[j].isoformat()

    macro: dict[str, Any] | None = None
    regime_month: str | None = None
    valid = np.array([], dtype=int)
    if len(reg):
        series, months = reading_series(reg)
        valid = np.flatnonzero(np.isfinite(series))
    if len(valid) == 0:
        reasons.append("regime_missing: no valid regime reading")
    else:
        k = int(valid[-1])
        regime_month = str(months[k])
        history = series[: k + 1][np.isfinite(series[: k + 1])]
        macro = {
            "month": regime_month,
            "recession_plus_stagflation": float(series[k]),
            "top_third_threshold": None,
            "in_top_third": None,
            "role": "context, watched; not in the dial (PREREG_risk_dial_v1: MIXED)",
        }
        if len(history) < MIN_READINGS:
            reasons.append(f"regime_short: {len(history)} valid readings, fewer than {MIN_READINGS}")
        else:
            thr = top_threshold(history)
            macro["top_third_threshold"] = thr
            macro["in_top_third"] = bool(series[k] >= thr)

    vix_alarm: dict[str, Any] = {"active": None, "role": "context"}
    if spy_last is not None and len(vix):
        before = vix[vix["date"].dt.date <= spy_last]
        if len(before):
            vix_alarm["active"] = bool(before["active"].iloc[-1])
    if vix_alarm["active"] is None:
        reasons.append("vix_alarm_missing: no volatility_shock row on or before the last SPY close")

    return {
        "schema": SCHEMA,
        "asof": spy_last.isoformat() if spy_last else None,
        "built_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "shadow",
        "changes": "nothing: no book, size, buy, sell or hold reads this file",
        "dial": dial,
        "price_rule": price_rule,
        "macro_reading": macro,
        "vix_alarm": vix_alarm,
        "evidence": dict(EVIDENCE),
        "inputs": {"spy_last_date": spy_last.isoformat() if spy_last else None, "regime_month": regime_month},
        "degraded": bool(reasons),
        "reasons": reasons,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_risk_state(
    db_path: str | Path, outputs_dir: str | Path = "outputs", now: datetime | None = None
) -> tuple[Path, Path]:
    """Write `<outputs>/risk_state.json` and `<outputs>/archive/<build day>/risk_state/risk_state.json`."""
    doc = build_risk_state(db_path, now)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"  # a NaN is a bug here, never a value
    out = Path(outputs_dir)
    main_path = out / FILENAME
    archive_path = out / "archive" / doc["built_at"][:10] / ARCHIVE_SUBDIR / FILENAME
    _atomic_write_text(main_path, text)
    _atomic_write_text(archive_path, text)
    return main_path, archive_path
