"""Direction study (WO_MD1 Part 1): does adding direction to the dimension scores improve the regime label?

Reads the stored monthly history from the DuckDB store (opened read-only) and recomputes regime probabilities
with the engine's own arithmetic (`build_regimes_from_dimensions`). It writes report.md and results.json into the
output folder. It changes nothing in the engine and never writes to the store.

Usage:
    python scripts/direction_study.py --store <path to macro_engine.duckdb> [--out outputs/experiments/direction_study]

USREC (the NBER recession indicator) is read from <out>/USREC.csv. It is not in the store, and the engine's FRED
client needs an API key kept in .env (which this study may not read), so the file is the public FRED CSV download.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from macro_engine.regimes.config import load_regime_config  # noqa: E402
from macro_engine.regimes.scoring import build_regimes_from_dimensions  # noqa: E402

MIN_HISTORY = 60  # months of history before a direction is standardised (fixed in the work order)
DOWN = ("recession", "stagflation")
TIGHT = ("tightening", "reflation", "stagflation")
KS = (1.0, 0.5, 2.0)  # k = 1 is the reading; 0.5 and 2 are sensitivity only
HORIZONS = {"6": 6, "3": 3}


# ----------------------------------------------------------------------------------------------- loading
def load_store(path: str):
    con = duckdb.connect(path, read_only=True)
    q = lambda sql: con.execute(sql).df()  # noqa: E731
    ds = q("select dimension_id, date, score, valid, reason from dimension_scores")
    ds["date"] = pd.to_datetime(ds["date"])
    rs = q("select regime_id, date, probability from regime_scores where valid")
    rs["date"] = pd.to_datetime(rs["date"])
    tl = q("select date, raw_dominant_regime from historical_regime_timeline")
    tl["date"] = pd.to_datetime(tl["date"])
    ro = q("select series_id, date, value from raw_observations where series_id in ('FEDFUNDS','CPIAUCSL','DTB3')")
    ro["date"] = pd.to_datetime(ro["date"])
    px = q("select date, close from sector_proxy_prices where ticker = 'SPY'")
    px["date"] = pd.to_datetime(px["date"])
    con.close()
    return ds, rs, tl, ro, px.set_index("date")["close"].sort_index()


def month_series(ro: pd.DataFrame, series_id: str, index: pd.DatetimeIndex) -> pd.Series:
    s = ro[ro.series_id == series_id].drop_duplicates("date", keep="last").set_index("date")["value"].sort_index()
    return s.reindex(index)


# ----------------------------------------------------------------------------------------------- variants
def direction_tables(S: pd.DataFrame, dims: list[str]):
    """For each horizon h: z_h(t) = d_h(t) / sd(d_h over months <= t); sd_s(t) = sd(score over months <= t)."""
    sd_s = S[dims].expanding(min_periods=MIN_HISTORY).std()
    out = {}
    for name, h in HORIZONS.items():
        d = S[dims] - S[dims].shift(h)
        sd_d = d.expanding(min_periods=MIN_HISTORY).std()
        out[name] = {"d": d, "sd_d": sd_d, "z": (d / sd_d).where(sd_d > 0)}
    return sd_s, out


def adjusted_frame(ds: pd.DataFrame, dims: list[str], adj: pd.DataFrame | None) -> pd.DataFrame:
    """Stored dimension rows, with the score of each regime dimension replaced by score + adj where adj exists."""
    frame = ds.copy()
    if adj is not None:
        long = adj.stack().rename("adj").reset_index()
        long.columns = ["date", "dimension_id", "adj"]
        frame = frame.merge(long, on=["date", "dimension_id"], how="left")
        use = frame["valid"] & frame["adj"].notna()
        frame.loc[use, "score"] = frame.loc[use, "score"] + frame.loc[use, "adj"]
        frame = frame.drop(columns="adj")
    return frame


def run_engine(frame, regimes, scoring):
    result = build_regimes_from_dimensions(frame, regimes, scoring)
    sc = result.regime_scores
    sc = sc[sc["valid"]].copy()
    sc["date"] = pd.to_datetime(sc["date"])
    P = sc.pivot(index="date", columns="regime_id", values="probability").sort_index()
    h = result.regime_health.copy()
    h["date"] = pd.to_datetime(h["date"])
    label = h[h["valid"]].set_index("date")["dominant_regime"].sort_index()
    return P, label


# ----------------------------------------------------------------------------------------------- readings
def r1_recessions(label: pd.Series, usrec: pd.Series, sample: pd.DatetimeIndex, mid: pd.Timestamp) -> dict:
    us = usrec.reindex(sample)
    runs, start = [], None
    full = usrec[usrec.index >= pd.Timestamp("1990-01-01")]
    prev = 0
    for d, v in full.items():
        if v == 1 and prev == 0:
            start = d
        if v == 0 and prev == 1:
            runs.append((start, d - pd.DateOffset(months=1)))
        prev = v
    if prev == 1:
        runs.append((start, full.index[-1]))
    lags, last_end = {}, None
    for s, e in runs:
        if s < sample[0]:
            lags[s.strftime("%Y-%m")] = "not evaluable (before the sample starts)"
            last_end = e
            continue
        lo = max(s - pd.DateOffset(months=12), sample[0], (last_end + pd.DateOffset(months=1)) if last_end is not None else sample[0])
        win = label.reindex(sample[(sample >= lo) & (sample <= e)])
        hit = win[win == "recession"]
        lags[s.strftime("%Y-%m")] = (
            int((hit.index[0].year - s.year) * 12 + hit.index[0].month - s.month) if len(hit) else None
        )
        last_end = e
    lab = label.reindex(sample)
    flagged = lab.isin(DOWN)
    rec = us == 1

    def share(mask):
        return float(flagged[mask].mean()) if mask.sum() else None

    return {
        "months_to_first_recession_label": lags,
        "recession_months_labelled_recession_or_stagflation": share(rec),
        "nonrecession_months_labelled_recession_or_stagflation": share(~rec),
        "n_recession_months": int(rec.sum()),
        "n_nonrecession_months": int((~rec).sum()),
        "halves": {
            "first": {"rec": share(rec & (sample < mid)), "nonrec": share(~rec & (sample < mid)),
                      "n_rec": int((rec & (sample < mid)).sum())},
            "second": {"rec": share(rec & (sample >= mid)), "nonrec": share(~rec & (sample >= mid)),
                       "n_rec": int((rec & (sample >= mid)).sum())},
        },
    }


def r2_turns(label: pd.Series, ff: pd.Series, cpi_yoy: pd.Series, sample: pd.DatetimeIndex, mid: pd.Timestamp) -> dict:
    ff_up = (ff - ff.shift(6)) >= 0.5
    cpi_up = (cpi_yoy - cpi_yoy.shift(6)) >= 0.5
    lab = label.reindex(sample)
    out = {}
    for name, cond in (("fedfunds_up_0.5_in_6m", ff_up), ("cpi_yoy_up_0.5_in_6m", cpi_up)):
        c = cond.reindex(sample).fillna(False)
        halves = {"all": c, "first": c & (sample < mid), "second": c & (sample >= mid)}
        cell = {}
        for hk, m in halves.items():
            n = int(m.sum())
            cell[hk] = {
                "n": n,
                "goldilocks": float((lab[m] == "goldilocks").mean()) if n else None,
                "tightening_reflation_stagflation": float(lab[m].isin(TIGHT).mean()) if n else None,
            }
        out[name] = cell
    return out


def build_r3_table(spy: pd.Series, tb3: pd.Series, months: pd.DatetimeIndex) -> pd.DataFrame:
    """Forward SPY outcomes per month (independent of any variant). Same logic as the 2026-11-12 study (m4.py, m9.py)."""
    idx = spy.index
    logret = np.log(spy).diff()
    tbd = tb3.reindex(idx).ffill()
    rows = []
    for d in months:
        i = idx.searchsorted(d)
        if i < 1 or i >= len(idx) or (idx[i] - d).days > 7:
            continue
        row = {"date": d, "ok3": False, "ok1": False}
        e3 = d + pd.DateOffset(months=3)
        if e3 <= idx[-1]:
            j = idx.searchsorted(e3)
            w = spy.iloc[i : j + 1]
            row.update(ok3=True, ret3=w.iloc[-1] / w.iloc[0] - 1,
                       vol3=logret.loc[w.index[1:]].std() * math.sqrt(252), mdd3=(w / w.cummax() - 1).min())
        e1 = d + pd.DateOffset(months=1)
        if e1 <= idx[-1] and i >= 200:
            j = idx.searchsorted(e1)
            row.update(ok1=True, r1=spy.iloc[j] / spy.iloc[i] - 1, cash1=tbd.loc[idx[i - 1]] / 100 / 12)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


def trailing_flag(down: pd.Series) -> pd.Series:
    """1 when the month's down probability is at or above the 2/3 quantile of all earlier months (>= 60 earlier months)."""
    out = {}
    for d in down.index:
        hist = down[down.index < d]
        out[d] = float(down.loc[d] >= hist.quantile(2 / 3)) if len(hist) >= MIN_HISTORY else 0.0
    return pd.Series(out)


