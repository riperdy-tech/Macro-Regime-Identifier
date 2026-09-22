#!/usr/bin/env python3
"""
S0.5 — a reproducible baseline measurement pack.

Reads the DuckDB store (read-only, never written by this script) and writes a JSON + Markdown
pack to `outputs/baseline/S0/`. Every number in the pack is labelled with the table/column basis
it was computed from, so the pack can be re-derived and cross-checked without re-reading this
file.

**Reproducibility contract.** Running this script twice in a row against an unchanged database
must produce a byte-identical `baseline.json`:

  * no timestamps inside `baseline.json` — the run timestamp lives only in the sidecar
    `baseline_meta.json`, which is NOT part of the comparison;
  * `json.dumps(..., sort_keys=True, allow_nan=False)` with every float rounded to a fixed
    number of digits before serialization (`_ROUND_NDIGITS` below);
  * every aggregation sorts its input before grouping/reducing, so floating-point summation
    order cannot drift between runs.

**How to check byte-identity**: run the script twice into two different `--out-dir` values (or
copy `baseline.json` aside between runs) and `diff` the two `baseline.json` files directly —
they must be empty-diff. Do not diff `baseline_meta.json`; it carries the run's wall-clock
timestamp by design and will always differ.

Usage:
    python scripts/measure_baseline.py
    python scripts/measure_baseline.py --db-path data/macro_engine.duckdb --out-dir outputs/baseline/S0
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from macro_engine.anchors.config import load_anchor_config  # noqa: E402
from macro_engine.anchors.multiples import build_regime_state_frame  # noqa: E402

DEFAULT_DB_PATH = "data/macro_engine.duckdb"
DEFAULT_OUT_DIR = "outputs/baseline/S0"
DEFAULT_NBER_CONFIG = "config/nber_recessions.yaml"
DEFAULT_PHASE_B_CONFIG = "config/phase_b_sources.yaml"
DEFAULT_ANCHORS_CONFIG = "config/anchors.yaml"
DEFAULT_SECTORS_CONFIG = "config/sectors.yaml"

# Named in the S0.2 brief: sub-industries carved from a parent GICS sector. Near-collinear
# with their parent, so the "17-row" sector cross-section double-counts them and the
# "11-row" cross-section (parents only) is the de-duplicated, honest one.
SUB_INDUSTRY_SECTOR_IDS = frozenset(
    {"semiconductors", "software", "banks", "biotech", "oil_gas_ep", "homebuilders"}
)

MAX_PRICE_LOOKAHEAD_DAYS = 7
_ROUND_NDIGITS = 6


# ── entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    build_baseline_pack(db_path=args.db_path, out_dir=args.out_dir)


def build_baseline_pack(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    nber_config_path: str | Path = DEFAULT_NBER_CONFIG,
    phase_b_config_path: str | Path = DEFAULT_PHASE_B_CONFIG,
    anchors_config_path: str | Path = DEFAULT_ANCHORS_CONFIG,
    sectors_config_path: str | Path = DEFAULT_SECTORS_CONFIG,
) -> dict[str, Any]:
    tables = _read_tables(db_path)
    payload = _build_payload(
        tables,
        nber_config_path=nber_config_path,
        phase_b_config_path=phase_b_config_path,
        anchors_config_path=anchors_config_path,
        sectors_config_path=sectors_config_path,
    )
    _write_pack(payload, out_dir=out_dir, db_path=db_path)
    return payload


# ── storage (read-only) ──────────────────────────────────────────────────────


_TABLE_NAMES = [
    "historical_regime_timeline",
    "regime_scores",
    "regime_dimension_contributions",
    "dimension_scores",
    "raw_observations",
    "sector_scores",
    "sector_proxy_prices",
    "sector_validation_returns",
]


def _read_tables(db_path: str | Path) -> dict[str, pd.DataFrame]:
    # Hard safety rule: this database is read-only for measurement. Opened directly with
    # read_only=True rather than through DuckDBStore, because DuckDBStore.initialize() issues
    # CREATE/ALTER statements that would require a writable connection.
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {name: con.execute(f"SELECT * FROM {name}").fetchdf() for name in _TABLE_NAMES}
    finally:
        con.close()


# ── payload assembly ─────────────────────────────────────────────────────────


def _build_payload(
    tables: dict[str, pd.DataFrame],
    *,
    nber_config_path: str | Path,
    phase_b_config_path: str | Path,
    anchors_config_path: str | Path,
    sectors_config_path: str | Path,
) -> dict[str, Any]:
    timeline = _prepare_timeline(tables["historical_regime_timeline"])
    nber_recessions = _load_nber_recessions(nber_config_path)
    min_confidence_to_switch = _load_min_confidence_to_switch(phase_b_config_path)

    total_months = int(len(timeline))

    payload: dict[str, Any] = {
        "comparison_instructions": (
            "baseline.json must be byte-identical across consecutive runs against an unchanged "
            "database; diff it directly. baseline_meta.json (run timestamp) is a sidecar, "
            "excluded from that comparison by design."
        ),
        "universe": {
            "total_months": total_months,
            "start_date": _date_str(timeline["date"].min()),
            "end_date": _date_str(timeline["date"].max()),
            "basis": "historical_regime_timeline, one row per evaluation month, valid and invalid rows both counted",
        },
        "recession_triple": _measure_recession_triple(timeline, nber_recessions, total_months),
        "base_rates": _measure_base_rates(timeline, nber_recessions, total_months),
        "stability": _measure_stability(timeline),
        "confidence_distribution": _measure_confidence_distribution(
            timeline, total_months, min_confidence_to_switch
        ),
        "regime_score_summary": _measure_regime_score_summary(tables["regime_scores"]),
        "regime_dimension_contributions": _measure_regime_dimension_contributions(
            tables["regime_dimension_contributions"]
        ),
        "dimension_diagnostics": _measure_dimension_diagnostics(tables["dimension_scores"]),
        "coverage": _measure_coverage(tables["dimension_scores"], tables["regime_scores"]),
        "forward_returns": _measure_forward_returns(timeline, tables["sector_proxy_prices"]),
        "sector_rank_ic": _measure_sector_rank_ic(tables["sector_validation_returns"]),
        "regime_state_buckets": _measure_regime_state_buckets(
            tables["dimension_scores"], tables["raw_observations"], anchors_config_path
        ),
        "screener_quota_table": _measure_screener_quota_table(
            tables["sector_scores"], sectors_config_path
        ),
        "latest_confidence": _measure_latest_confidence(timeline),
    }
    return _clean(payload)


# ── item 1: recession triple ────────────────────────────────────────────────


def _prepare_timeline(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").reset_index(drop=True)
    frame["month"] = frame["date"].dt.to_period("M")
    return frame


def _load_nber_recessions(path: str | Path) -> list[tuple[str, str]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return [(item["start"], item["end"]) for item in data.get("nber_recessions", [])]


def _nber_month_set(recessions: list[tuple[str, str]]) -> set[pd.Period]:
    months: set[pd.Period] = set()
    for start, end in recessions:
        months.update(pd.period_range(start, end, freq="M").tolist())
    return months


def _load_min_confidence_to_switch(path: str | Path) -> float:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return float(
        data["historical_diagnostic"]["transition_filter"]["min_confidence_to_switch"]
    )


def _measure_recession_triple(
    timeline: pd.DataFrame, recessions: list[tuple[str, str]], total_months: int
) -> dict[str, Any]:
    nber_months = _nber_month_set(recessions)
    in_nber = timeline["month"].isin(nber_months)
    result: dict[str, Any] = {
        "basis": (
            "historical_regime_timeline.{reported_regime,raw_dominant_regime} == 'recession', "
            "against NBER peak-through-trough months from config/nber_recessions.yaml"
        ),
        "nber_recessions": [{"start": s, "end": e} for s, e in recessions],
    }
    for column, key in (("reported_regime", "reported"), ("raw_dominant_regime", "raw_dominant")):
        labeled = timeline[column] == "recession"
        tp = int((labeled & in_nber).sum())
        fp = int((labeled & ~in_nber).sum())
        fn = int((~labeled & in_nber).sum())
        share = int(labeled.sum())
        result[key] = {
            "label_month_count": share,
            "label_share_of_all_months": _ratio(share, total_months),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
        }
    return result


# ── item 2: base rates ───────────────────────────────────────────────────────


def _measure_base_rates(
    timeline: pd.DataFrame, recessions: list[tuple[str, str]], total_months: int
) -> dict[str, Any]:
    nber_months = _nber_month_set(recessions)
    in_nber = timeline["month"].isin(nber_months)

    def counts_by_regime(column: str) -> dict[str, Any]:
        counts = timeline[column].value_counts(dropna=True)
        return {
            str(regime_id): {
                "count": int(count),
                "share_of_all_months": _ratio(int(count), total_months),
            }
            for regime_id, count in sorted(counts.items())
        }

    return {
        "basis": "historical_regime_timeline.{reported_regime,raw_dominant_regime}, share denominator is total_months",
        "reported_regime": counts_by_regime("reported_regime"),
        "raw_dominant_regime": counts_by_regime("raw_dominant_regime"),
        "nber_truth": {
            "count": int(in_nber.sum()),
            "share_of_all_months": _ratio(int(in_nber.sum()), total_months),
        },
    }


# ── item 3: stability ────────────────────────────────────────────────────────


def _switches_and_spells(labels: pd.Series) -> dict[str, Any]:
    # Matches the hand-measured basis exactly: comparison is `!=` against the prior row with
    # no NaN-dropping, so a run of invalid/unlabeled months counts as switches too (pandas'
    # NaN != NaN is True). This is not smoothed away because the invalid months are a real
    # gap in the reported timeline, not a data artifact to paper over.
    prev = labels.shift(1)
    switch = labels != prev
    switch.iloc[0] = False
    switch_count = int(switch.sum())
    spell_group = switch.cumsum()
    spell_lengths = spell_group.value_counts().sort_index().to_numpy(dtype=float)
    return {
        "switch_count": switch_count,
        "spell_length_months": _distribution(spell_lengths, include_n=True),
    }


def _measure_stability(timeline: pd.DataFrame) -> dict[str, Any]:
    return {
        "basis": (
            "historical_regime_timeline.{reported_regime,raw_dominant_regime}, month over month; "
            "a switch is any change from the prior row, including into/out of an invalid month"
        ),
        "reported_regime": _switches_and_spells(timeline["reported_regime"]),
        "raw_dominant_regime": _switches_and_spells(timeline["raw_dominant_regime"]),
    }


# ── item 4: confidence distribution ─────────────────────────────────────────


def _measure_confidence_distribution(
    timeline: pd.DataFrame, total_months: int, min_confidence_to_switch: float
) -> dict[str, Any]:
    confidence = pd.to_numeric(timeline["confidence"], errors="coerce")
    valid = confidence.dropna()
    at_or_above = int((confidence >= min_confidence_to_switch).sum())
    return {
        "basis": "historical_regime_timeline.confidence; share denominator is total_months (invalid months count as below threshold)",
        "min_confidence_to_switch": min_confidence_to_switch,
        "min_confidence_to_switch_source": "config/phase_b_sources.yaml: historical_diagnostic.transition_filter.min_confidence_to_switch",
        "n_valid": int(len(valid)),
        "mean": _r(valid.mean()),
        "p25": _r(valid.quantile(0.25)),
        "median": _r(valid.quantile(0.5)),
        "p75": _r(valid.quantile(0.75)),
        "max": _r(valid.max()),
        "months_at_or_above_threshold": at_or_above,
        "share_at_or_above_threshold": _ratio(at_or_above, total_months),
    }


# ── item 5: regime score summary ────────────────────────────────────────────


def _measure_regime_score_summary(regime_scores: pd.DataFrame) -> dict[str, Any]:
    frame = regime_scores[regime_scores["valid"]].copy()
    frame["raw_score"] = pd.to_numeric(frame["raw_score"], errors="coerce")
    frame["probability"] = pd.to_numeric(frame["probability"], errors="coerce")
    result: dict[str, Any] = {
        "basis": "regime_scores WHERE valid, grouped by regime_id",
    }
    for regime_id, group in frame.groupby("regime_id"):
        scores = group["raw_score"].dropna()
        result[str(regime_id)] = {
            "n": int(len(group)),
            "mean": _r(scores.mean()),
            "sd": _r(scores.std()),
            "min": _r(scores.min()),
            "max": _r(scores.max()),
            "mean_probability": _r(group["probability"].dropna().mean()),
        }
    return dict(sorted(result.items()))


def _measure_regime_dimension_contributions(contributions: pd.DataFrame) -> dict[str, Any]:
    frame = contributions[contributions["valid"]].copy()
    frame["transformed_dimension_value"] = pd.to_numeric(
        frame["transformed_dimension_value"], errors="coerce"
    )
    frame["contribution"] = pd.to_numeric(frame["contribution"], errors="coerce")
    result: dict[str, Any] = {
        "basis": (
            "regime_dimension_contributions WHERE valid, grouped by (regime_id, dimension_id): "
            "the mean transformed value and mean contribution attribute the polarity-transform "
            "handicap in the regime_score_summary raw_score means"
        ),
    }
    for (regime_id, dimension_id), group in frame.groupby(["regime_id", "dimension_id"]):
        result.setdefault(str(regime_id), {})[str(dimension_id)] = {
            "n": int(len(group)),
            "mean_transformed_dimension_value": _r(group["transformed_dimension_value"].mean()),
            "mean_contribution": _r(group["contribution"].mean()),
        }
    for regime_id in [key for key in result if key != "basis"]:
        result[regime_id] = dict(sorted(result[regime_id].items()))
    return {
        key: (value if key == "basis" else dict(sorted(value.items())))
        for key, value in sorted(result.items())
    }


# ── item 6: dimension diagnostics ───────────────────────────────────────────


def _measure_dimension_diagnostics(dimension_scores: pd.DataFrame) -> dict[str, Any]:
    frame = dimension_scores[dimension_scores["valid"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")

    per_dimension: dict[str, Any] = {}
    for dimension_id, group in frame.groupby("dimension_id"):
        series = group.sort_values("date")["score"].dropna()
        per_dimension[str(dimension_id)] = {
            "n": int(len(series)),
            "mean": _r(series.mean()),
            "sd": _r(series.std()),
            "lag1_autocorrelation": _r(series.autocorr(lag=1)) if len(series) > 2 else None,
        }

    pivot = frame.pivot_table(index="date", columns="dimension_id", values="score", aggfunc="last")
    pivot = pivot[sorted(pivot.columns)]
    correlation = pivot.corr(method="pearson")
    correlation_matrix = {
        str(row_dim): {str(col_dim): _r(correlation.loc[row_dim, col_dim]) for col_dim in correlation.columns}
        for row_dim in correlation.index
    }

    return {
        "basis": (
            "dimension_scores WHERE valid, per dimension_id; correlation matrix from the "
            "date x dimension_id pivot of dimension_scores.score (pairwise-complete, pandas default)"
        ),
        "per_dimension": dict(sorted(per_dimension.items())),
        "correlation_matrix": dict(sorted(correlation_matrix.items())),
    }


# ── item 7: coverage distribution ───────────────────────────────────────────


def _measure_coverage(dimension_scores: pd.DataFrame, regime_scores: pd.DataFrame) -> dict[str, Any]:
    dim = dimension_scores[dimension_scores["valid"]].copy()
    dim["coverage_ratio"] = pd.to_numeric(dim["coverage_ratio"], errors="coerce")
    by_dimension = {}
    for dimension_id, group in dim.groupby("dimension_id"):
        by_dimension[str(dimension_id)] = _distribution(group["coverage_ratio"].dropna().to_numpy())

    reg = regime_scores[regime_scores["valid"]].copy()
    reg["coverage_ratio"] = pd.to_numeric(reg["coverage_ratio"], errors="coerce")
    by_regime = {}
    for regime_id, group in reg.groupby("regime_id"):
        by_regime[str(regime_id)] = _distribution(group["coverage_ratio"].dropna().to_numpy())

    # regime_health never stores a literal "coverage_ratio" column. Internally
    # (regimes/scoring.py `_build_regime_health`) it computes, per date, the mean of
    # regime_scores.coverage_ratio across that date's valid regimes, and folds that value into
    # `confidence` without persisting it separately. This recomputes that per-date average, i.e.
    # the coverage number regime_health actually consumes but does not expose as its own column.
    per_date_average = (
        reg.dropna(subset=["coverage_ratio"]).groupby("date")["coverage_ratio"].mean().to_numpy()
    )

    return {
        "basis": "dimension_scores.coverage_ratio and regime_scores.coverage_ratio WHERE valid; both columns are literally named coverage_ratio",
        "dimension_scores_coverage_ratio": {
            "overall": _distribution(dim["coverage_ratio"].dropna().to_numpy()),
            "by_dimension": dict(sorted(by_dimension.items())),
        },
        "regime_scores_coverage_ratio": {
            "overall": _distribution(reg["coverage_ratio"].dropna().to_numpy()),
            "by_regime": dict(sorted(by_regime.items())),
        },
        "regime_health_implied_coverage": {
            "basis": (
                "regime_health has no coverage_ratio column; this is the per-date mean of "
                "regime_scores.coverage_ratio across that date's valid regimes -- the value "
                "regimes/scoring.py `_build_regime_health` computes as `average_coverage` and "
                "multiplies into `confidence`, without storing it separately"
            ),
            "distribution": _distribution(per_date_average),
        },
    }


# ── item 8: forward returns ─────────────────────────────────────────────────


def _price_on_or_after(prices: pd.DataFrame, date: pd.Timestamp) -> float | None:
    index = prices["date"].searchsorted(date, side="left")
    if index >= len(prices):
        return None
    row = prices.iloc[int(index)]
    if (row["date"] - date).days > MAX_PRICE_LOOKAHEAD_DAYS:
        return None
    return float(row["close"])


def _forward_return(prices: pd.DataFrame, date: pd.Timestamp, months: int) -> float | None:
    start = _price_on_or_after(prices, date)
    end = _price_on_or_after(prices, date + pd.DateOffset(months=months))
    if start is None or end is None or start <= 0:
        return None
    return end / start - 1.0


def _measure_forward_returns(timeline: pd.DataFrame, sector_proxy_prices: pd.DataFrame) -> dict[str, Any]:
    spy = sector_proxy_prices[sector_proxy_prices["ticker"] == "SPY"].copy()
    spy["date"] = pd.to_datetime(spy["date"])
    spy = spy.sort_values("date").reset_index(drop=True)

    dates = timeline.sort_values("date").reset_index(drop=True)
    horizons = (1, 3, 12)
    per_date = dates[["date", "reported_regime", "raw_dominant_regime"]].copy()
    for months in horizons:
        per_date[f"fwd_{months}m"] = [
            _forward_return(spy, date, months) for date in per_date["date"]
        ]

    def cell(frame: pd.DataFrame, months: int, group_col: str) -> dict[str, Any]:
        column = f"fwd_{months}m"
        valid = frame[frame[group_col].notna() & frame[column].notna()]
        out = {}
        for label, group in valid.groupby(group_col):
            out[str(label)] = {"mean": _r(group[column].mean()), "n": int(len(group))}
        return dict(sorted(out.items()))

    overlapping: dict[str, Any] = {}
    for months in horizons:
        overlapping[f"{months}m"] = {
            "by_reported_regime": cell(per_date, months, "reported_regime"),
            "by_raw_dominant_regime": cell(per_date, months, "raw_dominant_regime"),
        }

    non_overlapping: dict[str, Any] = {}
    for months, stride in ((3, 3), (12, 12)):
        sampled = per_date.iloc[::stride].reset_index(drop=True)
        non_overlapping[f"{months}m"] = {
            "by_reported_regime": cell(sampled, months, "reported_regime"),
            "by_raw_dominant_regime": cell(sampled, months, "raw_dominant_regime"),
        }

    return {
        "basis": (
            "SPY close from sector_proxy_prices as the index; forward return from the price on "
            "or up to 7 days after the evaluation date, to the price on or up to 7 days after "
            "date + N months; grouped by historical_regime_timeline label at the evaluation date"
        ),
        "overlapping": {
            "note": "all months; consecutive 3m/12m windows overlap and are NOT independent observations",
            "horizons": overlapping,
        },
        "non_overlapping": {
            "note": "every 3rd month sampled for 3m, every 12th month for 12m, starting from the first evaluation month",
            "horizons": non_overlapping,
        },
    }


# ── item 9: sector rank IC ──────────────────────────────────────────────────


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 2:
        return None
    left_rank = frame["left"].rank()
    right_rank = frame["right"].rank()
    if left_rank.nunique() < 2 or right_rank.nunique() < 2:
        return None
    value = left_rank.corr(right_rank)
    return None if pd.isna(value) else float(value)


def _rank_ic_for_horizon(frame: pd.DataFrame, column: str, months: int) -> dict[str, Any]:
    valid = frame[frame[column].notna()]
    ic_values: list[float] = []
    top_quintile_positive = 0
    top_quintile_total = 0
    for _, group in valid.sort_values("sector_id").groupby("score_date"):
        if len(group) < 2:
            continue
        ic = _spearman(group["confidence_adjusted_score"], group[column])
        if ic is not None:
            ic_values.append(ic)
        ordered = group.sort_values(
            ["confidence_adjusted_score", "sector_id"], ascending=[False, True]
        )
        bucket_size = max(1, int(round(len(ordered) * 0.2)))
        top = ordered.head(bucket_size)[column]
        top_quintile_positive += int((top > 0).sum())
        top_quintile_total += int(len(top))

    series = pd.Series(ic_values, dtype=float)
    n = int(len(series))
    mean = float(series.mean()) if n else None
    sd = float(series.std()) if n > 1 else None
    naive_t = mean / (sd / math.sqrt(n)) if mean is not None and sd else None
    overlap_corrected_t = naive_t / math.sqrt(months) if naive_t is not None else None
    return {
        "n_dates": n,
        "mean_per_date_ic": _r(mean),
        "sd_per_date_ic": _r(sd),
        "naive_t": _r(naive_t),
        "overlap_corrected_t": _r(overlap_corrected_t),
        "positive_share_of_dates": _r(float((series > 0).mean())) if n else None,
        "top_quintile_hit_rate": _ratio(top_quintile_positive, top_quintile_total),
    }


def _measure_sector_rank_ic(sector_validation_returns: pd.DataFrame) -> dict[str, Any]:
    frame = sector_validation_returns[sector_validation_returns["valid"]].copy()
    frame["confidence_adjusted_score"] = pd.to_numeric(
        frame["confidence_adjusted_score"], errors="coerce"
    )
    eleven_row = frame[~frame["sector_id"].isin(SUB_INDUSTRY_SECTOR_IDS)]

    def horizons(subframe: pd.DataFrame) -> dict[str, Any]:
        return {
            "1m": _rank_ic_for_horizon(subframe, "relative_forward_1m_return", 1),
            "3m": _rank_ic_for_horizon(subframe, "relative_forward_3m_return", 3),
        }

    return {
        "basis": (
            "sector_validation_returns WHERE valid; per-date Spearman rank IC between "
            "confidence_adjusted_score and relative forward return; naive_t = mean/(sd/sqrt(n)); "
            "overlap_corrected_t divides naive_t by sqrt(horizon_months); top_quintile_hit_rate "
            "is the share of top-quintile-by-score picks with positive relative return (same "
            "definition as sectors/validation.py summarize_validation_returns.hit_rate_top_positive)"
        ),
        "sub_industry_sector_ids_excluded_from_11_row": sorted(SUB_INDUSTRY_SECTOR_IDS),
        "17_row_cross_section": horizons(frame),
        "11_row_cross_section": horizons(eleven_row),
    }


# ── item 10: regime_state buckets ───────────────────────────────────────────


def _measure_regime_state_buckets(
    dimension_scores: pd.DataFrame,
    raw_observations: pd.DataFrame,
    anchors_config_path: str | Path,
) -> dict[str, Any]:
    config = load_anchor_config(anchors_config_path)
    state = build_regime_state_frame(
        dimension_scores=dimension_scores, observations=raw_observations, config=config
    )
    total = int(len(state))
    result: dict[str, Any] = {
        "basis": (
            "anchors/multiples.py build_regime_state_frame: growth/inflation/credit bucketed "
            "from dimension_scores at +/-0.5, rate_band from raw_observations DGS10 at 2.5/4.0 "
            "(config/anchors.yaml regime_state)"
        ),
        "total_months": total,
    }
    for column in ("growth", "inflation", "credit", "rate_band"):
        counts = state[column].value_counts(dropna=False)
        result[column] = {
            str(bucket): {"count": int(count), "share": _ratio(int(count), total)}
            for bucket, count in sorted(counts.items())
        }
    return result


# ── item 11: screener quota table ───────────────────────────────────────────


def _tilt(score: float) -> int:
    return int(round(score * 4))


def _quota(score: float) -> int:
    return int(min(12, max(5, 8 + _tilt(score))))


def _load_mapped_gics_sector_ids(sectors_config_path: str | Path) -> list[str]:
    with Path(sectors_config_path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    all_ids = [entry["sector_id"] for entry in data.get("sectors", [])]
    return sorted(set(all_ids) - SUB_INDUSTRY_SECTOR_IDS)


def _measure_screener_quota_table(
    sector_scores: pd.DataFrame, sectors_config_path: str | Path
) -> dict[str, Any]:
    mapped_ids = _load_mapped_gics_sector_ids(sectors_config_path)
    frame = sector_scores[sector_scores["valid"]].copy()
    if frame.empty:
        return {"basis": "sector_scores WHERE valid", "as_of_date": None, "sectors": {}}
    frame["date"] = pd.to_datetime(frame["date"])
    as_of = frame["date"].max()
    latest = frame[frame["date"] == as_of].set_index("sector_id")

    sectors: dict[str, Any] = {}
    for sector_id in mapped_ids:
        if sector_id not in latest.index:
            continue
        row = latest.loc[sector_id]
        raw_score = float(row["raw_sector_score"])
        adjusted_score = float(row["confidence_adjusted_score"])
        sectors[sector_id] = {
            "raw_sector_score": _r(raw_score),
            "confidence_adjusted_score": _r(adjusted_score),
            "with_confidence_multiplier": {
                "tilt": _tilt(adjusted_score),
                "quota": _quota(adjusted_score),
            },
            "without_confidence_multiplier": {
                "tilt": _tilt(raw_score),
                "quota": _quota(raw_score),
            },
        }
    return {
        "basis": (
            "sector_scores WHERE valid, at the latest score date, for the 11 mapped GICS sectors "
            "in config/sectors.yaml (sub-industries excluded); tilt = round(score*4), "
            "quota = clamp(8+tilt, 5, 12); computed once from confidence_adjusted_score (with "
            "the confidence multiplier) and once from raw_sector_score (without it)"
        ),
        "as_of_date": _date_str(as_of),
        "sectors": dict(sorted(sectors.items())),
    }


# ── item 12: latest confidence ──────────────────────────────────────────────


def _measure_latest_confidence(timeline: pd.DataFrame) -> dict[str, Any]:
    row = timeline.sort_values("date").iloc[-1]
    return {
        "basis": "historical_regime_timeline.confidence at the latest date the table carries",
        "date": _date_str(row["date"]),
        "confidence": _r(row["confidence"]),
    }


# ── shared numeric helpers ───────────────────────────────────────────────────


def _r(value: Any, ndigits: int = _ROUND_NDIGITS) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(value), ndigits)


def _ratio(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return _r(numerator / denominator)


def _date_str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return pd.Timestamp(value).date().isoformat()


def _distribution(values: np.ndarray, include_n: bool = True) -> dict[str, Any]:
    series = pd.Series(values, dtype=float).dropna()
    if series.empty:
        out = {"n": 0, "min": None, "p25": None, "median": None, "mean": None, "p75": None, "max": None}
        return out if include_n else {k: v for k, v in out.items() if k != "n"}
    out = {
        "n": int(len(series)),
        "min": _r(series.min()),
        "p25": _r(series.quantile(0.25)),
        "median": _r(series.quantile(0.5)),
        "mean": _r(series.mean()),
        "p75": _r(series.quantile(0.75)),
        "max": _r(series.max()),
    }
    return out if include_n else {k: v for k, v in out.items() if k != "n"}


def _clean(obj: Any) -> Any:
    """Recursively convert numpy/pandas scalar types to native Python and NaN to None."""
    if isinstance(obj, dict):
        return {str(key): _clean(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(item) for item in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return None if math.isnan(value) else value
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


# ── output ───────────────────────────────────────────────────────────────────


def _write_pack(payload: dict[str, Any], *, out_dir: str | Path, db_path: str | Path) -> None:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    json_text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    (target / "baseline.json").write_text(json_text, encoding="utf-8")

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "note": "sidecar only -- excluded from the byte-identical comparison across runs",
    }
    (target / "baseline_meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    (target / "baseline.md").write_text(_build_markdown(payload), encoding="utf-8")


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# S0.5 Baseline Measurement Pack")
    lines.append("")
    lines.append(
        "Generated by `scripts/measure_baseline.py`. See `baseline.json` for the full, "
        "byte-identical-on-rerun payload this file summarizes; `baseline_meta.json` carries "
        "the run timestamp and is not part of the reproducibility comparison."
    )
    lines.append("")
    universe = payload["universe"]
    lines.append(
        f"Universe: {universe['total_months']} months, {universe['start_date']} .. {universe['end_date']}."
    )
    lines.append("")
    lines.append("## Recession triple (vs NBER)")
    lines.append("")
    lines.append("| label | share of months | precision | recall | TP | FP | FN |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for key in ("reported", "raw_dominant"):
        row = payload["recession_triple"][key]
        lines.append(
            f"| {key} | {row['label_share_of_all_months']:.3f} | {row['precision']:.3f} | "
            f"{row['recall']:.3f} | {row['true_positive']} | {row['false_positive']} | "
            f"{row['false_negative']} |"
        )
    lines.append("")
    lines.append("## Stability")
    lines.append("")
    for key in ("reported_regime", "raw_dominant_regime"):
        row = payload["stability"][key]
        spell = row["spell_length_months"]
        lines.append(
            f"- {key}: {row['switch_count']} switches; spell length months "
            f"min={spell['min']} p25={spell['p25']} median={spell['median']} "
            f"mean={spell['mean']} p75={spell['p75']} max={spell['max']} (n={spell['n']})"
        )
    lines.append("")
    lines.append("## Confidence distribution")
    lines.append("")
    conf = payload["confidence_distribution"]
    lines.append(
        f"- mean={conf['mean']} p25={conf['p25']} median={conf['median']} p75={conf['p75']} "
        f"max={conf['max']}; {conf['months_at_or_above_threshold']} months "
        f"({conf['share_at_or_above_threshold']:.3f}) at or above "
        f"min_confidence_to_switch={conf['min_confidence_to_switch']}"
    )
    lines.append("")
    lines.append("## Latest confidence")
    lines.append("")
    latest = payload["latest_confidence"]
    lines.append(f"- {latest['date']}: confidence={latest['confidence']}")
    lines.append("")
    lines.append("## S0.2 note")
    lines.append("")
    lines.append(
        "BAMLH0A0HYM2 (high-yield OAS, 30% of credit_liquidity) is settled separately in "
        "S0.2 -- see the commit for that work item, not this pack."
    )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
