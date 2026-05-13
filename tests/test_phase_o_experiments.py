from __future__ import annotations

import json

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.experiments.config import load_calibration_experiment_config
from macro_engine.experiments.runner import run_calibration_experiments
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_l_experiments import _dimension_scores


def test_phase_o_config_validates():
    config = load_calibration_experiment_config("config/experiments/phase_o.yaml")

    assert config.experiment.name == "phase_o"
    assert config.experiment.output_dir == "outputs/experiments/phase_o"
    assert {variant.variant_id for variant in config.variants} >= {
        "baseline",
        "tightening_growth_resilience",
        "stagflation_interaction_reduced_additive",
        "reflation_inflation_cap",
        "recession_growth_confirmation",
        "combined_overlap_reduction",
    }


def test_phase_o_experiments_write_outputs_without_production_overwrite(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "phase_o"
    config_path = tmp_path / "phase_o.yaml"
    phase_o_config = open("config/experiments/phase_o.yaml", encoding="utf-8").read()
    phase_o_config = phase_o_config.replace(
        "output_dir: outputs/experiments/phase_o",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(phase_o_config, encoding="utf-8")

    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_dimension_outputs(pd.DataFrame(), _dimension_scores(), pd.DataFrame())

    result = run_calibration_experiments(experiment_config_path=config_path, db_path=db_path)

    assert len(result.variant_results) == 8
    assert (output_dir / "baseline.json").exists()
    assert (output_dir / "combined_overlap_reduction.json").exists()
    comparison = json.loads((output_dir / "comparison.json").read_text())
    assert {row["variant_id"] for row in comparison} >= {
        "baseline",
        "policy_tightening_heavy",
        "combined_overlap_reduction",
    }
    baseline = json.loads((output_dir / "baseline.json").read_text())
    assert "stagflation__tightening" in baseline["pairwise_raw_score_correlations"]
    assert store.read_table("regime_scores").empty


def test_phase_o_cli_works(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "phase_o"
    config_path = tmp_path / "phase_o.yaml"
    phase_o_config = open("config/experiments/phase_o.yaml", encoding="utf-8").read()
    phase_o_config = phase_o_config.replace(
        "output_dir: outputs/experiments/phase_o",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(phase_o_config, encoding="utf-8")

    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_dimension_outputs(pd.DataFrame(), _dimension_scores(), pd.DataFrame())

    result = CliRunner().invoke(
        app,
        [
            "run-calibration-experiments",
            "--experiment-config",
            str(config_path),
            "--db-path",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    assert "variant_count" in result.output

