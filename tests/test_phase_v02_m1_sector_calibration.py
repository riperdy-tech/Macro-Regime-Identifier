from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.sectors.calibration import (
    load_sector_calibration_config,
    run_sector_calibration_experiments,
)
from macro_engine.sectors.validation import normalize_price_frame
from macro_engine.storage.duckdb_store import DuckDBStore

runner = CliRunner()


def test_sector_calibration_config_validates():
    config = load_sector_calibration_config(
        "config/experiments/sector_calibration_v02_m1.yaml"
    )

    assert config.experiment.primary_horizon == "3m"
    assert {variant.variant_id for variant in config.variants} >= {
        "baseline_current_assumptions",
        "dimension_only_no_regime_prior",
        "combined_candidate_1",
    }


def test_sector_calibration_experiments_write_outputs_without_mutating_store(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    config_path = _write_calibration_config(tmp_path)
    store = DuckDBStore(db_path)
    store.initialize()
    _seed_macro_outputs(store)
    store.upsert_sector_proxy_prices(_proxy_prices())

    result = run_sector_calibration_experiments(
        experiment_config_path=config_path,
        db_path=db_path,
    )

    assert len(result.variant_results) == 2
    assert set(result.comparison["variant_id"]) == {
        "baseline_current_assumptions",
        "dimension_only_no_regime_prior",
    }
    assert (result.output_dir / "baseline_current_assumptions.json").exists()
    assert (result.output_dir / "comparison.json").exists()
    payload = json.loads((result.output_dir / "comparison.json").read_text())
    assert set(payload[0]) >= {
        "variant_id",
        "primary_rank_ic",
        "primary_top_minus_bottom_spread",
    }
    assert store.read_table("sector_scores").empty


def test_sector_calibration_cli_works(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    config_path = _write_calibration_config(tmp_path)
    store = DuckDBStore(db_path)
    store.initialize()
    _seed_macro_outputs(store)
    store.upsert_sector_proxy_prices(_proxy_prices())

    result = runner.invoke(
        app,
        [
            "run-sector-calibration-experiments",
            "--experiment-config",
            str(config_path),
            "--db-path",
            str(db_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "variant_count" in result.output


def _write_calibration_config(tmp_path: Path) -> Path:
    path = tmp_path / "sector_calibration.yaml"
    output_dir = tmp_path / "experiments"
    path.write_text(
        f"""
experiment:
  macro_config: config/phase_b_sources.yaml
  sector_config: config/sectors.yaml
  exposure_config: config/sector_exposures.yaml
  prior_config: config/sector_regime_priors.yaml
  validation_config: config/sector_validation.yaml
  output_dir: {output_dir.as_posix()}
  primary_horizon: 3m
  secondary_horizon: 1m
variants:
  - variant_id: baseline_current_assumptions
    description: Baseline.
  - variant_id: dimension_only_no_regime_prior
    description: Dimension only.
    regime_prior_weight: 0.0
""",
        encoding="utf-8",
    )
    return path


def _seed_macro_outputs(store: DuckDBStore) -> None:
    dates = pd.date_range("2026-01-01", periods=5, freq="MS")
    regimes = ["goldilocks", "reflation", "stagflation", "recession", "tightening"]
    regime_rows = []
    health_rows = []
    timeline_rows = []
    dimension_rows = []
    for date_index, date in enumerate(dates):
        probabilities = {
            "goldilocks": 0.25,
            "reflation": 0.30,
            "stagflation": 0.20,
            "recession": 0.15,
            "tightening": 0.10,
        }
        for rank, regime_id in enumerate(regimes, start=1):
            regime_rows.append(
                {
                    "regime_id": regime_id,
                    "date": date.date(),
                    "raw_score": float(1.0 / rank),
                    "probability": probabilities[regime_id],
                    "rank": rank,
                    "valid_dimension_count": 5,
                    "configured_dimension_count": 5,
                    "coverage_ratio": 1.0,
                    "valid": True,
                    "reason": "ok",
                }
            )
        health_rows.append(
            {
                "date": date.date(),
                "valid": True,
                "dominant_regime": "reflation",
                "dominant_probability": 0.30,
                "confidence": 0.05,
                "entropy": 1.5,
                "valid_regime_count": 5,
                "reason": "ok",
            }
        )
        timeline_rows.append(
            {
                "date": date.date(),
                "dominant_regime": "reflation",
                "dominant_probability": 0.30,
                "reported_regime": "reflation",
                "reported_regime_probability": 0.30,
                "reported_confidence": 0.05,
                "raw_dominant_regime": "reflation",
                "raw_dominant_probability": 0.30,
                "raw_confidence": 0.05,
                "second_regime": "goldilocks",
                "second_probability": 0.25,
                "confidence": 0.05,
                "entropy": 1.5,
                "valid_regime_count": 5,
                "valid": True,
                "transition_filter_applied": False,
                "transition_filter_reason": "test",
                "reason": "ok",
            }
        )
        dimensions = {
            "growth_momentum": 0.2 + (date_index * 0.1),
            "inflation_pressure": 0.4 - (date_index * 0.05),
            "policy_stance": -0.1,
            "credit_liquidity": -0.2 + (date_index * 0.02),
            "yield_curve": 0.1,
        }
        for dimension_id, score in dimensions.items():
            dimension_rows.append(
                {
                    "dimension_id": dimension_id,
                    "date": date.date(),
                    "score": score,
                    "valid_feature_count": 2,
                    "configured_feature_count": 2,
                    "total_configured_weight": 1.0,
                    "used_weight": 1.0,
                    "coverage_ratio": 1.0,
                    "valid": True,
                    "reason": "ok",
                }
            )
    store.replace_regime_outputs(pd.DataFrame(), pd.DataFrame(regime_rows), pd.DataFrame(health_rows))
    store.replace_diagnostic_outputs(pd.DataFrame(timeline_rows), pd.DataFrame(), pd.DataFrame())
    store.replace_dimension_outputs(pd.DataFrame(), pd.DataFrame(dimension_rows), pd.DataFrame())


def _proxy_prices() -> pd.DataFrame:
    tickers = ["SPY", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLK", "XLB", "XLRE", "XLU"]
    rows = []
    dates = pd.date_range("2026-01-01", periods=9, freq="MS")
    for ticker_index, ticker in enumerate(tickers):
        for date_index, date in enumerate(dates):
            rows.append(
                {
                    "ticker": ticker,
                    "date": date.date(),
                    "close": 100.0 + ticker_index + (date_index * (1.0 + ticker_index / 20.0)),
                }
            )
    return normalize_price_frame(pd.DataFrame(rows), source="mock")
