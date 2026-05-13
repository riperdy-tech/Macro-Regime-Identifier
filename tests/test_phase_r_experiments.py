from __future__ import annotations

import json

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.diagnostics.runner import build_regime_transitions
from macro_engine.experiments.config import (
    TransitionFilterConfig,
    load_calibration_experiment_config,
)
from macro_engine.experiments.runner import _apply_transition_filter, run_calibration_experiments
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_l_experiments import _dimension_scores


def test_phase_r_config_validates():
    config = load_calibration_experiment_config("config/experiments/phase_r.yaml")

    assert config.experiment.name == "phase_r"
    assert config.experiment.output_dir == "outputs/experiments/phase_r"
    assert {variant.variant_id for variant in config.variants} == {
        "baseline",
        "policy_tightening_heavy_v2_raw",
        "policy_tightening_heavy_v2_gap_0_01",
        "policy_tightening_heavy_v2_gap_0_02",
        "policy_tightening_heavy_v2_gap_0_03",
        "policy_tightening_heavy_v2_gap_0_02_light_persistence",
    }
    filtered = config.variants[2]
    assert filtered.transition_filter is not None
    assert filtered.transition_filter.min_confidence_to_switch == 0.01


def test_transition_filter_keeps_raw_probability_timeline_but_filters_reported_switches():
    timeline = pd.DataFrame(
        [
            _timeline_row("2026-01-01", "recession", 0.35, "tightening", 0.30, 0.05),
            _timeline_row("2026-02-01", "tightening", 0.31, "recession", 0.305, 0.005),
            _timeline_row("2026-03-01", "recession", 0.32, "tightening", 0.314, 0.006),
            _timeline_row("2026-04-01", "tightening", 0.36, "recession", 0.31, 0.05),
        ]
    )

    filtered = _apply_transition_filter(
        timeline,
        TransitionFilterConfig(min_confidence_to_switch=0.01),
    )
    transitions = build_regime_transitions(filtered)

    assert list(filtered["raw_dominant_regime"]) == [
        "recession",
        "tightening",
        "recession",
        "tightening",
    ]
    assert list(filtered["dominant_regime"]) == [
        "recession",
        "recession",
        "recession",
        "tightening",
    ]
    assert len(transitions) == 1
    assert transitions.iloc[0]["transition_date"] == "2026-04-01"


def test_phase_r_experiments_write_filtered_transition_review(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "phase_r"
    config_path = tmp_path / "phase_r.yaml"
    phase_r_config = open("config/experiments/phase_r.yaml", encoding="utf-8").read()
    phase_r_config = phase_r_config.replace(
        "output_dir: outputs/experiments/phase_r",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(phase_r_config, encoding="utf-8")

    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_dimension_outputs(pd.DataFrame(), _dimension_scores(), pd.DataFrame())

    result = run_calibration_experiments(experiment_config_path=config_path, db_path=db_path)

    assert len(result.variant_results) == 6
    filtered = json.loads(
        (output_dir / "policy_tightening_heavy_v2_gap_0_02.json").read_text()
    )
    assert filtered["transition_filter"]["min_confidence_to_switch"] == 0.02
    assert "filtered_transition_review" in filtered
    assert "raw_regime_switches" in filtered["filtered_transition_review"]
    assert "filtered_regime_switches" in filtered["filtered_transition_review"]
    assert "latest_filtered_transitions" in filtered["filtered_transition_review"]
    assert store.read_table("regime_scores").empty


def test_phase_r_cli_works(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "phase_r"
    config_path = tmp_path / "phase_r.yaml"
    phase_r_config = open("config/experiments/phase_r.yaml", encoding="utf-8").read()
    phase_r_config = phase_r_config.replace(
        "output_dir: outputs/experiments/phase_r",
        f"output_dir: {output_dir.as_posix()}",
    )
    config_path.write_text(phase_r_config, encoding="utf-8")

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


def _timeline_row(
    date: str,
    dominant_regime: str,
    dominant_probability: float,
    second_regime: str,
    second_probability: float,
    confidence: float,
) -> dict:
    return {
        "date": date,
        "dominant_regime": dominant_regime,
        "dominant_probability": dominant_probability,
        "second_regime": second_regime,
        "second_probability": second_probability,
        "confidence": confidence,
        "entropy": 1.0,
        "valid_regime_count": 5,
        "valid": True,
        "reason": "revised_data_diagnostic",
    }
