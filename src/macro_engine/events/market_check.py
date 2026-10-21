"""E2 market response for the candidate register (plan v2 §6, erratum v2.1, rulings of 2026-10-22).

Answers one question per (candidate, theme): what did the market do in the window after the news? It never says whether the
event happened (that is the corroboration field) and it never changes a number (D1).

Per instrument (config/event_theme_map.yaml `instruments`):

- transform: basis-point change (rates, spreads, breakevens, curve) or log change in % (prices, ETFs, WTI, VIX);
- P_ref: the last session close at or before the onset instant (`published_at`), a close being 16:00 America/New_York on
  the observation date (erratum v2.1);
- move: the change from P_ref to the k-th stored observation after it (k = 2; 1 and 3 recorded for information only);
- z: move / robust SD, the robust SD being MAD x 1.4826 of every overlapping k-observation move among the 504 observations
  ending at P_ref. Nothing after P_ref enters the baseline. Fewer than 250 moves, or MAD = 0, gives data_unavailable;
- result: aligned (z >= +2 in the expected direction), opposed (z >= +2 against it), no_signal, pending (the k-th
  observation has not arrived and the series is within its lag limit), data_unavailable (with a reason).

A check is keyed by (instrument, reference close date, window): candidates that share the key share one check
(`shared_with`) and any aggregate counts the key once. Per theme, the family rule decides: data_unavailable or pending when
the primary family is; mixed when families are both aligned and opposed; aligned when the primary family is aligned (or two
families are); opposed when the primary family is opposed; otherwise no_signal. Instruments listed as the same family as the
primary are recorded and never counted again.

Company-specific checks (resolved entities with a ticker): the stock's k-session log return minus its sector ETF's,
standardised the same way, recorded as `equity_reaction_only`. Bond data is unavailable and is said to be.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from macro_engine.events.confirmation_prices import weekday_lag
from macro_engine.events.register import market_check_key
from macro_engine.shocks.register import _oil_levels_with_tail

REPO_ROOT = Path(__file__).resolve().parents[3]
EVENTS_CONFIG_PATH = REPO_ROOT / "config" / "events.yaml"
THEME_MAP_PATH = REPO_ROOT / "config" / "event_theme_map.yaml"
COMPANY_SECTORS_PATH = REPO_ROOT / "config" / "event_company_sectors.yaml"
SECTORS_PATH = REPO_ROOT / "config" / "sectors.yaml"
SHOCKS_CONFIG_PATH = REPO_ROOT / "config" / "shocks.yaml"

ALIGNED, OPPOSED, NO_SIGNAL, PENDING, UNAVAILABLE, MIXED = (
    "aligned", "opposed", "no_signal", "pending", "data_unavailable", "mixed")
DISPLAY_ONLY, NOT_SCORED, RECORDED = "display_only", "not_scored", "recorded"


@dataclass(frozen=True)
class E2Config:
    k: int
    info_windows: tuple[int, ...]
    mad_scale: float
    baseline: int
    min_obs: int
    z_threshold: float
    current_fraction: float
    close_hour: int
    close_minute: int
    tz: str
    max_lag_weekdays: int
    oil_anchor_gap_days: int


def load_e2_config(path: str | Path = EVENTS_CONFIG_PATH, shocks_path: str | Path = SHOCKS_CONFIG_PATH) -> E2Config:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    e2 = doc["e2"]
    hh, mm = str(e2["p_ref_close_time_local"]["value"]).split(":")
    lag = doc["unratified"]["series_staleness_limits"]["proposed_default_weekdays"]
    shocks = yaml.safe_load(Path(shocks_path).read_text(encoding="utf-8"))
    return E2Config(
        k=int(e2["primary_window_sessions"]["value"]),
        info_windows=tuple(int(x) for x in e2["info_window_sessions"]["value"]),
        mad_scale=float(e2["robust_sd"]["mad_scale"]["value"]),
        baseline=int(e2["robust_sd"]["baseline_sessions"]["value"]),
        min_obs=int(e2["robust_sd"]["min_observations"]["value"]),
        z_threshold=float(e2["z_threshold"]["value"]),
        current_fraction=float(e2["response_current_fraction"]["value"]),
        close_hour=int(hh), close_minute=int(mm),
        tz=str(e2["p_ref_timezone"]["value"]),
        max_lag_weekdays=int(lag),
        oil_anchor_gap_days=int(shocks["shocks"]["oil_shock"]["daily_tail"]["max_anchor_gap_days"]),
    )


def load_theme_map(path: str | Path = THEME_MAP_PATH) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_company_sectors(path: str | Path = COMPANY_SECTORS_PATH,
                         sectors_path: str | Path = SECTORS_PATH) -> tuple[dict[str, str], dict[str, int]]:
    """(Yahoo sector string -> benchmark ETF, theme -> expected company sign)."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sectors = yaml.safe_load(Path(sectors_path).read_text(encoding="utf-8"))
    proxy = {s["sector_id"]: s["proxy_ticker"] for s in sectors.get("sectors", [])
             if s.get("proxy_ticker") and not s.get("parent_sector_id")}
    etf = {name: proxy[sid] for name, sid in doc["yahoo_sector_to_sector_id"].items() if sid in proxy}
    return etf, {k: int(v) for k, v in (doc.get("company_expected_sign") or {}).items()}


