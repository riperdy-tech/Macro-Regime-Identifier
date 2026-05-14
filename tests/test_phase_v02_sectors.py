from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner
import yaml

from macro_engine.cli import app
from macro_engine.sectors.config import (
    SectorConfig,
    SectorDefinition,
    SectorScoringConfig,
    load_sector_config,
)
from macro_engine.sectors.report import build_current_sector_report, current_sector_report_markdown
from macro_engine.sectors.scoring import build_sector_scores
from macro_engine.storage.duckdb_store import DuckDBStore

runner = CliRunner()


def _toy_sector_config() -> SectorConfig:
    return SectorConfig(
        sectors=[
            SectorDefinition(
                sector_id="energy",
                label="Energy",
                proxy_ticker="XLE",
            ),
            SectorDefinition(
                sector_id="utilities",
                label="Utilities",
                proxy_ticker="XLU",
            ),
        ],
        exposures={
            "energy": {
                "growth_momentum": 0.5,
                "inflation_pressure": 1.0,
            },
            "utilities": {
                "growth_momentum": -0.5,
                "inflation_pressure": -0.2,
            },
        },
        regime_priors={
            "reflation": {
                "energy": 0.4,
                "utilities": -0.2,
            },
            "recession": {
                "energy": -0.2,
                "utilities": 0.4,
            },
        },
        scoring=SectorScoringConfig(min_multiplier=0.40, max_multiplier=1.00),
    )


def _regime_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "regime_id": "reflation",
                "date": "2026-01-01",
                "raw_score": 1.0,
                "probability": 0.7,
                "rank": 1,
                "valid_dimension_count": 2,
                "configured_dimension_count": 2,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
            {
                "regime_id": "recession",
                "date": "2026-01-01",
                "raw_score": 0.2,
                "probability": 0.3,
                "rank": 2,
                "valid_dimension_count": 2,
                "configured_dimension_count": 2,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def _regime_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "valid": True,
                "dominant_regime": "reflation",
                "dominant_probability": 0.7,
                "confidence": 0.4,
                "entropy": 0.6,
                "valid_regime_count": 2,
                "reason": "ok",
            }
        ]
    )


def _timeline(confidence: float = 0.25) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "dominant_regime": "reflation",
                "dominant_probability": 0.7,
                "reported_regime": "reflation",
                "reported_regime_probability": 0.7,
                "reported_confidence": confidence,
                "raw_dominant_regime": "reflation",
                "raw_dominant_probability": 0.7,
                "raw_confidence": confidence,
                "second_regime": "recession",
                "second_probability": 0.3,
                "confidence": confidence,
                "entropy": 0.6,
                "valid_regime_count": 2,
                "valid": True,
                "transition_filter_applied": True,
                "transition_filter_reason": "raw_signal_confirmed",
                "reason": "revised_data_diagnostic",
            }
        ]
    )


def _dimension_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "dimension_id": "growth_momentum",
                "date": "2026-01-01",
                "score": 0.6,
                "valid_feature_count": 2,
                "configured_feature_count": 2,
                "total_configured_weight": 1.0,
                "used_weight": 1.0,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
            {
                "dimension_id": "inflation_pressure",
                "date": "2026-01-01",
                "score": 0.8,
                "valid_feature_count": 2,
                "configured_feature_count": 2,
                "total_configured_weight": 1.0,
                "used_weight": 1.0,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def test_sector_config_loads_default_files():
    config = load_sector_config()

    assert len(config.sectors) == 11
    assert config.sectors[0].sector_id == "communication_services"
    assert config.exposures["energy"]["inflation_pressure"] == 0.7
    assert config.regime_priors["reflation"]["materials"] == 0.35


def test_sector_config_rejects_duplicate_sector(tmp_path: Path):
    sector_path, exposure_path, prior_path = _write_sector_configs(tmp_path)
    data = yaml.safe_load(sector_path.read_text())
    data["sectors"].append(data["sectors"][0])
    sector_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate sector_id"):
        load_sector_config(
            sector_config_path=sector_path,
            exposure_config_path=exposure_path,
            prior_config_path=prior_path,
        )


def test_sector_config_rejects_missing_exposure(tmp_path: Path):
    sector_path, exposure_path, prior_path = _write_sector_configs(tmp_path)
    data = yaml.safe_load(exposure_path.read_text())
    del data["sector_exposures"]["energy"]
    exposure_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="missing sector exposure"):
        load_sector_config(
            sector_config_path=sector_path,
            exposure_config_path=exposure_path,
            prior_config_path=prior_path,
        )


def test_sector_config_rejects_unknown_dimension(tmp_path: Path):
    sector_path, exposure_path, prior_path = _write_sector_configs(tmp_path)
    data = yaml.safe_load(exposure_path.read_text())
    data["sector_exposures"]["energy"]["not_a_dimension"] = 1.0
    exposure_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown dimension_id"):
        load_sector_config(
            sector_config_path=sector_path,
            exposure_config_path=exposure_path,
            prior_config_path=prior_path,
        )


