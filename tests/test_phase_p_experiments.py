from __future__ import annotations

import json

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.experiments.config import load_calibration_experiment_config
from macro_engine.experiments.runner import run_calibration_experiments
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_l_experiments import _dimension_scores


def test_phase_p_config_validates():
    config = load_calibration_experiment_config("config/experiments/phase_p.yaml")

    assert config.experiment.name == "phase_p"
    assert config.experiment.output_dir == "outputs/experiments/phase_p"
    assert {variant.variant_id for variant in config.variants} >= {
        "baseline",
        "policy_tightening_heavy_v2",
        "tightening_growth_resilience_lighter",
        "reflation_soft_inflation_cap",
        "recession_inflation_gate",
        "combined_best_candidate",
    }


def test_phase_p_experiments_write_transition_review(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "phase_p"
    config_path = tmp_path / "phase_p.yaml"
    phase_p_config = open("config/experiments/phase_p.yaml", encoding="utf-8").read()
    phase_p_config = phase_p_config.replace(
        "output_dir: outputs/experiments/phase_p",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(phase_p_config, encoding="utf-8")

    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_dimension_outputs(pd.DataFrame(), _dimension_scores(), pd.DataFrame())

    result = run_calibration_experiments(experiment_config_path=config_path, db_path=db_path)

    assert len(result.variant_results) == 8
    baseline = json.loads((output_dir / "baseline.json").read_text())
    assert "transition_review" in baseline
    assert "latest_transitions" in baseline["transition_review"]
    assert "near_zero_confidence_transition_count" in baseline["transition_review"]
    assert store.read_table("regime_scores").empty


def test_phase_p_cli_works(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "phase_p"
    config_path = tmp_path / "phase_p.yaml"
    phase_p_config = open("config/experiments/phase_p.yaml", encoding="utf-8").read()
    phase_p_config = phase_p_config.replace(
        "output_dir: outputs/experiments/phase_p",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(phase_p_config, encoding="utf-8")

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