def _on_off(R: pd.DataFrame, flag: pd.Series) -> dict:
    f = flag.reindex(R.index).astype(bool)
    a, b = R[f], R[~f]
    nz = lambda x: float(x) if pd.notna(x) else None  # noqa: E731
    return {
        "n_on": int(f.sum()), "n_off": int((~f).sum()),
        "ret_on": nz(a.ret3.mean()), "ret_off": nz(b.ret3.mean()),
        "vol_on": nz(a.vol3.mean()), "vol_off": nz(b.vol3.mean()),
        "vol_gap": nz(a.vol3.mean() - b.vol3.mean()),
        "mdd_on": nz(a.mdd3.mean()), "mdd_off": nz(b.mdd3.mean()),
    }


def _sharpe_dd(R1: pd.DataFrame, flag: pd.Series) -> dict:
    f = flag.reindex(R1.index).fillna(0.0)
    w = 1 - 0.5 * f
    x = w * R1.r1 + (1 - w) * R1.cash1 - R1.cash1
    cum = (1 + w * R1.r1 + (1 - w) * R1.cash1).cumprod()
    return {"n": int(len(x)), "sharpe": float(x.mean() / x.std() * math.sqrt(12)) if len(x) > 2 else None,
            "max_drawdown": float((cum / cum.cummax() - 1).min()) if len(x) else None}


