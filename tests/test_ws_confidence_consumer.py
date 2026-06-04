"""Confidence recalibration consumer: pipe built, valve closed (identity)."""

from __future__ import annotations

from macro_engine.news.confidence_consumer import (
    SUPPORTED_SCHEMA_VERSION,
    apply_confidence_calibration,
    calibrate_confidence,
)


def _ready_artifact(**over):
    base = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "directional_calls": 500,
        "horizons": ["1m", "3m"],
    }
    base.update(over)
    return base


# ---- identity by default ---------------------------------------------------


def test_identity_when_disabled():
    out = calibrate_confidence(0.83, enabled=False, calibration_artifact=_ready_artifact())
    assert out.confidence_calibrated == 0.83
    assert out.confidence_calibration_applied is False
    assert out.confidence_calibration_reason == "disabled"


def test_identity_when_artifact_missing():
    assert apply_confidence_calibration(0.7, enabled=True, calibration_artifact=None) == 0.7


def test_identity_when_schema_version_mismatch():
    art = _ready_artifact(schema_version=SUPPORTED_SCHEMA_VERSION + 1)
    out = calibrate_confidence(0.7, enabled=True, horizon="1m", calibration_artifact=art)
    assert out.confidence_calibrated == 0.7
    assert out.confidence_calibration_reason == "schema_version_unsupported"


def test_identity_when_sample_below_threshold():
    art = _ready_artifact(directional_calls=199)
    out = calibrate_confidence(0.7, enabled=True, horizon="1m", calibration_artifact=art)
    assert out.confidence_calibrated == 0.7
    assert out.confidence_calibration_reason == "insufficient_sample"


def test_identity_when_horizon_unsupported():
    out = calibrate_confidence(
        0.7, enabled=True, horizon="6m", calibration_artifact=_ready_artifact()
    )
    assert out.confidence_calibrated == 0.7
    assert out.confidence_calibration_reason == "horizon_unsupported"


# ---- valve-open path still identity (no transform yet) ----------------------


def test_valve_open_path_is_identity_until_transform_exists():
    out = calibrate_confidence(
        0.64, enabled=True, horizon="1m", calibration_artifact=_ready_artifact()
    )
    assert out.confidence_calibration_applied is True
    assert out.confidence_calibration_reason == "applied"
    assert out.confidence_calibrated == 0.64  # _apply_transform is identity


# ---- output bounds ---------------------------------------------------------


def test_output_clamped_between_0_and_1():
    assert apply_confidence_calibration(1.5) == 1.0
    assert apply_confidence_calibration(-0.2) == 0.0


# ---- never raises on bad artifact ------------------------------------------


def test_never_raises_on_malformed_artifact():
    for bad in [{}, {"schema_version": 1}, {"directional_calls": "lots"}, 42, "x"]:
        val = apply_confidence_calibration(
            0.5, enabled=True, horizon="1m", calibration_artifact=bad  # type: ignore[arg-type]
        )
        assert val == 0.5
