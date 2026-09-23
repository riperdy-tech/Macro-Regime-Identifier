"""S0.5: the baseline measurement pack must be byte-identical across consecutive runs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "measure_baseline", Path(__file__).resolve().parents[1] / "scripts" / "measure_baseline.py"
)
measure_baseline = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(measure_baseline)  # type: ignore[union-attr]


def _build_fixture_store(db_path: Path) -> None:
    """A small DuckDB store shaped like the real one, exercising every table the script reads."""
    con = duckdb.connect(str(db_path))
    try:
        dates = pd.date_range("2020-01-01", periods=6, freq="MS")

        timeline = pd.DataFrame(
            {
                "date": dates,
                "dominant_regime": ["recession", "recession", "goldilocks", "goldilocks", "reflation", "reflation"],
                "dominant_probability": [0.4, 0.42, 0.3, 0.31, 0.35, 0.36],
                "second_regime": ["reflation"] * 6,
                "second_probability": [0.2] * 6,
                "confidence": [0.12, 0.15, 0.05, 0.2, 0.09, 0.18],
                "entropy": [1.1, 1.0, 1.3, 0.9, 1.2, 1.05],
                "valid_regime_count": [5] * 6,
                "valid": [True] * 6,
                "reason": ["ok"] * 6,
                "reported_regime": ["recession", "recession", "goldilocks", "goldilocks", "reflation", "reflation"],
                "reported_regime_probability": [0.4, 0.42, 0.3, 0.31, 0.35, 0.36],
                "reported_confidence": [0.12, 0.15, 0.05, 0.2, 0.09, 0.18],
                "raw_dominant_regime": ["recession", "goldilocks", "goldilocks", "goldilocks", "reflation", "recession"],
                "raw_dominant_probability": [0.4, 0.33, 0.3, 0.31, 0.35, 0.3],
                "raw_confidence": [0.12, 0.11, 0.05, 0.2, 0.09, 0.08],
                "transition_filter_applied": [False, True, False, False, False, True],
                "transition_filter_reason": ["no_filter"] * 6,
            }
        )
        con.execute("CREATE TABLE historical_regime_timeline AS SELECT * FROM timeline")

        regime_ids = ["goldilocks", "recession", "reflation", "stagflation", "tightening"]
        regime_rows = []
        for date in dates:
            for regime_id in regime_ids:
                regime_rows.append(
                    {
                        "regime_id": regime_id,
                        "date": date,
                        "raw_score": 0.1 if regime_id == "recession" else -0.05,
                        "probability": 0.4 if regime_id == "recession" else 0.15,
                        "rank": 1 if regime_id == "recession" else 2,
                        "valid_dimension_count": 5,
                        "configured_dimension_count": 5,
                        "coverage_ratio": 1.0,
                        "valid": True,
                        "reason": "ok",
                    }
                )
        regime_scores = pd.DataFrame(regime_rows)
        con.execute("CREATE TABLE regime_scores AS SELECT * FROM regime_scores")

        dimension_ids = ["growth_momentum", "inflation_pressure", "policy_stance", "credit_liquidity", "yield_curve"]
        contribution_rows = []
        for date in dates:
            for regime_id in regime_ids:
                for dimension_id in dimension_ids:
                    contribution_rows.append(
                        {
                            "regime_id": regime_id,
                            "dimension_id": dimension_id,
                            "date": date,
                            "dimension_score": 0.2,
                            "weight": 0.2,
                            "normalized_weight": 0.2,
                            "polarity": "positive",
                            "transformed_dimension_value": 0.2,
                            "contribution": 0.04,
                            "valid": True,
                            "reason": "ok",
                        }
                    )
        regime_dimension_contributions = pd.DataFrame(contribution_rows)
        con.execute(
            "CREATE TABLE regime_dimension_contributions AS SELECT * FROM regime_dimension_contributions"
        )

        dimension_rows = []
        for i, date in enumerate(dates):
            for dimension_id in dimension_ids:
                dimension_rows.append(
                    {
                        "dimension_id": dimension_id,
                        "date": date,
                        "score": 0.1 * (i - 2),
                        "valid_feature_count": 3,
                        "configured_feature_count": 3,
                        "total_configured_weight": 1.0,
                        "used_weight": 1.0,
                        "coverage_ratio": 1.0,
                        "valid": True,
                        "reason": "ok",
                    }
                )
        dimension_scores = pd.DataFrame(dimension_rows)
        con.execute("CREATE TABLE dimension_scores AS SELECT * FROM dimension_scores")

        rate_dates = pd.date_range("2019-12-01", periods=10, freq="MS")
        raw_observations = pd.DataFrame(
            {
                "series_id": ["DGS10"] * len(rate_dates),
                "date": rate_dates,
                "value": [3.0 + 0.05 * i for i in range(len(rate_dates))],
                "realtime_start": rate_dates,
                "realtime_end": rate_dates,
                "source": ["FRED"] * len(rate_dates),
                "fetched_at": [pd.Timestamp.now(tz="UTC")] * len(rate_dates),
                "frequency": ["daily"] * len(rate_dates),
                "units": ["%"] * len(rate_dates),
            }
        )
        con.execute("CREATE TABLE raw_observations AS SELECT * FROM raw_observations")

        mapped_sectors = [
            "communication_services",
            "consumer_discretionary",
            "consumer_staples",
            "energy",
            "financials",
            "health_care",
            "industrials",
            "information_technology",
            "materials",
            "real_estate",
            "utilities",
        ]
        sub_industries = ["semiconductors", "software", "banks", "biotech", "oil_gas_ep", "homebuilders"]
        all_sectors = mapped_sectors + sub_industries
        sector_score_rows = []
        for date in dates:
            for i, sector_id in enumerate(all_sectors):
                sector_score_rows.append(
                    {
                        "sector_id": sector_id,
                        "date": date,
                        "raw_sector_score": 0.05 * (i % 5 - 2),
                        "confidence_adjusted_score": 0.03 * (i % 5 - 2),
                        "rank": (i % 5) + 1,
                        "macro_reported_regime": "recession",
                        "macro_raw_dominant_regime": "recession",
                        "macro_confidence": 0.1,
                        "valid": True,
                        "reason": "ok",
                    }
                )
        sector_scores = pd.DataFrame(sector_score_rows)
        con.execute("CREATE TABLE sector_scores AS SELECT * FROM sector_scores")

        price_dates = pd.date_range("2019-12-01", periods=260, freq="B")
        sector_proxy_prices = pd.DataFrame(
            {
                "ticker": ["SPY"] * len(price_dates),
                "date": price_dates,
                "close": [100.0 + 0.1 * i for i in range(len(price_dates))],
                "source": ["mock"] * len(price_dates),
                "fetched_at": [pd.Timestamp.now(tz="UTC")] * len(price_dates),
            }
        )
        con.execute("CREATE TABLE sector_proxy_prices AS SELECT * FROM sector_proxy_prices")

        validation_rows = []
        for date in dates:
            for i, sector_id in enumerate(all_sectors):
                validation_rows.append(
                    {
                        "sector_id": sector_id,
                        "proxy_ticker": "XLX",
                        "score_date": date,
                        "sector_score": 0.05 * (i % 5 - 2),
                        "confidence_adjusted_score": 0.03 * (i % 5 - 2),
                        "forward_1m_return": 0.01 * (i % 3 - 1),
                        "forward_3m_return": 0.02 * (i % 3 - 1),
                        "relative_forward_1m_return": 0.01 * (i % 3 - 1),
                        "relative_forward_3m_return": 0.02 * (i % 3 - 1),
                        "valid": True,
                        "reason": "ok",
                    }
                )
        sector_validation_returns = pd.DataFrame(validation_rows)
        con.execute(
            "CREATE TABLE sector_validation_returns AS SELECT * FROM sector_validation_returns"
        )
    finally:
        con.close()


def test_baseline_pack_is_byte_identical_across_runs(tmp_path):
    db_path = tmp_path / "fixture.duckdb"
    _build_fixture_store(db_path)

    out_dir_1 = tmp_path / "run1"
    out_dir_2 = tmp_path / "run2"

    measure_baseline.build_baseline_pack(db_path=db_path, out_dir=out_dir_1)
    measure_baseline.build_baseline_pack(db_path=db_path, out_dir=out_dir_2)

    payload_1 = (out_dir_1 / "baseline.json").read_bytes()
    payload_2 = (out_dir_2 / "baseline.json").read_bytes()
    assert payload_1 == payload_2

    # The sidecar carries a run timestamp and is deliberately NOT required to match.
    meta_1 = json.loads((out_dir_1 / "baseline_meta.json").read_text())
    meta_2 = json.loads((out_dir_2 / "baseline_meta.json").read_text())
    assert "generated_at" in meta_1 and "generated_at" in meta_2


def test_baseline_pack_is_valid_json_with_sorted_keys(tmp_path):
    db_path = tmp_path / "fixture.duckdb"
    _build_fixture_store(db_path)

    out_dir = tmp_path / "run"
    payload = measure_baseline.build_baseline_pack(db_path=db_path, out_dir=out_dir)

    on_disk = json.loads((out_dir / "baseline.json").read_text())
    assert on_disk == payload

    # sort_keys=True at dump time means the raw text's key order matches Python's sorted().
    raw_text = (out_dir / "baseline.json").read_text()
    reserialized = json.dumps(json.loads(raw_text), indent=2, sort_keys=True) + "\n"
    assert raw_text == reserialized


def test_baseline_pack_does_not_open_the_store_for_write(tmp_path, monkeypatch):
    db_path = tmp_path / "fixture.duckdb"
    _build_fixture_store(db_path)

    real_connect = duckdb.connect
    seen_read_only = []

    def spy_connect(*args, **kwargs):
        seen_read_only.append(kwargs.get("read_only", False))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(measure_baseline.duckdb, "connect", spy_connect)
    measure_baseline.build_baseline_pack(db_path=db_path, out_dir=tmp_path / "run")

    assert seen_read_only, "expected the script to open the database at least once"
    assert all(seen_read_only), "measure_baseline.py must open the store with read_only=True"


def test_s1_1_intercepts_center_the_transform_within_0_02():
    """C8 / P0_0 §2.2 (MRI_S1_APPROVAL.md §2, condition C8): each regime-dimension pair
    that carries a non-default intercept must have a transform-attributable mean within
    ±0.02 of zero, measured on the real store's `dimension_scores`. This is the test the
    S1.1 commit was missing: without it, an intercept can go stale silently -- exactly
    what happened when S1.4 removed `ten_year_yield_level_z` from `policy_stance` and the
    0.889 centring value (measured on the pre-removal dimension) was never re-derived.
    The shipped value is now 0.814, re-derived directly from `dimension_scores`."""
    db_path = Path(__file__).resolve().parents[1] / "data" / "macro_engine.duckdb"
    if not db_path.exists():
        pytest.skip("real store not present")

    from macro_engine.regimes.config import load_regime_config
    from macro_engine.regimes.scoring import transform_dimension_value

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        scores = con.execute(
            "select dimension_id, score from dimension_scores where valid and score is not null"
        ).fetchdf()
    finally:
        con.close()

    config = load_regime_config(
        Path(__file__).resolve().parents[1] / "config" / "phase_b_sources.yaml"
    )
    checked = 0
    for regime in config.regimes:
        for dimension in regime.dimensions:
            if dimension.intercept == 0.0:
                continue
            dim_scores = scores.loc[scores["dimension_id"] == dimension.dimension_id, "score"]
            if dim_scores.empty:
                continue
            transformed = dim_scores.astype(float).apply(
                lambda value: transform_dimension_value(value, dimension.polarity)
                + dimension.intercept
            )
            mean = float(transformed.mean())
            assert abs(mean) <= 0.02, (
                f"{regime.regime_id}/{dimension.dimension_id}: transform-attributable mean "
                f"{mean:.4f} is outside +/-0.02 -- the intercept ({dimension.intercept}) is stale"
            )
            checked += 1
    assert checked > 0, "expected at least one intercepted regime-dimension pair to check"