def r3_dial(R: pd.DataFrame, down: pd.Series, sample: pd.DatetimeIndex, mid: pd.Timestamp) -> dict:
    flag = trailing_flag(down)
    ev = R[R.index.isin(sample)]
    r3 = ev[ev.ok3]
    r1 = ev[ev.ok1]
    out = {"all": _on_off(r3, flag), "first": _on_off(r3[r3.index < mid], flag), "second": _on_off(r3[r3.index >= mid], flag)}
    out["half_exposure_all"] = _sharpe_dd(r1, flag)
    out["always_in_all"] = _sharpe_dd(r1, flag * 0.0)
    out["half_exposure_first"] = _sharpe_dd(r1[r1.index < mid], flag)
    out["half_exposure_second"] = _sharpe_dd(r1[r1.index >= mid], flag)
    return out


def r4_noise(label: pd.Series, v0_label: pd.Series, sample: pd.DatetimeIndex) -> dict:
    lab = label.reindex(sample)
    changes = int((lab != lab.shift(1)).iloc[1:].sum())
    return {"changes_per_year": changes / (len(lab) / 12), "n_changes": changes,
            "share_differs_from_V0": float((lab != v0_label.reindex(sample)).mean())}


def r5_2026(P: pd.DataFrame, label: pd.Series) -> dict:
    out = {}
    for d in P.index[P.index >= pd.Timestamp("2026-01-01")]:
        out[d.strftime("%Y-%m")] = {"label": label.get(d), **{c: float(P.loc[d, c]) for c in P.columns}}
    return out


