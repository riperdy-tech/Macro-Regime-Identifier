"""LLM confidence calibration instrumentation.

The DeepSeek classifier emits a `confidence` per sector impact, but that number
is not statistically calibrated: a stated 0.85 does not mean the call is right
85% of the time. This module logs every directional sector call to an
accumulating ledger and, once enough forward sector ETF prices exist, scores
each confidence bucket by realized hit rate and relative return.

It answers the question from the design review:

    Confidence Bucket | Hit Rate | Avg Sector Relative Return
    0.0-0.3           |    ?     |            ?
    0.3-0.6           |    ?     |            ?
    0.6-0.8           |    ?     |            ?
    0.8-1.0           |    ?     |            ?

Nothing here recalibrates confidence yet - it only instruments. A learned
transform (e.g. isotonic regression) is a later step once the ledger has enough
rows (target N >= 200 directional calls).

Pure functions (build_confidence_ledger, attach_forward_returns,
bucket_calibration) carry the logic and are unit-tested without a database. The
service wires them to stored classifications + sector proxy prices and persists
an accumulating ledger so data builds up over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.sectors.validation import (
    SectorValidationConfig,
    _forward_return,
    _prepared_price_lookup,
    load_sector_validation_config,
)
from macro_engine.storage.duckdb_store import DuckDBStore

# Directional sector calls only - mixed/neutral/unclear make no testable
# directional prediction, so they are excluded from hit-rate scoring.
EXPECTED_SIGN: dict[str, float] = {"tailwind": 1.0, "headwind": -1.0}

# Default confidence bucket edges (left-closed, right-closed on the final bin).
DEFAULT_BUCKET_EDGES: tuple[float, ...] = (0.0, 0.3, 0.6, 0.8, 1.0)

_LEDGER_KEY = ["classification_id", "sector_id", "prediction_date"]


# Schema version of the calibration artifact consumed by confidence_consumer.
# Bump in lockstep with confidence_consumer.SUPPORTED_SCHEMA_VERSION.
CALIBRATION_ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_MIN_DIRECTIONAL_CALLS = 200


@dataclass(frozen=True)
class ConfidenceCalibrationResult:
    ledger_path: Path
    ledger_rows: int
    bucket_tables: dict[str, pd.DataFrame]  # horizon label -> bucket table
    artifact: dict
    json_path: Path
    markdown_path: Path


# ---- pure logic ------------------------------------------------------------


def build_confidence_ledger(
    sector_impacts: pd.DataFrame,
    classifications: pd.DataFrame,
    *,
    news_items: pd.DataFrame | None = None,
    date_basis: str = "classified_at",
) -> pd.DataFrame:
    """One row per (classification, sector) directional-or-not call.

    Joins stored ``news_sector_impacts`` to ``news_classifications`` for the
    classification id, and sets prediction_date by ``date_basis``:

    - "classified_at" (default, LIVE): the date the call was produced - the
      operationally honest as-of date.
    - "published_at" (BACKFILL, provisional): the article's publication date,
      taken from ``news_items``. Lets historical calls resolve against existing
      forward prices, but pairs with hindsight-leaked LLM confidence, so the
      resulting calibration is provisional only.
    """
    columns = [
        "classification_id",
        "news_id",
        "prediction_date",
        "sector_id",
        "impact_direction",
        "impact_score",
        "confidence",
        "expected_sign",
    ]
    if sector_impacts is None or sector_impacts.empty:
        return pd.DataFrame(columns=columns)
    if date_basis not in ("classified_at", "published_at"):
        raise ValueError(f"unknown date_basis {date_basis}")

    impacts = sector_impacts.copy()
    meta = classifications[["classification_id", "news_id", "classified_at"]].copy()
    meta = meta.drop_duplicates(subset=["news_id"], keep="last")
    merged = impacts.merge(meta, on="news_id", how="left")

    if date_basis == "published_at":
        if news_items is None or "published_at" not in getattr(news_items, "columns", []):
            raise ValueError("date_basis='published_at' requires news_items with published_at")
        pub = news_items[["news_id", "published_at"]].drop_duplicates(subset=["news_id"], keep="last")
        merged = merged.merge(pub, on="news_id", how="left")
        basis_col = "published_at"
    else:
        basis_col = "classified_at"

    merged["prediction_date"] = pd.to_datetime(
        merged[basis_col], errors="coerce", utc=True
    ).dt.date
    merged["confidence"] = pd.to_numeric(merged["confidence"], errors="coerce")
    merged["impact_score"] = pd.to_numeric(merged["impact_score"], errors="coerce")
    merged["expected_sign"] = merged["impact_direction"].map(EXPECTED_SIGN)
    return merged[columns].reset_index(drop=True)


def attach_forward_returns(
    ledger: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    proxies: dict[str, str],
    benchmark_ticker: str,
    horizons_months: list[int],
) -> pd.DataFrame:
    """Add relative_forward_{h}m_return columns using sector ETF proxy prices.

    relative_forward = sector_proxy_forward_return - benchmark_forward_return,
    measured from prediction_date over each horizon (reuses the sector
    validation price machinery for identical semantics)."""
    result = ledger.copy()
    horizon_cols = [f"relative_forward_{h}m_return" for h in horizons_months]
    if result.empty:
        result["proxy_ticker"] = pd.Series(dtype="object")
        for col in horizon_cols:
            result[col] = pd.Series(dtype="float")
        return result

    price_lookup = _prepared_price_lookup(prices)
    result["proxy_ticker"] = result["sector_id"].map(proxies)
    for col in horizon_cols:
        result[col] = None

    for idx, row in result.iterrows():
        proxy = row["proxy_ticker"]
        pred_date = row["prediction_date"]
        if proxy is None or pd.isna(proxy) or pred_date is None or pd.isna(pred_date):
            continue
        start = pd.Timestamp(pred_date)
        for horizon in horizons_months:
            sector_ret = _forward_return(price_lookup, proxy, start, horizon)
            bench_ret = _forward_return(price_lookup, benchmark_ticker, start, horizon)
            if sector_ret is not None and bench_ret is not None:
                result.at[idx, f"relative_forward_{horizon}m_return"] = sector_ret - bench_ret
    return result


def bucket_calibration(
    ledger_with_returns: pd.DataFrame,
    *,
    horizon_months: int,
    bucket_edges: tuple[float, ...] = DEFAULT_BUCKET_EDGES,
) -> pd.DataFrame:
    """Hit-rate / relative-return table by confidence bucket for one horizon.

    Only directional calls (tailwind/headwind) with a non-null relative forward
    return are scored. ``hit`` = realized relative move matched the predicted
    direction. ``avg_signed_relative_return`` orients the move by the predicted
    direction (positive = confirmed); ``avg_raw_relative_return`` is unsigned.
    """
    columns = [
        "confidence_bucket",
        "n",
        "hit_rate",
        "avg_signed_relative_return",
        "avg_raw_relative_return",
    ]
    return_col = f"relative_forward_{horizon_months}m_return"
    if ledger_with_returns.empty or return_col not in ledger_with_returns:
        return _empty_buckets(bucket_edges, columns)

    df = ledger_with_returns.copy()
    df = df[df["expected_sign"].notna()]
    df[return_col] = pd.to_numeric(df[return_col], errors="coerce")
    df = df[df[return_col].notna()]
    if df.empty:
        return _empty_buckets(bucket_edges, columns)

    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").clip(0.0, 1.0)
    labels = _bucket_labels(bucket_edges)
    df["confidence_bucket"] = pd.cut(
        df["confidence"], bins=list(bucket_edges), labels=labels, include_lowest=True
    )
    df["signed_return"] = df["expected_sign"] * df[return_col]
    df["hit"] = (df["signed_return"] > 0).astype(float)

    rows: list[dict[str, Any]] = []
    grouped = {label: group for label, group in df.groupby("confidence_bucket", observed=False)}
    for label in labels:
        group = grouped.get(label)
        if group is None or group.empty:
            rows.append({"confidence_bucket": label, "n": 0, "hit_rate": None,
                         "avg_signed_relative_return": None, "avg_raw_relative_return": None})
            continue
        rows.append(
            {
                "confidence_bucket": label,
                "n": int(len(group)),
                "hit_rate": float(group["hit"].mean()),
                "avg_signed_relative_return": float(group["signed_return"].mean()),
                "avg_raw_relative_return": float(group[return_col].mean()),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _bucket_labels(edges: tuple[float, ...]) -> list[str]:
    return [f"{edges[i]:.1f}-{edges[i + 1]:.1f}" for i in range(len(edges) - 1)]


def _empty_buckets(edges: tuple[float, ...], columns: list[str]) -> pd.DataFrame:
    rows = [
        {"confidence_bucket": label, "n": 0, "hit_rate": None,
         "avg_signed_relative_return": None, "avg_raw_relative_return": None}
        for label in _bucket_labels(edges)
    ]
    return pd.DataFrame(rows, columns=columns)


def build_calibration_artifact(
    ledger_with_returns: pd.DataFrame,
    *,
    horizons_months: list[int],
    min_directional_calls: int = DEFAULT_MIN_DIRECTIONAL_CALLS,
    provisional: bool = False,
) -> dict:
    """Emit the artifact dict consumed by confidence_consumer (the 5->6 bridge).

    Counts directional calls that have a realized forward return for each
    horizon and reports readiness. This is the gate that unlocks the consumer
    valve. It carries NO learned transform: `fitted` is always False here, so
    the consumer stays identity until a real transform is trained. Fitting
    isotonic/logistic would add a dependency and requires
    directional_calls >= min_directional_calls - deliberately deferred."""
    horizons: list[str] = []
    directional_calls = 0
    if ledger_with_returns is not None and not ledger_with_returns.empty:
        directional = ledger_with_returns[ledger_with_returns["expected_sign"].notna()]
        per_horizon_counts = {}
        for h in horizons_months:
            col = f"relative_forward_{h}m_return"
            if col in directional:
                n = int(pd.to_numeric(directional[col], errors="coerce").notna().sum())
            else:
                n = 0
            per_horizon_counts[f"{h}m"] = n
            if n >= min_directional_calls:
                horizons.append(f"{h}m")
        directional_calls = max(per_horizon_counts.values(), default=0)
    return {
        "schema_version": CALIBRATION_ARTIFACT_SCHEMA_VERSION,
        "directional_calls": int(directional_calls),
        "min_directional_calls": int(min_directional_calls),
        # Only horizons with enough samples are advertised as supported.
        "horizons": horizons,
        "ready": bool(horizons),
        # No learned transform trained yet: consumer remains identity even when
        # ready. Train and set this True in a later, dependency-gated step.
        "fitted": False,
        # True when any rows were dated by published_at (backfill): the fit is
        # hindsight-biased and must be re-validated on live calls before use.
        "provisional": bool(provisional),
    }


def merge_ledger(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Accumulate: union old + new ledger rows, keeping the latest per call key."""
    if existing is None or existing.empty:
        return new.reset_index(drop=True)
    if new.empty:
        return existing.reset_index(drop=True)
    combined = pd.concat([existing, new], ignore_index=True)
    return combined.drop_duplicates(subset=_LEDGER_KEY, keep="last").reset_index(drop=True)


