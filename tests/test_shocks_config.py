"""Tests for config/shocks.yaml taxonomy and orchestrator decisions."""
from __future__ import annotations

from pathlib import Path

import yaml

from macro_engine.shocks.config import (
    compute_taxonomy_version,
    load_shocks_config,
    to_measurement_thresholds,
)

_CONFIG_PATH = Path("config/shocks.yaml")
# S4.4: bumped when the narrative_themes and risk_flags blocks (the §3.5 theme map and
# the T3 impact-study promotion) were added to config/shocks.yaml -- taxonomy_version is
# a hash of the file's bytes, so any edit to it, including this one, is expected to move
# this constant.
EXPECTED_TAXONOMY_VERSION = "560fc4accd6be2d6dfa43c23ee513f2e32b71b39028eaaa3ec77790d1df9465d"


def test_shocks_config_file_loads() -> None:
    assert _CONFIG_PATH.exists(), f"Missing {_CONFIG_PATH}"
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    assert "shocks" in data
    assert len(data["shocks"]) == 7


def test_taxonomy_version_is_stable() -> None:
    version1 = compute_taxonomy_version(_CONFIG_PATH)
    version2 = compute_taxonomy_version(_CONFIG_PATH)
    assert version1 == version2
    assert len(version1) == 64
    assert int(version1, 16) > 0
    assert version1 == EXPECTED_TAXONOMY_VERSION

    # Test load_shocks_config attaches taxonomy_version
    cfg = load_shocks_config(_CONFIG_PATH)
    assert cfg["taxonomy_version"] == version1


def test_all_seven_shocks_have_metadata_and_threshold_attributes() -> None:
    cfg = load_shocks_config(_CONFIG_PATH)
    shocks = cfg["shocks"]
    expected_shocks = {
        "volatility_shock",
        "credit_shock",
        "rates_shock",
        "oil_shock",
        "dollar_shock",
        "labour_shock",
        "inflation_shock",
    }
    assert set(shocks.keys()) == expected_shocks

    for shock_id, scfg in shocks.items():
        assert "name" in scfg
        assert "series_id" in scfg
        assert "transform" in scfg
        assert "measure" in scfg
        assert "unit" in scfg
        assert "signed" in scfg
        assert scfg["R"] == 10
        assert "retire_level" in scfg
        assert "sunset_days" in scfg
        assert "sample_window" in scfg
        assert "thresholds" in scfg

        thresh_block = scfg["thresholds"]
        if shock_id == "rates_shock":
            for leg in ["dfii10", "dgs2"]:
                assert leg in thresh_block
                for side in ["up", "down"]:
                    assert side in thresh_block[leg]
                    for sev in ["severity_1", "severity_2"]:
                        item = thresh_block[leg][side][sev]
                        assert isinstance(item["threshold"], (int, float))
                        assert isinstance(item["percentile"], (int, float))
                        assert isinstance(item["sample_window"], str)
                        assert isinstance(item["episodes"], int)
                        assert item["episodes"] >= 1
        elif scfg["signed"]:
            for side in ["up", "down"]:
                assert side in thresh_block
                for sev in ["severity_1", "severity_2"]:
                    item = thresh_block[side][sev]
                    assert isinstance(item["threshold"], (int, float))
                    assert isinstance(item["percentile"], (int, float))
                    assert isinstance(item["sample_window"], str)
                    assert isinstance(item["episodes"], int)
                    assert item["episodes"] >= 1
        else:
            assert "up" in thresh_block
            for sev in ["severity_1", "severity_2"]:
                item = thresh_block["up"][sev]
                assert isinstance(item["threshold"], (int, float))
                assert isinstance(item["percentile"], (int, float))
                assert isinstance(item["sample_window"], str)
                assert isinstance(item["episodes"], int)
                assert item["episodes"] >= 1