# ----------------------------------------------------------------------------------------------- decision rule
def decision(res: dict, name: str, v0: str = "V0") -> dict:
    a, b = res[name], res[v0]
    notes = {}
    # (a) recession timing and false alarms
    la, lb = a["R1"]["months_to_first_recession_label"], b["R1"]["months_to_first_recession_label"]
    both = [k for k in lb if isinstance(lb[k], int) and isinstance(la.get(k), int)]
    new_miss = [k for k in lb if isinstance(lb[k], int) and la.get(k) is None]
    avg_a = float(np.mean([la[k] for k in both])) if both else None
    avg_b = float(np.mean([lb[k] for k in both])) if both else None
    fa_a = a["R1"]["nonrecession_months_labelled_recession_or_stagflation"]
    fa_b = b["R1"]["nonrecession_months_labelled_recession_or_stagflation"]
    notes["a_timing_no_later"] = bool(both) and avg_a <= avg_b and not new_miss
    notes["a_false_alarm_within_5_points"] = fa_a <= fa_b + 0.05
    notes["a_detail"] = {"avg_lag_variant": avg_a, "avg_lag_V0": avg_b, "recessions_compared": both,
                         "recessions_newly_missed": new_miss, "false_alarm_variant": fa_a, "false_alarm_V0": fa_b}
    # (b) goldilocks share lower in both R2 readings in both halves
    cells = {}
    ok_b = True
    for rd in ("fedfunds_up_0.5_in_6m", "cpi_yoy_up_0.5_in_6m"):
        for hk in ("first", "second"):
            ga, gb = a["R2"][rd][hk]["goldilocks"], b["R2"][rd][hk]["goldilocks"]
            lower = ga is not None and gb is not None and ga < gb
            cells[f"{rd}/{hk}"] = {"variant": ga, "V0": gb, "lower": lower}
            ok_b = ok_b and lower
    notes["b_goldilocks_lower_everywhere"] = ok_b
    notes["b_detail"] = cells
    # (c) volatility split (vol on minus vol off) not smaller than V0's in either half
    cc, ok_c = {}, True
    for hk in ("first", "second"):
        ga, gb = a["R3"][hk]["vol_gap"], b["R3"][hk]["vol_gap"]
        fine = ga is not None and gb is not None and ga >= gb
        cc[hk] = {"variant": ga, "V0": gb, "not_worse": fine}
        ok_c = ok_c and fine
    notes["c_vol_split_not_worse"] = ok_c
    notes["c_detail"] = cc
    notes["passes"] = bool(notes["a_timing_no_later"] and notes["a_false_alarm_within_5_points"] and ok_b and ok_c)
    return notes


# ----------------------------------------------------------------------------------------------- report
def pct(x, d=1):
    return "n/a" if x is None else f"{100 * x:.{d}f}%"


def num(x, d=2):
    return "n/a" if x is None else f"{x:.{d}f}"


