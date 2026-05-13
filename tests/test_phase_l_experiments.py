from __future__ import annotations

import json

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.experiments.config import load_calibration_experiment_config
from macro_engine.experiments.runner import run_calibration_experiments
from macro_engine.storage.duckdb_store import DuckDBStore


def _dimension_scores() -> pd.DataFrame:
    rows = []
    dates = ["2026-01-01", "2026-02-01", "2026-03-01"]
    values = {
        "growth_momentum": [-0.5, 0.2, 0.4],
        "inflation_pressure": [1.2, 0.5, -0.2],
        "policy_stance": [-0.1, -0.4, 0.1],
        "credit_liquidity": [0.4, 0.2, -0.3],
        "yield_curve": [0.2, 0.1, -0.5],
    }
    for date_index, date in enumerate(dates):
        for dimension_id, scores in values.items():
            rows.append(
                {
                    "dimension_id": dimension_id,
                    "date": date,
                    "score": scores[date_index],
                    "valid_feature_count": 2,
                    "configured_feature_count": 2,
                    "total_configured_weight": 1.0,
                    "used_weight": 1.0,
                    "coverage_ratio": 1.0,
                    "valid": True,
                    "reason": "ok",
                }
            )
    return pd.DataFrame(rows)


def _experiment_config(tmp_path) -> str:
    output_dir = tmp_path / "experiments"
    config_path = tmp_path / "phase_l.yaml"
    config_path.write_text(
        f"""
experiment:
  name: test_phase_l
  output_dir: {output_dir.as_posix()}
  base_config: config/phase_b_sources.yaml
variants:
  - variant_id: baseline
    description: Baseline formulas.
    softmax_temperature: 1.0
  - variant_id: temperature_0_5
    description: Lower temperature.
    softmax_temperature: 0.5
  - variant_id: sharper_stagflation
    description: Interaction-style stagflation test.
    softmax_temperature: 1.0
    regime_overrides:
      stagflation:
        dimensions:
          - dimension_id: inflation_pressure
            weight: 0.3
            polarity: positive
          - dimension_id: growth_momentum
            weight: 0.2
            polarity: negative
          - dimension_id: credit_liquidity
            weight: 0.1
            polarity: negative
          - dimension_id: policy_stance
            weight: 0.1
            polarity: negative
          - dimension_id: yield_curve
            weight: 0.1
            polarity: negative
        interactions:
          - interaction_id: inflation_x_weak_growth
            weight: 0.2
            components:
              - dimension_id: inflation_pressure
                polarity: positive_only
              - dimension_id: growth_momentum
                polarity: negative_only
""",
        encoding="utf-8",
    )
    return str(config_path)


def test_phase_l_config_validates():
    config = load_calibration_experiment_config("config/experiments/phase_l.yaml")

    assert config.experiment.name == "phase_l"
    assert {variant.variant_id for variant in config.variants} >= {
        "baseline",
        "temperature_0_6",
        "combined_formula_sharpening",
    }


def test_calibration_experiments_write_deterministic_outputs(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_dimension_outputs(pd.DataFrame(), _dimension_scores(), pd.DataFrame())

    result = run_calibration_experiments(
        experiment_config_path=_experiment_config(tmp_path),
        db_path=db_path,
    )

    assert len(result.variant_results) == 3
    assert set(result.comparison["variant_id"]) == {
        "baseline",
        "temperature_0_5",
        "sharper_stagflation",
    }
    assert (result.output_dir / "baseline.json").exists()
    assert (result.output_dir / "comparison.json").exists()
    baseline = json.loads((result.output_dir / "baseline.json").read_text())
    assert set(baseline) >= {
        "variant_id",
        "latest",
        "metrics",
        "dominant_regime_distribution",
        "pairwise_raw_score_correlations",
    }
    assert store.read_table("regime_scores").empty


def test_calibration_experiment_cli_works(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_dimension_outputs(pd.DataFrame(), _dimension_scores(), pd.DataFrame())
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-calibration-experiments",
            "--experiment-config",
            _experiment_config(tmp_path),
            "--db-path",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    assert "variant_count" in result.output

