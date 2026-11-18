"""What the brief starts from: the five regime dimensions as measured, their data dates, their own history of
month-to-month moves, and the engine's regime arithmetic on a set of scores.

The live store is opened read-only. The regime arithmetic is the engine's own `build_regimes_from_dimensions`; it is
called, never re-implemented.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from macro_engine.regimes.config import load_regime_config
from macro_engine.regimes.scoring import build_regimes_from_dimensions

# THE JUDGEMENT IN THIS DESIGN (WO_MB1 item 5, SPEC_macro_combined_v0.1): the three percentiles of a dimension's own
# history of one-month absolute changes in its stored score that a size word maps to. Nothing is fitted; these three
# numbers are the whole of the judgement, and the output shows the reading with every accepted change halved and doubled.
SIZE_PERCENTILES = {"small": 25, "typical": 50, "large": 90}

# What each regime dimension means in plain words. "higher" and "lower" in the brief always mean the SCORE rises or falls.
DIMENSION_TEXT = {
    "growth_momentum": ("Growth momentum. The score rises when output and jobs are growing faster and unemployment and "
                        "jobless claims are falling; it falls when growth slows or unemployment or claims rise."),
    "inflation_pressure": ("Inflation pressure. The score rises when consumer price inflation (CPI and the PCE price "
                           "index, year on year) is higher or accelerating; it falls when inflation is lower."),
    "policy_stance": ("Policy stance, scored so that a HIGHER score means EASIER policy. The score falls when the central "
                      "bank raises its policy rate or has just been raising it, and rises when the rate is cut or is low."),
    "credit_liquidity": ("Credit and liquidity conditions, scored so that a HIGHER score means LOOSER conditions. The "
                         "score falls when corporate bond spreads widen or financial conditions tighten, and rises when "
                         "spreads narrow or conditions ease."),
    "yield_curve": ("Yield curve slope (10-year minus 2-year Treasury yield). The score rises when the curve steepens "
                    "and falls when it flattens or inverts."),
}


def regime_dimensions(config_path: str | Path) -> list[str]:
    """The dimensions that feed at least one enabled regime, in a fixed order (the five regime dimensions)."""
    cfg = load_regime_config(config_path)
    return sorted({d.dimension_id for r in cfg.regimes if r.enabled for d in r.dimensions})


def read_current_regime(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dimension_series(config_path: str | Path, dims: list[str]) -> dict[str, dict[str, int]]:
    """{dimension: {series id: publication lag in days}} from the engine's own configuration."""
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    feature_series = {f["feature_id"]: f["series_id"] for f in data.get("features", [])}
    lag = {s["series_id"]: int(s.get("publication_lag_days") or 0) for s in data.get("sources", [])}
    out: dict[str, dict[str, int]] = {}
    for dim in data.get("dimensions", []):
        if dim["dimension_id"] not in dims:
            continue
        series = {}
        for feat in dim.get("features", []):
            sid = feature_series.get(feat["feature_id"])
            if sid:
                series[sid] = lag.get(sid, 0)
        out[dim["dimension_id"]] = series
    return out


def data_dates(current: dict, dim_series: dict[str, dict[str, int]]) -> dict[str, dict]:
    """For each dimension: the newest observation date of the series that feed it (`data_date`, as the order says), and
    `known_by`: the newest of (observation date + that series' configured publication lag), an estimate of the day
    by which every observation in the score was public. `known_by` is shown in the output for information only: the
    checks use `data_date`, as the work order says."""
    latest = {f["series_id"]: f["latest_valid_date"] for f in current.get("feature_health", [])
              if f.get("latest_valid_date")}
    out = {}
    for dim, series in dim_series.items():
        rows = {}
        for sid, lag in series.items():
            if sid in latest:
                obs = pd.Timestamp(latest[sid]).date()
                rows[sid] = {"observation_date": obs.isoformat(), "lag_days": lag,
                             "known_by": (obs + timedelta(days=lag)).isoformat()}
        out[dim] = {
            "data_date": max((r["observation_date"] for r in rows.values()), default=None),
            "known_by": max((r["known_by"] for r in rows.values()), default=None),
            "series": rows,
        }
    return out


def measured_scores(current: dict, dims: list[str]) -> dict[str, dict]:
    """{dimension: {score, valid, reason}} for the report month, from current_regime.json."""
    by = {x["dimension_id"]: x for x in current["dimension_scores"]}
    return {d: {"score": by[d]["score"], "valid": bool(by[d]["valid"]), "reason": by[d].get("reason")}
            for d in dims if d in by}


def score_history(store_path: str | Path, dims: list[str]) -> pd.DataFrame:
    """Monthly stored scores (valid rows only), one column per dimension. The store is opened read-only."""
    import duckdb

    con = duckdb.connect(str(store_path), read_only=True)
    try:
        ds = con.execute(
            "select dimension_id, date, score from dimension_scores where valid and score is not null").df()
    finally:
        con.close()
    ds["date"] = pd.to_datetime(ds["date"])
    wide = ds[ds["dimension_id"].isin(dims)].pivot(index="date", columns="dimension_id", values="score").sort_index()
    return wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="MS"))


def change_percentiles(history: pd.Series, current_month: pd.Timestamp) -> dict | None:
    """The three size percentiles of the one-month absolute changes of the stored score, over all months BEFORE the
    current one (a change counts when its later month is earlier than the current month). None when there is no change."""
    s = history[history.index < current_month]
    changes = s.diff().abs().dropna()
    if changes.empty:
        return None
    out = {name: float(np.percentile(changes.to_numpy(), q)) for name, q in SIZE_PERCENTILES.items()}
    out["n_changes"] = int(len(changes))
    return out


def regime_reading(scores: dict[str, float | None], report_date: str, current: dict, config_path: str | Path) -> dict:
    """The engine's own regime arithmetic on a set of dimension scores. `scores` replaces the score of the named
    dimensions in the report month's dimension rows; every other row is as in current_regime.json."""
    cfg = load_regime_config(config_path)
    rows = []
    for x in current["dimension_scores"]:
        score = scores.get(x["dimension_id"], x["score"])
        rows.append({"dimension_id": x["dimension_id"], "date": report_date, "score": score,
                     "valid": bool(x["valid"]), "reason": x.get("reason")})
    result = build_regimes_from_dimensions(pd.DataFrame(rows), cfg.regimes, cfg.scoring)
    sc = result.regime_scores
    sc = sc[sc["valid"]]
    probs = {r.regime_id: float(r.probability) for r in sc.itertuples()}
    health = result.regime_health
    leader = None
    if len(health) and bool(health.iloc[0]["valid"]):
        leader = str(health.iloc[0]["dominant_regime"])
    return {"probabilities": probs, "leader": leader}
