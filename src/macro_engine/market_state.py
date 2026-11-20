"""The daily market state: `outputs/market_state.json` (WO_MK1, MRI review step 1).

A reading of market stress and of what is driving it, from prices already in the store: SPY, 17 sector and
sub-industry funds (`sector_proxy_prices`) and nine FRED series (`raw_observations`). The store is opened read-only;
nothing existing is changed. The calendar is SPY's trading days; each FRED series is carried forward at most
`CARRY_SESSIONS` sessions onto it.

Stress step: `calm` (SPY not below its 200-close mean and VIX below 20), `watch` (exactly one of the two), `stress`
(both), `crisis` (VIX at or above 40). Driver, for 21 and 63 sessions: the highest of the composite scores `rates`,
`growth_credit`, `inflation` and the worst sector's score, if at least 1.0, else `unclear`. Every definition is in
WO_MK1; the measured table behind the step is `EVIDENCE`. The facts and the scores are description, not rules.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from macro_engine.risk_state import price_signal

PRICE_WINDOW = 200
DRAWDOWN_WINDOW = 252
STALE_DAYS = 5
CARRY_SESSIONS = 5
WINDOWS = (21, 63)
FACT_WINDOWS = (5, 21, 63)
SD_WINDOW = 2520
SD_MIN = 500
DRIVER_THRESHOLD = 1.0
VIX_WATCH = 20.0
VIX_CRISIS = 40.0
SCHEMA = "mri.market_state.v1"
FILENAME = "market_state.json"
ARCHIVE_SUBDIR = "market_state"

# ticker -> (sector_id as in config/sectors.yaml, plain name used in the summary)
SECTOR_FUNDS = {
    "XLC": ("communication_services", "communication services"),
    "XLY": ("consumer_discretionary", "consumer discretionary"),
    "XLP": ("consumer_staples", "staples"),
    "XLE": ("energy", "energy"),
    "XLF": ("financials", "financials"),
    "XLV": ("health_care", "health care"),
    "XLI": ("industrials", "industrials"),
    "XLK": ("information_technology", "technology"),
    "XLB": ("materials", "materials"),
    "XLRE": ("real_estate", "real estate"),
    "XLU": ("utilities", "utilities"),
}
SUB_FUNDS = ("SMH", "IGV", "KBE", "XBI", "XHB", "XOP")
CYCLICALS = ("XLY", "XLI", "XLF", "XLB")
DEFENSIVES = ("XLP", "XLU", "XLV")

BP_SERIES = ("DGS2", "DGS10", "DFII10", "T10YIE", "BAA10Y", "BAMLH0A0HYM2")
LOG_SERIES = ("DCOILWTICO", "DTWEXBGS")
FRED_SERIES = ("VIXCLS",) + BP_SERIES + LOG_SERIES

EVIDENCE = {
    "study": "WO_MK1",
    "measured": "2026-11-20 on stored prices, 1999-06 to 2026-08, 6800 trading days; outcomes are the next 21 and 63 sessions of SPY; in-sample, descriptive",
    "steps": {
        "calm": {"share_of_days_pct": 57.3, "volatility_next_21_pct": 11.8, "volatility_next_63_pct": 13.3, "worst_point_next_63_pct": -4.1, "return_next_63_pct": 1.8},
        "watch": {"share_of_days_pct": 18.8, "volatility_next_21_pct": 17.3, "volatility_next_63_pct": 17.1, "worst_point_next_63_pct": -4.5, "return_next_63_pct": 3.6},
        "stress": {"share_of_days_pct": 21.0, "volatility_next_21_pct": 23.7, "volatility_next_63_pct": 23.4, "worst_point_next_63_pct": -8.5, "return_next_63_pct": 0.2},
        "crisis": {"share_of_days_pct": 2.9, "volatility_next_21_pct": 46.2, "volatility_next_63_pct": 38.2, "worst_point_next_63_pct": -11.4, "return_next_63_pct": 5.9},
    },
    "driver": "face check only on 21 dated sell-offs; not outcome-tested",
}


# --------------------------------------------------------------------------- registered definitions


def stress_step(below_200d: bool | None, vix: float | None) -> str | None:
    """calm / watch / stress / crisis; None when either fact is missing. Crisis is VIX at or above 40."""
    if below_200d is None or vix is None or np.isnan(vix):
        return None
    if vix >= VIX_CRISIS:
        return "crisis"
    high = vix >= VIX_WATCH
    n = int(bool(below_200d)) + int(high)
    return ("calm", "watch", "stress")[n]


def z_of_change(changes: np.ndarray) -> float | None:
    """Today's change over the plain standard deviation (about the mean) of the last SD_WINDOW changes; None when
    today's change is missing or fewer than SD_MIN changes exist."""
    if len(changes) == 0 or not np.isfinite(changes[-1]):
        return None
    hist = changes[-SD_WINDOW:]
    hist = hist[np.isfinite(hist)]
    if len(hist) < SD_MIN:
        return None
    sd = float(np.std(hist))
    if sd == 0.0:
        return None
    return float(changes[-1]) / sd