def test_sector_config_rejects_unknown_regime(tmp_path: Path):
    sector_path, exposure_path, prior_path = _write_sector_configs(tmp_path)
    data = yaml.safe_load(prior_path.read_text())
    data["sector_regime_priors"]["not_a_regime"] = {"energy": 0.1}
    prior_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown regime_id"):
        load_sector_config(
            sector_config_path=sector_path,
            exposure_config_path=exposure_path,
            prior_config_path=prior_path,
        )


def test_sector_scoring_is_deterministic_and_stores_components():
    result = build_sector_scores(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        dimension_scores=_dimension_scores(),
        timeline=_timeline(confidence=0.25),
        config=_toy_sector_config(),
    )

    energy = result.sector_scores[result.sector_scores["sector_id"] == "energy"].iloc[0]
    assert bool(energy["valid"]) is True
    assert energy["raw_sector_score"] == pytest.approx(1.32)
    assert energy["confidence_adjusted_score"] == pytest.approx(0.726)
    assert energy["rank"] == 1
    assert len(result.components[result.components["sector_id"] == "energy"]) == 4
    assert set(result.components["component_type"]) == {"regime_prior", "dimension_exposure"}


def test_low_macro_confidence_reduces_adjusted_sector_score():
    high = build_sector_scores(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        dimension_scores=_dimension_scores(),
        timeline=_timeline(confidence=0.80),
        config=_toy_sector_config(),
    )
    low = build_sector_scores(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        dimension_scores=_dimension_scores(),
        timeline=_timeline(confidence=0.00),
        config=_toy_sector_config(),
    )

    high_energy = high.sector_scores[high.sector_scores["sector_id"] == "energy"].iloc[0]
    low_energy = low.sector_scores[low.sector_scores["sector_id"] == "energy"].iloc[0]
    assert low_energy["confidence_adjusted_score"] < high_energy["confidence_adjusted_score"]
    assert low_energy["confidence_adjusted_score"] == pytest.approx(0.528)


def test_sector_health_captures_missing_macro_inputs():
    dimensions = _dimension_scores()
    dimensions = dimensions[dimensions["dimension_id"] != "inflation_pressure"]
    result = build_sector_scores(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        dimension_scores=dimensions,
        timeline=_timeline(),
        config=_toy_sector_config(),
    )

    energy = result.sector_health[result.sector_health["sector_id"] == "energy"].iloc[0]
    assert bool(energy["valid"]) is False
    assert "dimension_exposure:inflation_pressure" in energy["missing_components"]


