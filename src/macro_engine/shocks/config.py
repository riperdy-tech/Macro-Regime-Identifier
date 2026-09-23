"""Shock configuration loader, validation, and taxonomy version calculation."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


def compute_taxonomy_version(config_path: str | Path = "config/shocks.yaml") -> str:
    """Compute sha256 hex digest of the shocks.yaml configuration file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    raw_bytes = path.read_bytes()
    return hashlib.sha256(raw_bytes).hexdigest()


def load_shocks_config(config_path: str | Path = "config/shocks.yaml") -> dict[str, Any]:
    """Load config/shocks.yaml and inject calculated taxonomy_version."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    version = compute_taxonomy_version(path)
    data["taxonomy_version"] = version
    return data


def to_measurement_thresholds(config: dict[str, Any]) -> dict[str, Any]:
    """Convert config/shocks.yaml dictionary to measurement script threshold dictionary format."""
    shocks = config.get("shocks", {})
    meas_cfg: dict[str, Any] = {}

    for shock_id, scfg in shocks.items():
        entry: dict[str, Any] = {
            "series_id": scfg["series_id"],
            "signed": scfg.get("signed", False),
            "R": scfg.get("R", 10),
            "retire_level": scfg["retire_level"],
            "sunset_days": scfg.get("sunset_days", 180),
        }

        if shock_id == "volatility_shock":
            entry["sample_start"] = "1990-01"
            entry["severity_1"] = scfg["thresholds"]["up"]["severity_1"]["threshold"]
            entry["severity_2"] = scfg["thresholds"]["up"]["severity_2"]["threshold"]
            entry["candidate_retires"] = [10.0, 15.0, 20.0, 25.0, 30.0]
        elif shock_id == "credit_shock":
            entry["sample_start"] = "1986-01"
            entry["severity_1"] = scfg["thresholds"]["up"]["severity_1"]["threshold"]
            entry["severity_2"] = scfg["thresholds"]["up"]["severity_2"]["threshold"]
            entry["candidate_retires"] = [-25.0, -12.5, 0.0, 12.5, 25.0]
        elif shock_id == "rates_shock":
            entry["proxy_series_id"] = scfg.get("proxy_series_id", "DGS2")
            entry["sample_start"] = "1986-01"
            entry["dfii10_start"] = "2003-01"
            entry["dfii10"] = {
                "severity_1_up": scfg["thresholds"]["dfii10"]["up"]["severity_1"]["threshold"],
                "severity_2_up": scfg["thresholds"]["dfii10"]["up"]["severity_2"]["threshold"],
                "severity_1_down": scfg["thresholds"]["dfii10"]["down"]["severity_1"]["threshold"],
                "severity_2_down": scfg["thresholds"]["dfii10"]["down"]["severity_2"]["threshold"],
            }
            entry["dgs2"] = {
                "severity_1_up": scfg["thresholds"]["dgs2"]["up"]["severity_1"]["threshold"],
                "severity_2_up": scfg["thresholds"]["dgs2"]["up"]["severity_2"]["threshold"],
                "severity_1_down": scfg["thresholds"]["dgs2"]["down"]["severity_1"]["threshold"],
                "severity_2_down": scfg["thresholds"]["dgs2"]["down"]["severity_2"]["threshold"],
            }
            entry["candidate_retires"] = [12.5, 18.75, 25.0, 31.25, 37.5]
        elif shock_id == "oil_shock":
            entry["sample_start"] = "1986-01"
            entry["severity_1_up"] = scfg["thresholds"]["up"]["severity_1"]["threshold"]
            entry["severity_2_up"] = scfg["thresholds"]["up"]["severity_2"]["threshold"]
            entry["severity_1_down"] = scfg["thresholds"]["down"]["severity_1"]["threshold"]
            entry["severity_2_down"] = scfg["thresholds"]["down"]["severity_2"]["threshold"]
            entry["candidate_retires"] = [5.0, 7.5, 10.0, 12.5, 15.0]
        elif shock_id == "dollar_shock":
            entry["sample_start"] = "1995-01"
            entry["severity_1_up"] = scfg["thresholds"]["up"]["severity_1"]["threshold"]
            entry["severity_2_up"] = scfg["thresholds"]["up"]["severity_2"]["threshold"]
            entry["severity_1_down"] = scfg["thresholds"]["down"]["severity_1"]["threshold"]
            entry["severity_2_down"] = scfg["thresholds"]["down"]["severity_2"]["threshold"]
            entry["candidate_retires"] = [1.25, 1.875, 2.5, 3.125, 3.75, 5.0, 7.5, 10.0, 12.5, 15.0]
        elif shock_id == "labour_shock":
            entry["sample_start"] = "1990-01"
            entry["severity_1"] = scfg["thresholds"]["up"]["severity_1"]["threshold"]
            entry["severity_2"] = scfg["thresholds"]["up"]["severity_2"]["threshold"]
            entry["candidate_retires"] = [5.0, 7.5, 10.0, 12.5, 15.0]
        elif shock_id == "inflation_shock":
            entry["sample_start"] = "2003-01"
            entry["severity_1_up"] = scfg["thresholds"]["up"]["severity_1"]["threshold"]
            entry["severity_2_up"] = scfg["thresholds"]["up"]["severity_2"]["threshold"]
            entry["severity_1_down"] = scfg["thresholds"]["down"]["severity_1"]["threshold"]
            entry["severity_2_down"] = scfg["thresholds"]["down"]["severity_2"]["threshold"]
            entry["candidate_retires"] = [10.0, 15.0, 20.0, 25.0, 30.0]

        meas_cfg[shock_id] = entry

    return meas_cfg