def write_report(path: Path, res: dict, meta: dict, rules: dict, names: list[str]) -> None:
    L = []
    w = L.append
    w("# Direction study (WO_MD1 Part 1)\n")
    w("Generated by `scripts/direction_study.py` from the stored monthly history. Revised data, not point-in-time: it flatters every "
      "variant and the level reading alike.\n")
    w(f"Evaluation sample: {meta['sample_first']} to {meta['sample_last']} ({meta['n_sample']} months), the months where every one of "
      f"the five regime dimensions has a standardised 3- and 6-month direction. Halves split at {meta['mid']} "
      f"(first half is before it).\n")
    w("## V0 reproduction\n")
    w(f"V0 recomputed through the engine's own function reproduces the stored regime probabilities: max abs difference "
      f"{meta['v0_max_abs_diff']:.2e} over {meta['v0_cells']} cells; dominant label equals the stored raw label in "
      f"{meta['v0_label_match']}.\n")
    w("## REPORT_4 risk-dial reproduction (V0, stored probabilities, full sample, not the evaluation sample)\n")
    for line in meta["repro_lines"]:
        w(f"- {line}")
    w("")
    w("## R1 Recession timing (months from recession start to first `recession` label; negative = early)\n")
    recs = sorted({k for n in names for k in res[n]["R1"]["months_to_first_recession_label"]})
    w("| Variant | " + " | ".join(recs) + " | Rec. months flagged | Non-rec. months flagged (false alarm) | Flagged first half rec / non-rec | Flagged second half rec / non-rec |")
    w("|---|" + "---|" * (len(recs) + 4))
    for n in names:
        r = res[n]["R1"]
        cells = [str(r["months_to_first_recession_label"][k]) if r["months_to_first_recession_label"][k] is not None else "none" for k in recs]
        h = r["halves"]
        w(f"| {n} | " + " | ".join(cells) + f" | {pct(r['recession_months_labelled_recession_or_stagflation'])} | "
          f"{pct(r['nonrecession_months_labelled_recession_or_stagflation'])} | {pct(h['first']['rec'])} / {pct(h['first']['nonrec'])} | "
          f"{pct(h['second']['rec'])} / {pct(h['second']['nonrec'])} |")
    w("")
    w("## R2 Tightening and inflation turns (a consistency reading: both definitions use the engine's own inputs, not independent truth)\n")
    w("| Variant | FF up: n (all/1st/2nd) | FF up: goldilocks all / 1st / 2nd | FF up: tightening+reflation+stagflation all | CPI up: n | CPI up: goldilocks all / 1st / 2nd |")
    w("|---|---|---|---|---|---|")
    for n in names:
        f = res[n]["R2"]["fedfunds_up_0.5_in_6m"]
        c = res[n]["R2"]["cpi_yoy_up_0.5_in_6m"]
        w(f"| {n} | {f['all']['n']}/{f['first']['n']}/{f['second']['n']} | {pct(f['all']['goldilocks'])} / {pct(f['first']['goldilocks'])} / {pct(f['second']['goldilocks'])} | "
          f"{pct(f['all']['tightening_reflation_stagflation'])} | {c['all']['n']}/{c['first']['n']}/{c['second']['n']} | "
          f"{pct(c['all']['goldilocks'])} / {pct(c['first']['goldilocks'])} / {pct(c['second']['goldilocks'])} |")
    w("")
    w("## R3 Risk dial (recession + stagflation probability in its trailing top third at month start; SPY next 3 months)\n")
    w("| Variant | Sample | Months on/off | Return on / off | Vol on / off | Vol gap (on - off) | Worst drawdown on / off |")
    w("|---|---|---|---|---|---|---|")
    for n in names:
        for hk in ("all", "first", "second"):
            d = res[n]["R3"][hk]
            w(f"| {n} | {hk} | {d['n_on']}/{d['n_off']} | {pct(d['ret_on'])} / {pct(d['ret_off'])} | {pct(d['vol_on'])} / {pct(d['vol_off'])} | "
              f"{pct(d['vol_gap'])} | {pct(d['mdd_on'])} / {pct(d['mdd_off'])} |")
    w("")
    w("Half-exposure rule (1-month steps, excess over bills):\n")
    w("| Variant | Sharpe all | Sharpe first / second half | Max drawdown all | Months |")
    w("|---|---|---|---|---|")
    base = res[names[0]]["R3"]["always_in_all"]
    w(f"| always in | {num(base['sharpe'])} | | {pct(base['max_drawdown'], 0)} | {base['n']} |")
    for n in names:
        d = res[n]["R3"]
        w(f"| {n} | {num(d['half_exposure_all']['sharpe'])} | {num(d['half_exposure_first']['sharpe'])} / {num(d['half_exposure_second']['sharpe'])} | "
          f"{pct(d['half_exposure_all']['max_drawdown'], 0)} | {d['half_exposure_all']['n']} |")
    w("")
    w("## R4 Label noise\n")
    w("| Variant | Label changes per year | Share of months differing from V0 |")
    w("|---|---|---|")
    for n in names:
        d = res[n]["R4"]
        w(f"| {n} | {num(d['changes_per_year'])} | {pct(d['share_differs_from_V0'])} |")
    w("")
    w("## R5 The case of 2026 (label, then probabilities: goldilocks / reflation / stagflation / recession / tightening, in percent)\n")
    months = sorted(res[names[0]]["R5"])
    w("| Month | " + " | ".join(names) + " |")
    w("|---|" + "---|" * len(names))
    for m in months:
        cells = []
        for n in names:
            x = res[n]["R5"].get(m)
            cells.append("n/a" if x is None else f"{x['label']} " + "/".join(f"{100 * x[c]:.0f}" for c in ("goldilocks", "reflation", "stagflation", "recession", "tightening")))
        w(f"| {m} | " + " | ".join(cells) + " |")
    w("")
    w("## Decision rule (registered in the work order, applied at k = 1; k = 0.5 and 2 shown for sensitivity only)\n")
    w("Direction feeds the label only if a variant (a) labels recessions no later than V0 on average with false-alarm months no more than "
      "V0 + 5 points, AND (b) lowers the goldilocks share in both R2 readings in both halves, AND (c) does not worsen the R3 volatility "
      "split in either half.\n")
    w("| Variant | (a) timing | (a) false alarms | (b) goldilocks lower | (c) vol split | Passes |")
    w("|---|---|---|---|---|---|")
    for n, r in rules.items():
        w(f"| {n} | {r['a_timing_no_later']} | {r['a_false_alarm_within_5_points']} | {r['b_goldilocks_lower_everywhere']} | {r['c_vol_split_not_worse']} | {r['passes']} |")
    w("")
    w("Detail of each test:\n")
    for n, r in rules.items():
        w(f"- {n}: (a) {json.dumps(r['a_detail'])}")
        w(f"  (b) {json.dumps(r['b_detail'])}")
        w(f"  (c) {json.dumps(r['c_detail'])}")
    w("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="path to macro_engine.duckdb (opened read-only)")
    ap.add_argument("--config", default=str(ROOT / "config" / "phase_b_sources.yaml"))
    ap.add_argument("--out", default=str(ROOT / "outputs" / "experiments" / "direction_study"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cfg = load_regime_config(args.config)
    regimes, scoring = cfg.regimes, cfg.scoring
    dims = sorted({d.dimension_id for r in regimes if r.enabled for d in r.dimensions})
    ds, rs, tl, ro, spy = load_store(args.store)
    usrec_raw = pd.read_csv(out / "USREC.csv", parse_dates=["observation_date"]).set_index("observation_date")["USREC"]

    S = ds[ds["valid"] & ds["score"].notna()].pivot(index="date", columns="dimension_id", values="score").sort_index()
    S = S.reindex(pd.date_range(S.index.min(), S.index.max(), freq="MS"))
    sd_s, dirs = direction_tables(S, dims)

    # ---- V0: must reproduce the stored probabilities
    P0, lab0 = run_engine(adjusted_frame(ds, dims, None), regimes, scoring)
    stored = rs.pivot(index="date", columns="regime_id", values="probability").sort_index()
    cols = list(stored.columns)
    diff = (P0.reindex(stored.index)[cols] - stored[cols]).abs()
    max_diff = float(diff.max().max())
    if not np.isfinite(max_diff) or max_diff > 1e-9:
        print(f"STOP: V0 does not reproduce the stored probabilities (max abs diff {max_diff}).", file=sys.stderr)
        return 2
    raw_stored = tl.set_index("date")["raw_dominant_regime"].sort_index()
    match = f"{int((lab0.reindex(raw_stored.index) == raw_stored).sum())} of {len(raw_stored)} months"

    # ---- evaluation sample: months where every regime dimension has a usable standardised direction at both horizons
    usable = pd.Series(True, index=S.index)
    for name in HORIZONS:
        usable &= dirs[name]["sd_d"].notna().all(axis=1)
    usable &= sd_s.notna().all(axis=1)
    sample = S.index[usable]
    sample = sample.intersection(P0.index)
    mid = sample[len(sample) // 2]

    variants = {"V0": (P0, lab0)}
    for vname, hz in (("V6", "6"), ("V3", "3")):
        for k in KS:
            adj = dirs[hz]["z"].fillna(0.0) * sd_s.fillna(0.0) * k
            P, lab = run_engine(adjusted_frame(ds, dims, adj), regimes, scoring)
            variants[f"{vname}_k{k:g}"] = (P, lab)
    names = list(variants)

    # ---- fixed inputs for the readings
    ff = month_series(ro, "FEDFUNDS", S.index)
    cpi = month_series(ro, "CPIAUCSL", pd.date_range(S.index.min() - pd.DateOffset(months=12), S.index.max(), freq="MS"))
    cpi_yoy = (100 * (cpi / cpi.shift(12) - 1)).reindex(S.index)
    tb3 = ro[ro.series_id == "DTB3"].drop_duplicates("date", keep="last").set_index("date")["value"].sort_index()
    R = build_r3_table(spy, tb3, P0.index)
    usrec = usrec_raw.reindex(pd.date_range(usrec_raw.index.min(), usrec_raw.index.max(), freq="MS")).fillna(0).astype(int)

    # ---- reproduction of REPORT_4's risk-dial row (V0, stored probabilities, full price sample, full-sample top third)
    down0 = stored[list(DOWN)].sum(axis=1)
    R3full = R[R.ok3].copy()
    f_full = (down0.reindex(R3full.index) >= down0.reindex(R3full.index).quantile(2 / 3))
    on_off = _on_off(R3full, f_full)
    R1full = R[R.ok1]
    trail_all = trailing_flag(down0)
    sd_none = _sharpe_dd(R1full, trail_all * 0.0)
    sd_half = _sharpe_dd(R1full, trail_all)
    repro = [
        f"top-third (full-sample quantile), SPY next 3 months, {on_off['n_on'] + on_off['n_off']} months, {on_off['n_on']} on "
        f"(REPORT_4: 330 months, 110 on): return {pct(on_off['ret_on'])} / {pct(on_off['ret_off'])} (REPORT_4: -0.1% / +3.0%), "
        f"vol {pct(on_off['vol_on'])} / {pct(on_off['vol_off'])} (22.2% / 14.3%), worst drawdown {pct(on_off['mdd_on'])} / {pct(on_off['mdd_off'])} (-11.1% / -6.2%)",
        f"half-exposure rule on the trailing top third, {sd_half['n']} months (REPORT_4: 322): Sharpe {num(sd_half['sharpe'])} (0.51), "
        f"max drawdown {pct(sd_half['max_drawdown'], 0)} (-32%); always in: Sharpe {num(sd_none['sharpe'])} (0.35), max drawdown {pct(sd_none['max_drawdown'], 0)} (-54%)",
    ]

    res = {}
    for n, (P, lab) in variants.items():
        down = P[list(DOWN)].sum(axis=1)
        res[n] = {
            "R1": r1_recessions(lab, usrec, sample, mid),
            "R2": r2_turns(lab, ff, cpi_yoy, sample, mid),
            "R3": r3_dial(R, down, sample, mid),
            "R4": r4_noise(lab, lab0, sample),
            "R5": r5_2026(P, lab),
        }
    # V0 on the full sample as a reference for the one recession that falls before the evaluation sample
    full_sample = P0.index
    ref = {"R1_full_sample": r1_recessions(lab0, usrec, full_sample, mid)}

    rules = {n: decision(res, n) for n in names if n != "V0"}
    meta = {"sample_first": sample[0].strftime("%Y-%m"), "sample_last": sample[-1].strftime("%Y-%m"), "n_sample": int(len(sample)),
            "mid": mid.strftime("%Y-%m"), "v0_max_abs_diff": max_diff, "v0_cells": int(diff.notna().sum().sum()),
            "v0_label_match": match, "repro_lines": repro}
    write_report(out / "report.md", res, meta, rules, names)

    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, (np.floating, float)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        return o

    (out / "results.json").write_text(json.dumps(clean({"meta": meta, "results": res, "decision": rules, "reference": ref}), indent=2), encoding="utf-8")
    print(f"wrote {out / 'report.md'}")
    for line in repro:
        print(line)
    for n, r in rules.items():
        print(n, "passes" if r["passes"] else "fails", {k: v for k, v in r.items() if k.endswith(("timing_no_later", "within_5_points", "everywhere", "not_worse"))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