def test_orchestrator_decision_thresholds_pinned() -> None:
    """Verify values EXACTLY match the plan's 'Decisions from the S4.3 measurement' table."""
    cfg = load_shocks_config(_CONFIG_PATH)
    shocks = cfg["shocks"]

    # 1. Volatility: retire 25, sunset 120
    assert shocks["volatility_shock"]["retire_level"] == 25.0
    assert shocks["volatility_shock"]["sunset_days"] == 120
    assert shocks["volatility_shock"]["thresholds"]["up"]["severity_1"]["threshold"] == 30.0
    assert shocks["volatility_shock"]["thresholds"]["up"]["severity_2"]["threshold"] == 40.0

    # 2. Credit: retire 0.0, sunset 180
    assert shocks["credit_shock"]["retire_level"] == 0.0
    assert shocks["credit_shock"]["sunset_days"] == 180
    assert shocks["credit_shock"]["thresholds"]["up"]["severity_1"]["threshold"] == 50.0
    assert shocks["credit_shock"]["thresholds"]["up"]["severity_2"]["threshold"] == 75.0

    # 3. Rates DFII10 down: changed to -60 / -70
    dfii10 = shocks["rates_shock"]["thresholds"]["dfii10"]
    assert dfii10["down"]["severity_1"]["threshold"] == -60.0
    assert dfii10["down"]["severity_1"]["percentile"] == 4.3
    assert dfii10["down"]["severity_2"]["threshold"] == -70.0
    assert dfii10["down"]["severity_2"]["percentile"] == 2.1
    assert shocks["rates_shock"]["retire_level"] == 25.0
    assert shocks["rates_shock"]["sunset_days"] == 240

    # 4. Oil: retire 10.0, sunset 180
    assert shocks["oil_shock"]["retire_level"] == 10.0
    assert shocks["oil_shock"]["sunset_days"] == 180

    # 5. Dollar: retire 2.5, sunset 120
    assert shocks["dollar_shock"]["retire_level"] == 2.5
    assert shocks["dollar_shock"]["sunset_days"] == 120

    # 6. Labour: changed to +30 / +40, retire 10.0, sunset 365
    labour_up = shocks["labour_shock"]["thresholds"]["up"]
    assert labour_up["severity_1"]["threshold"] == 30.0
    assert labour_up["severity_1"]["percentile"] == 90.0
    assert labour_up["severity_1"]["episodes"] == 5
    assert labour_up["severity_2"]["threshold"] == 40.0
    assert labour_up["severity_2"]["percentile"] == 92.7
    assert labour_up["severity_2"]["episodes"] == 5
    assert shocks["labour_shock"]["retire_level"] == 10.0
    assert shocks["labour_shock"]["sunset_days"] == 365

    # 7. Inflation: up +45 / +55, down -45 / -60, retire 20.0, sunset 120
    inf = shocks["inflation_shock"]["thresholds"]
    assert inf["up"]["severity_1"]["threshold"] == 45.0
    assert inf["up"]["severity_1"]["percentile"] == 95.7
    assert inf["up"]["severity_2"]["threshold"] == 55.0
    assert inf["up"]["severity_2"]["percentile"] == 97.9
    assert inf["down"]["severity_1"]["threshold"] == -45.0
    assert inf["down"]["severity_1"]["percentile"] == 4.6
    assert inf["down"]["severity_2"]["threshold"] == -60.0
    assert inf["down"]["severity_2"]["percentile"] == 2.1
    assert shocks["inflation_shock"]["retire_level"] == 20.0
    assert shocks["inflation_shock"]["sunset_days"] == 120


def test_to_measurement_thresholds_conversion() -> None:
    cfg = load_shocks_config(_CONFIG_PATH)
    m_cfg = to_measurement_thresholds(cfg)
    assert len(m_cfg) == 7
    assert m_cfg["labour_shock"]["severity_1"] == 30.0
    assert m_cfg["labour_shock"]["severity_2"] == 40.0
    assert m_cfg["volatility_shock"]["retire_level"] == 25.0
    assert m_cfg["rates_shock"]["dfii10"]["severity_1_down"] == -60.0
    assert m_cfg["rates_shock"]["dfii10"]["severity_2_down"] == -70.0