def test_sector_cli_commands_and_reports_work(tmp_path: Path):
    macro_config = _write_macro_config(tmp_path)
    db_path = tmp_path / "macro.duckdb"
    parquet_dir = tmp_path / "parquet"
    store = _seed_store(db_path)

    result = runner.invoke(
        app,
        [
            "build-sector-scores",
            "--config",
            str(macro_config),
            "--db-path",
            str(db_path),
            "--parquet-dir",
            str(parquet_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sector_score_rows" in result.output

    ranking = runner.invoke(app, ["current-sector-ranking", "--db-path", str(db_path)])
    assert ranking.exit_code == 0, ranking.output
    ranking_payload = json.loads(ranking.output)
    assert ranking_payload["valid"] is True
    assert ranking_payload["ranking"][0]["rank"] == 1

    inspect = runner.invoke(app, ["inspect-sector", "energy", "--db-path", str(db_path)])
    assert inspect.exit_code == 0, inspect.output
    assert "energy latest components" in inspect.output

    health = runner.invoke(app, ["sector-health", "--db-path", str(db_path)])
    assert health.exit_code == 0, health.output
    assert "Sector Health" in health.output

    report = runner.invoke(
        app,
        [
            "write-sector-report",
            "--config",
            str(macro_config),
            "--db-path",
            str(db_path),
        ],
    )
    assert report.exit_code == 0, report.output
    output_dir = tmp_path / "outputs"
    payload = json.loads((output_dir / "current_sector_ranking.json").read_text())
    markdown = (output_dir / "current_sector_ranking.md").read_text()
    assert payload["valid"] is True
    assert "not investment advice" in markdown
    assert "Sector Ranking" in markdown

    # Keep the seeded store referenced so the temp DB is not garbage-collected early on Windows.
    assert store.read_table("sector_scores").shape[0] > 0


def test_sector_report_markdown_is_deterministic_and_diagnostic_only():
    result = build_sector_scores(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        dimension_scores=_dimension_scores(),
        timeline=_timeline(confidence=0.01),
        config=_toy_sector_config(),
    )
    payload = build_current_sector_report(
        sector_scores=result.sector_scores,
        components=result.components,
        health=result.sector_health,
        config=_toy_sector_config(),
    )

    markdown = current_sector_report_markdown(payload)
    assert markdown == current_sector_report_markdown(payload)
    assert "weak diagnostic signal" in markdown
    forbidden = ["Buy ", "Sell ", "Overweight", "Underweight", "Avoid "]
    assert not any(term in markdown for term in forbidden)


def _write_sector_configs(tmp_path: Path) -> tuple[Path, Path, Path]:
    sector_path = tmp_path / "sectors.yaml"
    exposure_path = tmp_path / "sector_exposures.yaml"
    prior_path = tmp_path / "sector_regime_priors.yaml"
    sector_path.write_text(
        yaml.safe_dump(
            {
                "sectors": [
                    {
                        "sector_id": "energy",
                        "label": "Energy",
                        "proxy_ticker": "XLE",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    exposure_path.write_text(
        yaml.safe_dump(
            {
                "sector_exposures": {
                    "energy": {
                        "growth_momentum": 0.3,
                        "inflation_pressure": 0.7,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    prior_path.write_text(
        yaml.safe_dump(
            {
                "sector_regime_priors": {
                    regime: {"energy": 0.0}
                    for regime in [
                        "goldilocks",
                        "reflation",
                        "stagflation",
                        "recession",
                        "tightening",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return sector_path, exposure_path, prior_path


def _write_macro_config(tmp_path: Path) -> Path:
    data = yaml.safe_load(Path("config/phase_b_sources.yaml").read_text())
    data["reports"]["output_dir"] = str(tmp_path / "outputs")
    path = tmp_path / "phase_b_sources.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _seed_store(db_path: Path) -> DuckDBStore:
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_regime_outputs(_regime_contributions(), _full_regime_scores(), _full_regime_health())
    store.replace_dimension_outputs(
        _dimension_contributions(),
        _full_dimension_scores(),
        pd.DataFrame(),
    )
    store.replace_diagnostic_outputs(_full_timeline(), pd.DataFrame(), pd.DataFrame())
    return store


def _full_regime_scores() -> pd.DataFrame:
    rows = []
    probabilities = {
        "goldilocks": 0.1,
        "reflation": 0.4,
        "stagflation": 0.2,
        "recession": 0.1,
        "tightening": 0.2,
    }
    for rank, (regime_id, probability) in enumerate(probabilities.items(), start=1):
        rows.append(
            {
                "regime_id": regime_id,
                "date": "2026-01-01",
                "raw_score": probability,
                "probability": probability,
                "rank": rank,
                "valid_dimension_count": 5,
                "configured_dimension_count": 5,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            }
        )
    return pd.DataFrame(rows)


def _full_regime_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "valid": True,
                "dominant_regime": "reflation",
                "dominant_probability": 0.4,
                "confidence": 0.2,
                "entropy": 1.4,
                "valid_regime_count": 5,
                "reason": "ok",
            }
        ]
    )


def _regime_contributions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "regime_id",
            "dimension_id",
            "date",
            "dimension_score",
            "weight",
            "normalized_weight",
            "polarity",
            "transformed_dimension_value",
            "contribution",
            "valid",
            "reason",
        ]
    )


def _full_dimension_scores() -> pd.DataFrame:
    scores = {
        "growth_momentum": 0.5,
        "inflation_pressure": 0.6,
        "policy_stance": -0.2,
        "credit_liquidity": 0.3,
        "yield_curve": 0.1,
    }
    return pd.DataFrame(
        [
            {
                "dimension_id": dimension_id,
                "date": "2026-01-01",
                "score": score,
                "valid_feature_count": 2,
                "configured_feature_count": 2,
                "total_configured_weight": 1.0,
                "used_weight": 1.0,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            }
            for dimension_id, score in scores.items()
        ]
    )


def _dimension_contributions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "dimension_id",
            "feature_id",
            "date",
            "normalized_value",
            "weight",
            "normalized_weight",
            "polarity",
            "signed_value",
            "contribution",
            "valid",
            "reason",
        ]
    )


def _full_timeline() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "dominant_regime": "reflation",
                "dominant_probability": 0.4,
                "reported_regime": "reflation",
                "reported_regime_probability": 0.4,
                "reported_confidence": 0.2,
                "raw_dominant_regime": "reflation",
                "raw_dominant_probability": 0.4,
                "raw_confidence": 0.2,
                "second_regime": "stagflation",
                "second_probability": 0.2,
                "confidence": 0.2,
                "entropy": 1.4,
                "valid_regime_count": 5,
                "valid": True,
                "transition_filter_applied": True,
                "transition_filter_reason": "raw_signal_confirmed",
                "reason": "revised_data_diagnostic",
            }
        ]
    )