# ---- service (impure) ------------------------------------------------------


def run_confidence_calibration(
    *,
    config_path: str | Path = "config/sector_validation.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    output_dir: str | Path | None = None,
    bucket_edges: tuple[float, ...] = DEFAULT_BUCKET_EDGES,
    date_basis: str = "classified_at",
) -> ConfidenceCalibrationResult:
    """Build + persist the accumulating confidence ledger and write the
    bucket-calibration report (JSON + Markdown).

    date_basis="published_at" runs BACKFILL mode (provisional, hindsight-biased
    - see gdelt_backfill); default "classified_at" is the live, honest basis."""
    config: SectorValidationConfig = load_sector_validation_config(config_path)
    out_dir = Path(output_dir) if output_dir else Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    store = DuckDBStore(db_path)
    store.initialize()
    sector_impacts = store.read_table("news_sector_impacts")
    classifications = store.read_table("news_classifications")
    news_items = store.read_table("news_items")
    prices = store.read_sector_proxy_prices()

    provisional = date_basis == "published_at"
    # Live runs must not be contaminated by backfilled (e.g. GDELT) items - those
    # carry hindsight + headline-only confidence. Under classified_at they would
    # masquerade as live calls, so exclude them from the live ledger.
    if not provisional and not news_items.empty and "provider" in news_items.columns:
        backfill_ids = set(
            news_items.loc[news_items["provider"] == "gdelt", "news_id"].astype(str)
        )
        if backfill_ids:
            sector_impacts = sector_impacts[
                ~sector_impacts["news_id"].astype(str).isin(backfill_ids)
            ].copy()
    ledger = build_confidence_ledger(
        sector_impacts, classifications, news_items=news_items, date_basis=date_basis
    )
    ledger = attach_forward_returns(
        ledger,
        prices,
        proxies=config.proxies,
        benchmark_ticker=config.benchmark_ticker,
        horizons_months=config.horizons_months,
    )

    suffix = "_backfill" if provisional else ""
    ledger_path = out_dir / f"confidence_calibration_ledger{suffix}.parquet"
    existing = pd.read_parquet(ledger_path) if ledger_path.exists() else pd.DataFrame()
    accumulated = merge_ledger(existing, ledger)
    accumulated.to_parquet(ledger_path, index=False)

    bucket_tables = {
        f"{horizon}m": bucket_calibration(
            accumulated, horizon_months=horizon, bucket_edges=bucket_edges
        )
        for horizon in config.horizons_months
    }
    artifact = build_calibration_artifact(
        accumulated, horizons_months=config.horizons_months, provisional=provisional
    )
    artifact_path = out_dir / f"confidence_calibration_artifact{suffix}.json"
    _write_json(artifact_path, artifact)

    json_path, markdown_path = _write_reports(
        out_dir, accumulated, bucket_tables, artifact, suffix=suffix
    )
    return ConfidenceCalibrationResult(
        ledger_path=ledger_path,
        ledger_rows=int(len(accumulated)),
        bucket_tables=bucket_tables,
        artifact=artifact,
        json_path=json_path,
        markdown_path=markdown_path,
    )