def mean_of_legs(legs: list[float | None]) -> float | None:
    """The mean of the legs that exist; None when none does."""
    have = [x for x in legs if x is not None]
    return float(np.mean(have)) if have else None


def pick_driver(scores: dict[str, float | None]) -> str:
    """The key with the highest score if that score is at least DRIVER_THRESHOLD, else `unclear`."""
    best, best_score = "unclear", None
    for key, score in scores.items():
        if score is not None and (best_score is None or score > best_score):
            best, best_score = key, score
    if best_score is None or best_score < DRIVER_THRESHOLD:
        return "unclear"
    return best


def publish_driver(step: str | None, d21: str, d63: str) -> tuple[str | None, int | None, str | None]:
    """(driver, window, backdrop). Calm gives `none`; else the 21-session driver unless unclear, else the 63-session
    driver (window 63), else `unclear`. The backdrop is the 63-session driver when it differs and is not unclear."""
    if step is None:
        return None, None, None
    if step == "calm":
        driver, window = "none", None
    elif d21 != "unclear":
        driver, window = d21, 21
    elif d63 != "unclear":
        driver, window = d63, 63
    else:
        driver, window = "unclear", None
    backdrop = d63 if d63 != "unclear" and d63 != driver else None
    return driver, window, backdrop


# --------------------------------------------------------------------------- the state