# ----------------------------------------------------------------------------- series access


class SeriesSource:
    """Reads instrument levels from the store once each. FRED rows keep the latest vintage per date."""

    def __init__(self, con: Any, theme_map: dict[str, Any], cfg: E2Config):
        self.con, self.instruments, self.cfg = con, theme_map["instruments"], cfg
        self._cache: dict[str, tuple[pd.Series, pd.Timestamp | None]] = {}

    def _fred(self, series_id: str) -> pd.Series:
        df = self.con.execute(
            "SELECT date, value FROM raw_observations WHERE series_id = ? AND value IS NOT NULL "
            "ORDER BY date, realtime_start", [series_id]).df()
        return _as_series(df)

    def _market(self, series_id: str) -> pd.Series:
        try:
            df = self.con.execute(
                "SELECT date, value FROM market_observations WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
                [series_id]).df()
        except Exception:  # noqa: BLE001 - a store without the table has no market series
            return pd.Series(dtype=float)
        return _as_series(df)

    def get(self, name: str) -> tuple[pd.Series, pd.Timestamp | None]:
        """(levels indexed by observation date, last FRED date when a provisional tail follows it, else None)."""
        if name in self._cache:
            return self._cache[name]
        spec = self.instruments.get(name)
        if spec is None:  # a named ticker or a sector ETF: market store, plain close
            out = (self._market(name), None)
        elif spec["store"] == "raw_observations":
            levels = self._fred(spec["id"])
            tail_after = None
            tail = spec.get("tail")
            if tail:
                levels, tail_after, _reason = _oil_levels_with_tail(
                    levels, self._market(tail["id"]), max_anchor_gap_days=self.cfg.oil_anchor_gap_days)
                if tail_after is not None and levels.index[-1] <= tail_after:
                    tail_after = None  # nothing past FRED's last observation
            out = (levels, tail_after)
        else:
            out = (self._market(spec["id"]), None)
        self._cache[name] = out
        return out

    def transform(self, name: str) -> tuple[str, float]:
        spec = self.instruments.get(name) or {"transform": "log_return_pct"}
        return spec["transform"], float(spec.get("bp_factor", 100))


def _as_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset="date", keep="last").set_index("date")["value"].astype(float).sort_index()


def _transformed(levels: pd.Series, transform: str, bp_factor: float) -> np.ndarray | None:
    values = levels.to_numpy(dtype=float)
    if transform == "change_bp":
        return values * bp_factor
    if np.any(values <= 0):
        return None  # a log change of a non-positive level is undefined
    return 100.0 * np.log(values)


# ----------------------------------------------------------------------------- one instrument


def reference_index(levels: pd.Series, onset: datetime, cfg: E2Config) -> int | None:
    """Index of the last observation whose close (16:00 New York on its date) is at or before `onset`."""
    if levels.empty:
        return None
    closes = (pd.DatetimeIndex(levels.index.normalize())
              + pd.Timedelta(hours=cfg.close_hour, minutes=cfg.close_minute)).tz_localize(cfg.tz)
    onset_ts = pd.Timestamp(onset)
    onset_ts = onset_ts.tz_localize("UTC") if onset_ts.tzinfo is None else onset_ts
    # Compare as timestamps, never as raw integers: pandas 3 keeps the store's resolution (us), so .asi8 and
    # Timestamp.value can be in different units.
    pos = int(closes.searchsorted(onset_ts.tz_convert(cfg.tz), side="right")) - 1
    return pos if pos >= 0 else None


