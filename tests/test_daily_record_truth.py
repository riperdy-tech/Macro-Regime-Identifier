"""Section 3 of the 2026-11-12 MRI audit: the daily record must tell the truth.

The macro step ended `success_with_warnings` with 41 to 46 warnings while the daily record said
`warning_count: 0`, and a failed optional step was only visible in the step statuses.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from macro_engine.daily import run_daily_diagnostic

from test_phase_v05_m1_m2_operations import _daily_config, _daily_services

MACRO_WARNINGS = (
    "invalid_feature:dollar_yoy_z:missing_source_data",
    "invalid_dimension:inflation_pressure:below_min_valid_features",
    "invalid_regime:goldilocks:below_min_valid_dimensions",
    "vintage_partial:CPIAUCSL",
)


def _run(tmp_path: Path, services: dict):
    return run_daily_diagnostic(
        config_path=_daily_config(tmp_path),
        db_path=tmp_path / "macro.duckdb",
        run_date="2026-11-11",
        services=services,
        output_dir=tmp_path / "outputs",
    )


def test_the_macro_steps_warnings_reach_the_daily_record(tmp_path: Path):
    services = _daily_services(tmp_path)
    services["run_pipeline"] = lambda **_: SimpleNamespace(
        status="success_with_warnings", warnings=MACRO_WARNINGS
    )
    result = _run(tmp_path, services)

    assert result.status == "success_with_warnings"
    for warning in MACRO_WARNINGS:
        assert warning in result.warnings
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["warning_count"] == len(result.warnings) >= len(MACRO_WARNINGS)
    assert summary["warnings"] == result.warnings


def test_a_clean_macro_step_still_reports_zero_warnings(tmp_path: Path):
    result = _run(tmp_path, _daily_services(tmp_path))
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert result.status == "success"
    assert summary["warning_count"] == 0
    assert summary["non_fatal_failures"] == []


def test_a_failed_optional_step_is_listed_as_a_non_fatal_failure(tmp_path: Path):
    services = _daily_services(tmp_path)

    def _broken(**_):
        raise RuntimeError("shock register could not be built")

    services["build_and_write_shock_register"] = _broken
    result = _run(tmp_path, services)

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert result.status == "success_with_warnings"
    assert summary["non_fatal_failures"] == ["shocks"]
    assert any("shocks: shock register could not be built" in error for error in summary["errors"])
