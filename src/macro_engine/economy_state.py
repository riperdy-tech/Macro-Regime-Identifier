"""The daily economy state: `outputs/economy_state.json` (WO_ME1, MRI review step 2).

Where inflation, policy, jobs and growth stand against fixed references and which way they are moving, from the newest
published monthly, weekly and daily FRED observations in `raw_observations`. The store is opened read-only; nothing
existing is changed. There is no combined growth-and-inflation label (measured: "mixed" in 74% of months). All monthly
arithmetic is by calendar month, found by date: a missing month leaves a gap and shifts nothing. Every definition is in
WO_ME1; the measured table behind the words is `EVIDENCE`. The words are description, not rules.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from macro_engine.market_state import _atomic_write_text

SCHEMA = "mri.economy_state.v1"
FILENAME = "economy_state.json"
ARCHIVE_SUBDIR = "economy_state"
STALE_DAYS = 120
TARGET = 2.0
INFLATION_SERIES = ("PCEPI", "PCEPILFE", "CPIAUCSL", "CPILFESL")
SERIES = (*INFLATION_SERIES, "UNRATE", "PAYEMS", "INDPRO", "FEDFUNDS", "ICSA", "DGS2")
DIRECTION_BAND = 0.5  # inflation and payrolls, percentage points of annualised rate
IP_BAND = 1.0  # industrial production
FUNDS_BAND = 0.125  # half of one standard 0.25 move
MARKET_CUTS = -0.25
MARKET_HIKES = 1.5
SAHM_SOFT = 0.3
SAHM_DETERIORATING = 0.5

EVIDENCE = {
    "study": "WO_ME1",
    "measured": "2026-11-20 on stored series, revised data, 1990 to 2026; descriptive, in-sample",
    "inflation_direction": "6-month rate minus 12-month rate, +-0.5 (PCE): rising in 17% of months, the 12-month rate was higher six months later in 87% of them; falling in 18%, lower later in 78%; CPI 84% and 70%",
    "inflation_level": "share of months since 1990 (PCE): below 26%, near 38%, above 24%, well above 12%",
    "policy_direction": "funds 6-month change +-0.125: tightening 30% of months, higher again six months later in 69%; easing 32%, lower again in 63%",
    "two_year_minus_funds": ">= +0.25 in 56% of months, rate rose afterwards in 45% (no signal); >= +1.5 in 5%, rose in 78%; <= -0.25 in 17%, fell in 74%",
    "real_funds_rate": "median since 1990 +0.54; above zero in 58% of months",
    "unemployment_rise_from_low": ">= 0.5 flagged all four recessions since 1990, 2 to 3 months after the start, plus two spells not near a recession (2003, 2024); >= 0.3 flagged 0 to 2 months after the start, five such spells",
    "growth_direction": "payrolls +-0.5 (1990-2019): growth higher six months later in 98%, lower in 88%; production +-1.0: 83% and 80%",
    "payrolls_3m_rate_below_zero": "16% of months; 85% of recession months; 46% of flagged months were recession months",
    "no_quadrant": "growth-and-inflation quadrant label is 'mixed' in 74% of months and misnames the rest: not built",
}


# --------------------------------------------------------------------------- registered definitions


def rate_m(x: dict[int, float], t: int, m: int) -> float | None:
    """Annualised percent rate over m months ending at month key t; None if either end is missing."""
    a, b = x.get(t), x.get(t - m)
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return ((a / b) ** (12.0 / m) - 1.0) * 100.0


def newest(x: dict[int, float], fn) -> tuple[float | None, int | None]:
    """(value, month) of fn at the newest month in x where fn is not None."""
    for t in sorted(x, reverse=True):
        v = fn(t)
        if v is not None:
            return v, t
    return None, None


def latest_rate(x: dict[int, float], m: int) -> tuple[float | None, int | None]:
    return newest(x, lambda t: rate_m(x, t, m))


def rate_gap(x: dict[int, float], short: int, long: int) -> tuple[float | None, int | None]:
    """rate_short minus rate_long at the newest month where both exist."""

    def gap(t: int) -> float | None:
        a, b = rate_m(x, t, short), rate_m(x, t, long)
        return None if a is None or b is None else a - b

    return newest(x, gap)


def direction_word(gap: float | None, band: float = DIRECTION_BAND) -> str | None:
    """rising / falling / steady from a gap of short minus long rate; None when the gap is missing."""
    if gap is None:
        return None
    if gap >= band:
        return "rising"
    if gap <= -band:
        return "falling"
    return "steady"


def inflation_level(rate_12: float | None) -> str | None:
    """Level against the 2% target, from PCEPI rate_12."""
    if rate_12 is None:
        return None
    if rate_12 < 1.5:
        return "below target"
    if rate_12 <= 2.5:
        return "near target"
    if rate_12 <= 3.5:
        return "above target"
    return "well above target"


def policy_direction(change_6m: float | None) -> str | None:
    if change_6m is None:
        return None
    if change_6m >= FUNDS_BAND:
        return "tightening"
    if change_6m <= -FUNDS_BAND:
        return "easing"
    return "on hold"


def market_word(gap: float | None) -> str | None:
    """prices cuts / prices hikes from the 2-year yield minus the funds rate; None in between (a normal state)."""
    if gap is None:
        return None
    if gap <= MARKET_CUTS:
        return "prices cuts"
    if gap >= MARKET_HIKES:
        return "prices hikes"
    return None


def rise_from_low(u: dict[int, float]) -> tuple[float | None, int | None]:
    """(rise, latest month): the mean of the last 3 months' UNRATE (at least 2 present) minus the lowest such 3-month
    mean over the previous 12 months (at least 9 of those means formed)."""
    if not u:
        return None, None
    last = max(u)

    def mean3(t: int) -> float | None:
        vals = [u[k] for k in (t - 2, t - 1, t) if k in u]
        return float(np.mean(vals)) if len(vals) >= 2 else None

    cur = mean3(last)
    prev = [p for p in (mean3(t) for t in range(last - 12, last)) if p is not None]
    if cur is None or len(prev) < 9:
        return None, last
    return cur - min(prev), last


def jobs_state(rise: float | None) -> str | None:
    if rise is None:
        return None
    rise = round(rise, 9)
    if rise >= SAHM_DETERIORATING:
        return "deteriorating"
    if rise >= SAHM_SOFT:
        return "softening"
    return "steady"


def growth_state(payroll_rate_3: float | None) -> str | None:
    if payroll_rate_3 is None:
        return None
    return "contracting" if payroll_rate_3 < 0 else "expanding"


def growth_direction(g_pay: float | None, g_ip: float | None) -> str | None:
    """Count up legs (gap >= +band) and down legs (gap <= -band): accelerating if some up and none down, slowing if
    some down and none up, else steady; None when neither leg exists."""
    legs = [(g_pay, DIRECTION_BAND), (g_ip, IP_BAND)]
    if all(g is None for g, _ in legs):
        return None
    up = sum(1 for g, b in legs if g is not None and g >= b)
    down = sum(1 for g, b in legs if g is not None and g <= -b)
    if up >= 1 and down == 0:
        return "accelerating"
    if down >= 1 and up == 0:
        return "slowing"
    return "steady"


def claims_facts(values: list[float]) -> tuple[float | None, float | None]:
    """(4-week mean of the last four values, its percent above the lowest 4-week mean of the last 52 weeks); the
    percent is None unless 52 four-week means exist."""
    if len(values) < 4:
        return None, None
    means = [float(np.mean(values[i - 3 : i + 1])) for i in range(3, len(values))]
    cur = means[-1]
    window = means[-52:]
    if len(window) < 52 or min(window) <= 0:
        return cur, None
    return cur, (cur / min(window) - 1.0) * 100.0


# --------------------------------------------------------------------------- the state


def _mk(d: date) -> int:
    return d.year * 12 + d.month - 1


def _label(k: int | None) -> str | None:
    return None if k is None else f"{k // 12}-{k % 12 + 1:02d}"


def _month_start(k: int) -> date:
    return date(k // 12, k % 12 + 1, 1)


def _pt(value: float | None, month: str | None) -> dict[str, Any]:
    return {"value": value, "month": month}


def _fmt(x: float, nd: int = 1) -> str:
    return f"{x:+.{nd}f}".replace("-", "−")


def _load(db_path: str | Path) -> dict[str, list[tuple[date, float]]]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "SELECT series_id, date, value FROM raw_observations WHERE value IS NOT NULL AND series_id IN ("
            + ",".join("?" * len(SERIES))
            + ") QUALIFY row_number() OVER (PARTITION BY series_id, date ORDER BY realtime_start DESC) = 1 "
            "ORDER BY series_id, date",
            list(SERIES),
        ).fetchall()
    finally:
        con.close()
    out: dict[str, list[tuple[date, float]]] = {sid: [] for sid in SERIES}
    for sid, d, v in rows:
        out[sid].append((d if isinstance(d, date) else d.date(), float(v)))
    return out


def _inflation_series(x: dict[int, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in (12, 6, 3):
        v, t = latest_rate(x, m)
        out[f"rate_{m}"] = _pt(v, _label(t))
    gap, t = rate_gap(x, 6, 12)
    out["gap_6_minus_12"] = _pt(gap, _label(t))
    out["direction"] = direction_word(gap)
    return out


def _rates(x: dict[int, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in (3, 6, 12):
        v, t = latest_rate(x, m)
        out[f"rate_{m}"] = _pt(v, _label(t))
    return out


def build_economy_state(db_path: str | Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    build_day = now.astimezone(timezone.utc).date()
    rows = _load(db_path)
    reasons: list[str] = []
    x: dict[str, dict[int, float]] = {sid: {_mk(d): v for d, v in rows[sid]} for sid in SERIES if sid not in ("ICSA", "DGS2")}
    for sid in SERIES:
        if not rows[sid]:
            reasons.append(f"{sid}_missing: no observations in the store")

    def stale(axis: str, sid: str) -> bool:
        if not x[sid]:
            return False
        age = (build_day - _month_start(max(x[sid]))).days
        if age > STALE_DAYS:
            reasons.append(f"{axis}_stale: newest {sid} month {_label(max(x[sid]))} is {age} days before {build_day}, more than {STALE_DAYS}")
            return True
        return False

    # ---- inflation
    series = {sid: _inflation_series(x[sid]) for sid in INFLATION_SERIES}
    pce12 = series["PCEPI"]["rate_12"]["value"]
    inf_stale = stale("inflation", "PCEPI")
    level = None if inf_stale else inflation_level(pce12)
    direction = None if inf_stale else series["PCEPI"]["direction"]
    core_direction = None if inf_stale else series["PCEPILFE"]["direction"]
    inflation = {
        "level": level,
        "direction": direction,
        "core_direction": core_direction,
        "gap_to_target": _pt(None if pce12 is None else pce12 - TARGET, series["PCEPI"]["rate_12"]["month"]),
        "target": TARGET,
        "series": series,
    }
    if x["PCEPI"] and not inf_stale and level is None:
        reasons.append("inflation_level_unavailable: PCEPI rate_12 cannot be formed")

    # ---- policy
    funds, funds_t = newest(x["FEDFUNDS"], lambda t: x["FEDFUNDS"][t])
    # change_6m is taken at the funds rate's own latest month; an older month would be a different reading
    change = None if funds_t is None or funds_t - 6 not in x["FEDFUNDS"] else funds - x["FEDFUNDS"][funds_t - 6]
    change_t = funds_t if change is not None else None
    pol_stale = stale("policy", "FEDFUNDS")
    p_direction = None if pol_stale else policy_direction(change)
    real = None if funds is None or pce12 is None else funds - pce12
    dgs2 = rows["DGS2"][-1] if rows["DGS2"] else None
    gap2 = None if dgs2 is None or funds is None else dgs2[1] - funds
    p_market = None if pol_stale else market_word(gap2)
    policy = {
        "direction": p_direction,
        "market": p_market,
        "funds_rate": _pt(funds, _label(funds_t)),
        "change_6m": _pt(change, _label(change_t)),
        "real_rate": {
            "value": real,
            "funds_month": _label(funds_t),
            "inflation_month": series["PCEPI"]["rate_12"]["month"],
        },
        "two_year_minus_funds": {
            "value": gap2,
            "dgs2_date": None if dgs2 is None else dgs2[0].isoformat(),
            "funds_month": _label(funds_t),
        },
    }
    if x["FEDFUNDS"] and not pol_stale and p_direction is None:
        reasons.append("policy_direction_unavailable: no FEDFUNDS value six months before its latest month")

    # ---- jobs
    u = x["UNRATE"]
    rise, rise_t = rise_from_low(u)
    jobs_stale = stale("jobs", "UNRATE")
    j_state = None if jobs_stale else jobs_state(rise)
    unemployment, un_t = newest(u, lambda t: u[t])
    pay_avg, pay_avg_t = newest(
        x["PAYEMS"], lambda t: (x["PAYEMS"][t] - x["PAYEMS"][t - 3]) / 3.0 if t - 3 in x["PAYEMS"] else None
    )
    pay12, pay12_t = latest_rate(x["PAYEMS"], 12)
    icsa_values = [v for _, v in rows["ICSA"]]
    claims_4w, claims_pct = claims_facts(icsa_values)
    claims_date = rows["ICSA"][-1][0].isoformat() if rows["ICSA"] else None
    jobs = {
        "state": j_state,
        "unemployment": _pt(unemployment, _label(un_t)),
        "rise_from_low": _pt(rise, _label(rise_t)),
        "payrolls_3m_average_change": _pt(pay_avg, _label(pay_avg_t)),
        "payrolls_rate_12": _pt(pay12, _label(pay12_t)),
        "claims_4w": {"value": claims_4w, "date": claims_date},
        "claims_above_52w_low_pct": {"value": claims_pct, "date": claims_date},
    }
    if u and not jobs_stale and j_state is None:
        reasons.append("jobs_state_unavailable: too few UNRATE months for the rise from its 12-month low")

    # ---- growth
    pay_rates, ip_rates = _rates(x["PAYEMS"]), _rates(x["INDPRO"])
    g_pay, _ = rate_gap(x["PAYEMS"], 6, 12)
    g_ip, _ = rate_gap(x["INDPRO"], 6, 12)
    growth_stale = stale("growth", "PAYEMS")
    g_state = None if growth_stale else growth_state(pay_rates["rate_3"]["value"])
    g_direction = None if growth_stale else growth_direction(g_pay, g_ip)
    growth = {
        "state": g_state,
        "direction": g_direction,
        "payrolls": pay_rates,
        "industrial_production": ip_rates,
    }
    if x["PAYEMS"] and not growth_stale and g_state is None:
        reasons.append("growth_state_unavailable: PAYEMS rate_3 cannot be formed")

    degraded = level is None or p_direction is None or j_state is None or g_state is None
    seen = [rows[s][-1][0] for s in SERIES if rows[s]]
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "asof": max(seen).isoformat() if seen else None,
        "built_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "shadow",
        "changes": "additive artifact; no score, anchor, table or other output reads it or is changed by it",
        "summary": None,
        "inflation": inflation,
        "policy": policy,
        "jobs": jobs,
        "growth": growth,
        "evidence": json.loads(json.dumps(EVIDENCE)),
        "inputs": {"series_last": {sid: rows[sid][-1][0].isoformat() if rows[sid] else None for sid in SERIES}},
        "degraded": degraded,
        "reasons": reasons,
    }
    doc["summary"] = _summary(doc)
    return doc


def _summary(doc: dict[str, Any]) -> str:
    inf, pol, jobs, gro = doc["inflation"], doc["policy"], doc["jobs"], doc["growth"]

    if inf["level"] is None:
        a = "Inflation: no reading."
    else:
        p = inf["series"]["PCEPI"]
        facts = f"PCE prices {_fmt(p['rate_12']['value'])}% on the year"
        if p["rate_6"]["value"] is not None:
            facts += f", {_fmt(p['rate_6']['value'])}% annualised over six months"
        month = p["rate_12"]["month"]
        facts += f", to {calendar.month_name[int(month[5:])]}"
        a = f"Inflation {inf['level']}" + (f" and {inf['direction']}" if inf["direction"] else "") + f" ({facts})"
        if inf["core_direction"] is not None and inf["core_direction"] != inf["direction"]:
            a += f", with core inflation {inf['core_direction']}"
        a += "."

    if pol["direction"] is None:
        b = "Policy: no reading."
    else:
        ch = pol["change_6m"]["value"]
        b = f"Policy {pol['direction']}: funds rate {pol['funds_rate']['value']:.2f}%"
        if ch is not None:
            b += ", " + (f"up {ch:.2f}" if ch > 0 else f"down {-ch:.2f}" if ch < 0 else "unchanged") + " in six months"
        real = pol["real_rate"]["value"]
        if real is not None:
            b += f", {abs(real):.1f} " + ("above" if real >= 0 else "below") + " inflation"
        if pol["market"] is not None:
            b += f"; the 2-year yield {pol['market']}"
        b += "."

    if jobs["state"] is None:
        c = "Jobs: no reading."
    else:
        c = f"Jobs {jobs['state']}: "
        un = jobs["unemployment"]["value"]
        c += f"unemployment {un:.1f}%" if un is not None else "unemployment not available"
        r = jobs["rise_from_low"]["value"]
        if r is not None:
            c += ", no rise from its 12-month low" if round(r, 1) <= 0 else f", up {r:.1f} points from its 12-month low"
        c += "."

    if gro["state"] is None:
        d = "Growth: no reading."
    else:
        d = f"Growth {gro['state']}" + (f" and {gro['direction']}" if gro["direction"] else "")
        p12 = gro["payrolls"]["rate_12"]["value"]
        if p12 is not None:
            d += f": payrolls {_fmt(p12)}% on the year"
        d += "."
    return " ".join((a, b, c, d))


def write_economy_state(
    db_path: str | Path, outputs_dir: str | Path = "outputs", now: datetime | None = None
) -> tuple[Path, Path]:
    """Write `<outputs>/economy_state.json` and `<outputs>/archive/<build day>/economy_state/economy_state.json`."""
    doc = build_economy_state(db_path, now)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"  # a NaN is a bug here, never a value
    out = Path(outputs_dir)
    main_path = out / FILENAME
    archive_path = out / "archive" / doc["built_at"][:10] / ARCHIVE_SUBDIR / FILENAME
    _atomic_write_text(main_path, text)
    _atomic_write_text(archive_path, text)
    return main_path, archive_path