def _robust_sd(x: np.ndarray, ref: int, k: int, cfg: E2Config) -> tuple[float | None, int, str | None]:
    hist = x[: ref + 1]
    if len(hist) <= k:
        return None, 0, "insufficient_baseline"
    moves = hist[k:] - hist[:-k]
    moves = moves[-cfg.baseline:]
    moves = moves[~np.isnan(moves)]
    if len(moves) < cfg.min_obs:
        return None, int(len(moves)), "insufficient_baseline"
    mad = float(np.median(np.abs(moves - np.median(moves))))
    sd = mad * cfg.mad_scale
    if sd == 0.0:
        return None, int(len(moves)), "mad_zero"
    return sd, int(len(moves)), None


def raw_check(src: SeriesSource, name: str, onset: datetime, as_of_day: date, cfg: E2Config) -> dict[str, Any]:
    """The sign-free part of a check: move, robust SD and z over the primary window, plus the info windows."""
    levels, tail_after = src.get(name)
    transform, bp = src.transform(name)
    base = {"instrument": name, "window_sessions": cfg.k, "transform": transform}
    if levels.empty:
        return {**base, "check_key": None, "status": UNAVAILABLE, "reason": "no_series"}
    ref = reference_index(levels, onset, cfg)
    if ref is None:
        return {**base, "check_key": None, "status": UNAVAILABLE, "reason": "no_reference_close"}
    ref_date = levels.index[ref].date()
    key = market_check_key(name, ref_date, cfg.k)
    out = {**base, "check_key": key, "reference_close_date": ref_date.isoformat()}
    # Only the baseline window and what follows P_ref are used, so only they are transformed: an old non-positive
    # level outside it (WTI settled at -37.63 on 2020-04-20) must not make a 2026 check undefined.
    start = max(0, ref - cfg.baseline - cfg.k)
    levels, ref = levels.iloc[start:], ref - start
    x = _transformed(levels, transform, bp)
    if x is None:
        return {**out, "status": UNAVAILABLE, "reason": "non_positive_level"}
    sd, n, why = _robust_sd(x, ref, cfg.k, cfg)
    if sd is None:
        return {**out, "status": UNAVAILABLE, "reason": why, "baseline_n": n}
    out.update({"robust_sd": sd, "baseline_n": n})
    end = ref + cfg.k
    if end >= len(x):
        newest = levels.index[-1].date()
        if weekday_lag(newest, as_of_day) > cfg.max_lag_weekdays:
            return {**out, "status": UNAVAILABLE, "reason": "stale_series", "newest_observation": newest.isoformat()}
        return {**out, "status": PENDING, "reason": "window_not_complete", "newest_observation": newest.isoformat()}
    move = float(x[end] - x[ref])
    latest = float(x[-1] - x[ref])
    info = []
    for w in cfg.info_windows:
        if ref + w < len(x):
            m = float(x[ref + w] - x[ref])
            info.append({"window_sessions": w, "move": m, "z": m / sd})
    provisional = bool(tail_after is not None and levels.index[end] > tail_after)
    return {**out, "status": "computed", "end_session_date": levels.index[end].date().isoformat(), "move": move,
            "z": move / sd, "latest_move": latest, "latest_date": levels.index[-1].date().isoformat(),
            "info_windows": info, "provisional": provisional}


def signed_result(raw: dict[str, Any], expected_sign: int, cfg: E2Config) -> dict[str, Any]:
    """The theme-specific reading of a raw check: aligned / opposed / no_signal, and response_current."""
    if raw["status"] != "computed":
        return {"result": raw["status"], "reason": raw.get("reason"), "response_current": False}
    s = raw["z"] * expected_sign
    result = ALIGNED if s >= cfg.z_threshold else OPPOSED if s <= -cfg.z_threshold else NO_SIGNAL
    current = bool(result == ALIGNED and raw["latest_move"] * expected_sign >= cfg.current_fraction * raw["move"] * expected_sign)
    return {"result": result, "reason": None, "response_current": current}


# ----------------------------------------------------------------------------- themes and companies