def _write_json(path: Path, payload: dict) -> None:
    import json

    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_reports(
    out_dir: Path,
    ledger: pd.DataFrame,
    bucket_tables: dict[str, pd.DataFrame],
    artifact: dict,
    *,
    suffix: str = "",
) -> tuple[Path, Path]:
    import json

    directional = int(ledger["expected_sign"].notna().sum()) if not ledger.empty else 0
    payload = {
        "ledger_rows": int(len(ledger)),
        "directional_calls": directional,
        "calibration_artifact": artifact,
        "buckets_by_horizon": {
            horizon: table.to_dict(orient="records")
            for horizon, table in bucket_tables.items()
        },
        "notes": (
            "Instrumentation only - no confidence recalibration applied. "
            "Diagnostic, not trading guidance. Hit rates are unreliable until "
            "directional_calls is large (target >= 200). The consumer valve "
            "stays identity even when ready=true until a transform is fitted."
        ),
    }
    json_path = out_dir / f"confidence_calibration{suffix}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = ["# LLM Confidence Calibration", "",
             f"Ledger rows: {len(ledger)}  |  Directional calls: {directional}",
             "",
             "Diagnostic instrumentation only. No recalibration applied. "
             "Buckets are unreliable until directional calls are plentiful (target >= 200).",
             ""]
    for horizon, table in bucket_tables.items():
        lines.append(f"## Horizon {horizon}")
        lines.append("")
        lines.append("| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in table.to_dict(orient="records"):
            lines.append(
                "| {bucket} | {n} | {hr} | {sgn} | {raw} |".format(
                    bucket=row["confidence_bucket"],
                    n=row["n"],
                    hr=_fmt(row["hit_rate"]),
                    sgn=_fmt(row["avg_signed_relative_return"]),
                    raw=_fmt(row["avg_raw_relative_return"]),
                )
            )
        lines.append("")
    markdown_path = out_dir / f"confidence_calibration{suffix}.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def _fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return f"{value:.4f}"