def _num(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if np.isfinite(x) else None


def _changes(values: np.ndarray, n: int, kind: str) -> np.ndarray:
    """n-session change at each row: bp (difference x 100), pts (difference) or log (100 x log ratio)."""
    out = np.full(len(values), np.nan)
    if len(values) <= n:
        return out
    a, b = values[n:], values[:-n]
    with np.errstate(all="ignore"):
        if kind == "log":
            ok = (a > 0) & (b > 0)
            out[n:] = np.where(ok, 100.0 * np.log(np.where(ok, a, 1.0) / np.where(ok, b, 1.0)), np.nan)
        else:
            out[n:] = (a - b) * (100.0 if kind == "bp" else 1.0)
    return out


def _load(db_path: str | Path):
    import duckdb
    import pandas as pd

    tickers = ["SPY", *SECTOR_FUNDS, *SUB_FUNDS]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        prices = con.execute(
            "SELECT ticker, date, close FROM sector_proxy_prices WHERE close IS NOT NULL AND ticker IN ("
            + ",".join("?" * len(tickers))
            + ") ORDER BY ticker, date",
            tickers,
        ).fetchdf()
        fred = con.execute(
            "SELECT series_id, date, value FROM raw_observations WHERE value IS NOT NULL AND series_id IN ("
            + ",".join("?" * len(FRED_SERIES))
            + ") QUALIFY row_number() OVER (PARTITION BY series_id, date ORDER BY realtime_start DESC) = 1 "
            "ORDER BY series_id, date",
            list(FRED_SERIES),
        ).fetchdf()
    finally:
        con.close()
    for frame in (prices, fred):
        frame["date"] = pd.to_datetime(frame["date"])
    return prices, fred


def _align(series, calendar):
    """Values of `series` (index: dates) on the calendar, each carried forward at most CARRY_SESSIONS sessions.
    Returns (values, observed dates), both aligned to the calendar; NaN / NaT where nothing is within reach."""
    import pandas as pd

    union = series.index.union(calendar)
    values = series.reindex(union).ffill().reindex(calendar)
    obs = pd.Series(series.index, index=series.index).reindex(union).ffill().reindex(calendar)
    cal = calendar.to_numpy()
    obs_np = obs.to_numpy()
    have = ~pd.isna(obs_np)
    gap = np.full(len(calendar), CARRY_SESSIONS + 1)
    gap[have] = np.arange(len(calendar))[have] - np.searchsorted(cal, obs_np[have], side="left")
    keep = have & (gap <= CARRY_SESSIONS)
    return values.where(keep), obs.where(keep)


def _iso(ts) -> str | None:
    import pandas as pd

    return None if pd.isna(ts) else pd.Timestamp(ts).date().isoformat()


def _fmt(x: float, nd: int = 1) -> str:
    return f"{x:+.{nd}f}".replace("-", "−")


def build_market_state(db_path: str | Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    build_day = now.astimezone(timezone.utc).date()
    prices, fred = _load(db_path)
    reasons: list[str] = []

    spy_s = prices[prices["ticker"] == "SPY"].set_index("date")["close"]
    calendar = spy_s.index
    closes = spy_s.to_numpy(dtype=float)
    dates = [d.date() for d in calendar]
    n = len(closes)
    spy_last: date | None = dates[-1] if n else None

    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "asof": spy_last.isoformat() if spy_last else None,
        "built_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "shadow",
        "changes": "additive artifact; no score, anchor, table or other output reads it or is changed by it",
        "step": None,
        "since": None,
        "driver": None,
        "driver_window": None,
        "backdrop": None,
        "summary": None,
        "facts": None,
        "scores": None,
        "leaders": None,
        "laggards": None,
        "evidence": json.loads(json.dumps(EVIDENCE)),
        "inputs": {"spy_last_date": spy_last.isoformat() if spy_last else None},
        "degraded": True,
        "reasons": reasons,
    }
    if n == 0:
        reasons.append("spy_missing: no SPY closes in the store")
        return doc
    if (build_day - spy_last).days > STALE_DAYS:
        reasons.append(f"spy_stale: last SPY close {spy_last} is {(build_day - spy_last).days} days before {build_day}")
        return doc
    if n < PRICE_WINDOW:
        reasons.append(f"spy_short: {n} closes, fewer than {PRICE_WINDOW}")
        return doc
    i = n - 1

    # ---- aligned inputs
    fred_aligned: dict[str, np.ndarray] = {}
    fred_obs: dict[str, Any] = {}
    fred_last: dict[str, Any] = {}
    for sid in FRED_SERIES:
        part = fred[fred["series_id"] == sid]
        if len(part) == 0:
            fred_aligned[sid] = np.full(n, np.nan)
            fred_obs[sid] = None
            fred_last[sid] = None
            continue
        s = part.set_index("date")["value"]
        values, obs = _align(s, calendar)
        fred_aligned[sid] = values.to_numpy(dtype=float)
        fred_obs[sid] = obs.iloc[-1]
        fred_last[sid] = s.index.max()
    fund_aligned: dict[str, np.ndarray] = {}
    fund_closes: dict[str, np.ndarray] = {}
    for tk in (*SECTOR_FUNDS, *SUB_FUNDS):
        part = prices[prices["ticker"] == tk]
        if len(part) == 0:
            continue
        s = part.set_index("date")["close"]
        fund_closes[tk] = s.to_numpy(dtype=float)
        fund_aligned[tk] = _align(s, calendar)[0].to_numpy(dtype=float)

    # ---- stress step and its run
    vix_arr = fred_aligned["VIXCLS"]
    vix = _num(vix_arr[i])
    if vix is None:
        if fred_last["VIXCLS"] is None:
            reasons.append("vix_missing: no VIXCLS observations in the store")
        else:
            reasons.append(
                f"vix_stale: last VIXCLS observation {fred_last['VIXCLS'].date()} is more than {CARRY_SESSIONS} sessions before {spy_last}"
            )

    def step_at(j: int) -> str | None:
        p = price_signal(closes, j)
        v = _num(vix_arr[j])
        if np.isnan(p) or v is None:
            return None
        return stress_step(p == 1.0, v)

    below = price_signal(closes, i) == 1.0
    step = step_at(i)
    since = None
    if step is not None:
        j = i
        while j - 1 >= 0 and step_at(j - 1) == step:
            j -= 1
        since = dates[j].isoformat()

    # ---- facts
    mean_200 = float(np.mean(closes[i - PRICE_WINDOW + 1 : i + 1]))
    spy_facts: dict[str, Any] = {
        "close": float(closes[i]),
        "mean_200": mean_200,
        "below_200d": bool(below),
        "drawdown_pct": 100.0 * (float(closes[i]) / float(np.max(closes[max(0, i - DRAWDOWN_WINDOW + 1) : i + 1])) - 1.0),
    }
    for w in FACT_WINDOWS:
        spy_facts[f"return_{w}"] = _num(100.0 * (closes[i] / closes[i - w] - 1.0)) if i >= w else None
    breadth_flags = [price_signal(c, len(c) - 1) for tk, c in fund_closes.items() if tk in SECTOR_FUNDS]
    breadth_flags = [f for f in breadth_flags if not np.isnan(f)]
    breadth = 100.0 * float(np.mean(breadth_flags)) if breadth_flags else None
    if breadth is None:
        reasons.append("breadth_missing: no sector fund with 200 closes")

    series_facts: dict[str, Any] = {}
    for sid in FRED_SERIES:
        kind = "bp" if sid in BP_SERIES else "log" if sid in LOG_SERIES else "pts"
        entry: dict[str, Any] = {
            "level": _num(fred_aligned[sid][i]),
            "observed": _iso(fred_obs[sid]) if fred_obs[sid] is not None else None,
            "change_unit": {"bp": "basis points", "log": "log change, %", "pts": "points"}[kind],
        }
        for w in FACT_WINDOWS:
            entry[f"change_{w}"] = _num(_changes(fred_aligned[sid], w, kind)[i])
        series_facts[sid] = entry

    # ---- change series and z, per window
    spy_lr = {w: _changes(closes, w, "log") for w in WINDOWS}
    fund_lr = {tk: {w: _changes(v, w, "log") for w in WINDOWS} for tk, v in fund_aligned.items()}
    sector_facts: dict[str, Any] = {}
    for tk, per in fund_lr.items():
        e: dict[str, Any] = {"sector_id": SECTOR_FUNDS[tk][0] if tk in SECTOR_FUNDS else None}
        for w in WINDOWS:
            e[f"log_return_{w}"] = _num(per[w][i])
            e[f"relative_{w}"] = _num(per[w][i] - spy_lr[w][i])
        sector_facts[tk] = e

    scores: dict[str, Any] = {}
    descriptions: dict[int, dict[str, str]] = {}
    drivers: dict[int, str] = {}
    for w in WINDOWS:
        legs: dict[str, dict[str, Any]] = {}

        def leg(name: str, change: np.ndarray, unit: str) -> float | None:
            z = z_of_change(change)
            legs[name] = {"change": _num(change[-1]), "unit": unit, "z": z}
            return z

        z2 = leg("DGS2", _changes(fred_aligned["DGS2"], w, "bp"), "basis points")
        z10 = leg("DGS10", _changes(fred_aligned["DGS10"], w, "bp"), "basis points")
        zbaa = leg("BAA10Y", _changes(fred_aligned["BAA10Y"], w, "bp"), "basis points")
        zoil = leg("DCOILWTICO", _changes(fred_aligned["DCOILWTICO"], w, "log"), "log change, %")
        zbe = leg("T10YIE", _changes(fred_aligned["T10YIE"], w, "bp"), "basis points")
        if all(tk in fund_lr for tk in CYCLICALS + DEFENSIVES):
            cyc = np.mean([fund_lr[tk][w] for tk in CYCLICALS], axis=0)
            dfn = np.mean([fund_lr[tk][w] for tk in DEFENSIVES], axis=0)
            zcd = leg("cyclicals_minus_defensives", cyc - dfn, "log return difference, points")
        else:
            legs["cyclicals_minus_defensives"] = {"change": None, "unit": "log return difference, points", "z": None}
            zcd = None
        neg = lambda x: None if x is None else -x  # noqa: E731
        contributions = {
            "rates": [("DGS2", z2), ("DGS10", z10)],
            "growth_credit": [("DGS10", neg(z10)), ("BAA10Y", zbaa), ("cyclicals_minus_defensives", neg(zcd))],
            "inflation": [("DCOILWTICO", zoil), ("T10YIE", zbe)],
        }
        w_scores: dict[str, float | None] = {}
        best_leg: dict[str, str | None] = {}
        for name, parts in contributions.items():
            w_scores[name] = mean_of_legs([z for _, z in parts])
            have = [(z, k) for k, z in parts if z is not None]
            best_leg[name] = max(have)[1] if have else None
            for k, z in parts:
                if z is None:
                    reasons.append(f"leg_missing: {name}_{w} leg {k}")
        sector_z: dict[str, float] = {}
        for tk in SECTOR_FUNDS:
            if tk in fund_lr:
                z = z_of_change(fund_lr[tk][w] - spy_lr[w])
                if z is not None:
                    sector_z[tk] = z
                    continue
            reasons.append(f"leg_missing: sector_{w} fund {tk}")
        worst = min(sector_z, key=sector_z.get) if sector_z else None
        sector_name = f"sector_led:{SECTOR_FUNDS[worst][0]}" if worst else None
        sector_score = -sector_z[worst] if worst else None
        candidates: dict[str, float | None] = dict(w_scores)
        candidates[sector_name or "sector_led:none"] = sector_score
        driver = pick_driver(candidates)
        drivers[w] = driver

        def say(name: str) -> str:
            if name.startswith("sector_led:"):
                rel = sector_facts[worst][f"relative_{w}"]
                return f"{SECTOR_FUNDS[worst][1]} ({_fmt(rel)} points against the index in {w} sessions)"
            k = best_leg[name]
            ch = legs[k]["change"]
            phrase = {
                "DGS2": f"2-year yield {_fmt(ch, 0)} basis points",
                "DGS10": f"10-year yield {_fmt(ch, 0)} basis points",
                "BAA10Y": f"Baa credit spread {_fmt(ch, 0)} basis points",
                "DCOILWTICO": f"oil {_fmt(ch, 0)}%",
                "T10YIE": f"10-year breakeven inflation {_fmt(ch, 0)} basis points",
                "cyclicals_minus_defensives": f"cyclicals against defensives {_fmt(ch)} points",
            }[k]
            return f"{name} ({phrase} in {w} sessions)"

        descriptions[w] = {driver: say(driver)} if driver != "unclear" else {}
        scores[str(w)] = {
            "rates": w_scores["rates"],
            "growth_credit": w_scores["growth_credit"],
            "inflation": w_scores["inflation"],
            "sector": sector_score,
            "sector_fund": worst,
            "sector_id": SECTOR_FUNDS[worst][0] if worst else None,
            "driver": driver,
            "legs": legs,
            "sector_z": {tk: z for tk, z in sector_z.items()},
        }

    rel21 = [(tk, sector_facts[tk]["relative_21"]) for tk in SECTOR_FUNDS if tk in sector_facts and sector_facts[tk]["relative_21"] is not None]
    rel21.sort(key=lambda t: t[1], reverse=True)
    leaders = [{"fund": tk, "sector_id": SECTOR_FUNDS[tk][0], "relative_21": r, "log_return_21": sector_facts[tk]["log_return_21"]} for tk, r in rel21[:3]]
    laggards = [{"fund": tk, "sector_id": SECTOR_FUNDS[tk][0], "relative_21": r, "log_return_21": sector_facts[tk]["log_return_21"]} for tk, r in rel21[-3:][::-1]]

    driver, window, backdrop = publish_driver(step, drivers[21], drivers[63])

    summary = None
    if step is not None:
        parts = [f"{step.capitalize()} since {since}."]
        if driver is not None and driver not in ("none",):
            if driver == "unclear":
                parts.append("No single driver stands out.")
            else:
                parts.append(f"Led by {descriptions[window][driver]}.")
        if backdrop is not None:
            parts.append(f"Backdrop: {descriptions[63][backdrop]}.")
        if len(leaders) == 3:
            names = [SECTOR_FUNDS[d["fund"]][1] for d in leaders]
            text = f"{', '.join(names[:2])} and {names[2]} lead."
            parts.append(text[0].upper() + text[1:])
        summary = " ".join(parts)

    doc.update(
        {
            "step": step,
            "since": since,
            "driver": driver,
            "driver_window": window,
            "backdrop": backdrop,
            "summary": summary,
            "facts": {
                "spy": spy_facts,
                "vix": series_facts["VIXCLS"],
                "breadth_below_200d_pct": breadth,
                "series": {k: v for k, v in series_facts.items() if k != "VIXCLS"},
                "sectors": sector_facts,
            },
            "scores": scores,
            "leaders": leaders,
            "laggards": laggards,
            "inputs": {
                "spy_last_date": spy_last.isoformat(),
                "series_observed": {k: _iso(v) if v is not None else None for k, v in fred_obs.items()},
            },
            "degraded": step is None,
        }
    )
    return doc


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_market_state(
    db_path: str | Path, outputs_dir: str | Path = "outputs", now: datetime | None = None
) -> tuple[Path, Path]:
    """Write `<outputs>/market_state.json` and `<outputs>/archive/<build day>/market_state/market_state.json`."""
    doc = build_market_state(db_path, now)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"  # a NaN is a bug here, never a value
    out = Path(outputs_dir)
    main_path = out / FILENAME
    archive_path = out / "archive" / doc["built_at"][:10] / ARCHIVE_SUBDIR / FILENAME
    _atomic_write_text(main_path, text)
    _atomic_write_text(archive_path, text)
    return main_path, archive_path