@dataclass
class MarketResponder:
    con: Any
    as_of_day: date
    cfg: E2Config = field(default_factory=load_e2_config)
    theme_map: dict[str, Any] = field(default_factory=load_theme_map)
    stock_sectors: dict[str, str] = field(default_factory=dict)   # ticker -> Yahoo sector string
    sector_etf: dict[str, str] = field(default_factory=dict)
    company_sign: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.src = SeriesSource(self.con, self.theme_map, self.cfg)
        self.checks: dict[str, dict[str, Any]] = {}     # check_key -> raw check (shared)
        self.shared: dict[str, set[str]] = {}            # check_key -> candidate ids
        if not self.sector_etf:
            self.sector_etf, self.company_sign = load_company_sectors()

    def _raw(self, name: str, onset: datetime, candidate_id: str) -> dict[str, Any]:
        raw = raw_check(self.src, name, onset, self.as_of_day, self.cfg)
        key = raw.get("check_key")
        if key is not None:
            raw = self.checks.setdefault(key, raw)
            self.shared.setdefault(key, set()).add(candidate_id)
        return raw

    def _family(self, slot: dict[str, Any], onset: datetime, cid: str) -> dict[str, Any]:
        raw = self._raw(slot["instrument"], onset, cid)
        res = signed_result(raw, int(slot["expected_sign"]), self.cfg)
        entry = {"family": slot["family"], "instrument": slot["instrument"], "expected_sign": int(slot["expected_sign"]),
                 "check_key": raw.get("check_key"), **res, "provisional": bool(raw.get("provisional", False))}
        extra = []
        for other in slot.get("same_family_instruments") or []:
            r = self._raw(other, onset, cid)
            extra.append({"instrument": other, "check_key": r.get("check_key"),
                          **signed_result(r, int(slot["expected_sign"]), self.cfg), "counted": False})
        if extra:
            entry["same_family_recorded"] = extra
        return entry

    def theme_result(self, theme_id: str, direction: str, onset: datetime, cid: str) -> dict[str, Any]:
        spec = self.theme_map["themes"].get(theme_id)
        if spec is None:
            return {"theme_id": theme_id, "direction": direction, "result": NOT_SCORED, "reason": "theme_not_in_map"}
        if spec["kind"] == DISPLAY_ONLY:
            return {"theme_id": theme_id, "direction": direction, "result": DISPLAY_ONLY, "reason": spec.get("reason")}
        if direction != "positive":
            return {"theme_id": theme_id, "direction": direction, "result": NOT_SCORED, "reason": f"direction_{direction}"}
        primary = self._family(spec["primary"], onset, cid)
        families = [primary]
        if spec.get("secondary"):
            families.append(self._family(spec["secondary"], onset, cid))
        out = {"theme_id": theme_id, "direction": direction, "families": families,
               "provisional": any(f["provisional"] for f in families)}
        if primary["result"] in (UNAVAILABLE, PENDING):
            return {**out, "result": primary["result"], "reason": f"primary_{primary['reason']}",
                    "response_current": False}
        counted = [f["result"] for f in families if f["result"] not in (UNAVAILABLE, PENDING)]
        n_al, n_op = counted.count(ALIGNED), counted.count(OPPOSED)
        if n_al and n_op:
            result = MIXED
        elif primary["result"] == ALIGNED or n_al >= 2:
            result = ALIGNED
        elif primary["result"] == OPPOSED:
            result = OPPOSED
        else:
            result = NO_SIGNAL
        return {**out, "result": result, "reason": None, "response_current": bool(primary["response_current"])}

    def company_check(self, ticker: str, themes: list[tuple[str, str]], onset: datetime, cid: str) -> dict[str, Any]:
        base = {"ticker": ticker, "recorded_as": "equity_reaction_only", "bond_data": "unavailable",
                "window_sessions": self.cfg.k}
        sector = self.stock_sectors.get(ticker)
        etf = self.sector_etf.get(sector or "")
        if etf is None:
            return {**base, "result": UNAVAILABLE, "reason": "no_sector_benchmark", "sector": sector}
        stock, _ = self.src.get(ticker)
        bench, _ = self.src.get(etf)
        if stock.empty:
            return {**base, "benchmark": etf, "result": UNAVAILABLE, "reason": "no_stock_series"}
        joined = pd.concat([stock.rename("s"), bench.rename("b")], axis=1, join="inner").dropna()
        if joined.empty or (joined <= 0).any().any():
            return {**base, "benchmark": etf, "result": UNAVAILABLE, "reason": "no_common_sessions"}
        abnormal = pd.Series(100.0 * (np.log(joined["s"]) - np.log(joined["b"])), index=joined.index)
        ref = reference_index(abnormal, onset, self.cfg)
        if ref is None:
            return {**base, "benchmark": etf, "result": UNAVAILABLE, "reason": "no_reference_close"}
        x = abnormal.to_numpy()
        out = {**base, "benchmark": etf, "reference_close_date": abnormal.index[ref].date().isoformat(),
               "check_key": market_check_key(f"{ticker}-{etf}", abnormal.index[ref].date(), self.cfg.k)}
        sd, n, why = _robust_sd(x, ref, self.cfg.k, self.cfg)
        if sd is None:
            return {**out, "result": UNAVAILABLE, "reason": why, "baseline_n": n}
        end = ref + self.cfg.k
        if end >= len(x):
            newest = abnormal.index[-1].date()
            status = UNAVAILABLE if weekday_lag(newest, self.as_of_day) > self.cfg.max_lag_weekdays else PENDING
            return {**out, "result": status, "reason": "stale_series" if status == UNAVAILABLE else "window_not_complete",
                    "baseline_n": n}
        move = float(x[end] - x[ref])
        z = move / sd
        signs = sorted({self.company_sign[t] for t, d in themes if d == "positive" and t in self.company_sign})
        out.update({"abnormal_move": move, "robust_sd": sd, "z": z, "baseline_n": n,
                    "end_session_date": abnormal.index[end].date().isoformat()})
        if len(signs) != 1:  # no signed theme, or conflicting ones: record the reaction without a call
            return {**out, "expected_sign": None, "result": RECORDED, "reason": None}
        s = z * signs[0]
        result = ALIGNED if s >= self.cfg.z_threshold else OPPOSED if s <= -self.cfg.z_threshold else NO_SIGNAL
        return {**out, "expected_sign": signs[0], "result": result, "reason": None}

    def respond(self, candidate: dict[str, Any]) -> dict[str, Any]:
        cid = candidate["candidate_id"]
        onset_iso = candidate.get("onset")
        themes = [(t["theme_id"], t["direction"]) for t in candidate.get("themes") or []]
        if onset_iso is None:
            return {"checks": [], "company_checks": [], "reason": "published_at_missing",
                    "bond_data": "unavailable"}
        onset = datetime.fromisoformat(onset_iso)
        if onset.tzinfo is None:
            onset = onset.replace(tzinfo=timezone.utc)
        checks = [self.theme_result(t, d, onset, cid) for t, d in sorted(set(themes))]
        tickers = sorted({tk for e in candidate.get("entities_resolved") or [] for tk in (e.get("tickers") or [])[:1]})
        companies = [self.company_check(tk, themes, onset, cid) for tk in tickers]
        return {"checks": checks, "company_checks": companies, "bond_data": "unavailable"}

    def shared_checks(self) -> dict[str, Any]:
        """Document-level table: one entry per check key, with the candidates that share it."""
        return {k: {**{kk: vv for kk, vv in v.items() if kk != "check_key"}, "shared_with": sorted(self.shared[k])}
                for k, v in sorted(self.checks.items())}


def read_stock_sectors(stocks_path: Path) -> dict[str, str]:
    rows = json.loads(stocks_path.read_text(encoding="utf-8"))
    return {str(r["symbol"]).upper(): str(r.get("sector") or "") for r in rows if r.get("symbol")}


def attach_market_response(con: Any, doc: dict[str, Any], as_of_day: date,
                           stock_sectors: dict[str, str] | None = None) -> dict[str, Any]:
    """Fill each candidate's `market_response` and add the shared `market_checks` table. Pure in (store, as-of day)."""
    responder = MarketResponder(con, as_of_day, stock_sectors=stock_sectors or {})
    out = dict(doc)
    cands = []
    for c in doc["candidates"]:
        c = dict(c)
        c["market_response"] = responder.respond(c)
        cands.append(c)
    out["candidates"] = cands
    out["market_checks"] = responder.shared_checks()
    out["market_response_method"] = {
        "plan_ref": "MRI_EVENTS_PLAN v2 §6, erratum v2.1, rulings 2026-10-22",
        "window_sessions": responder.cfg.k, "z_threshold": responder.cfg.z_threshold,
        "baseline": f"MAD x {responder.cfg.mad_scale} of overlapping moves, {responder.cfg.baseline} observations before P_ref",
        "meaning": "What the market did after the news. It does not say whether the event happened, and it changes no number.",
    }
    return out

