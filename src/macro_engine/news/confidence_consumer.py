"""Confidence recalibration consumer (integration point, valve CLOSED).

The calibration ledger (news/confidence_calibration.py) accumulates raw LLM
confidence vs realized forward sector relative return. Eventually a learned
transform (e.g. isotonic regression) will map raw -> calibrated confidence.

This module is that future integration point, defined NOW so scoring/reporting
has a clean place to consume calibrated confidence later. But the valve is
closed: default behavior is IDENTITY.

    calibrated_confidence = raw_confidence

unless ALL of these hold:
    enabled = True
    calibration_artifact present
    artifact schema_version supported
    artifact directional_calls >= min_directional_calls
    horizon supported by the artifact

No learned transform is applied yet (no isotonic regression, no new
dependency). When a real transform lands it slots into `_apply_transform`; the
gating contract here does not change.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bump when the artifact shape or transform semantics change.
SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_MIN_DIRECTIONAL_CALLS = 200


@dataclass(frozen=True)
class CalibrationOutcome:
    confidence_raw: float
    confidence_calibrated: float
    confidence_calibration_applied: bool
    confidence_calibration_reason: str


def apply_confidence_calibration(
    confidence: float,
    *,
    sector_id: str | None = None,
    horizon: str | None = None,
    calibration_artifact: dict | None = None,
    enabled: bool = False,
    min_directional_calls: int = DEFAULT_MIN_DIRECTIONAL_CALLS,
) -> float:
    """Return calibrated confidence, or the raw value unchanged when the valve
    is closed or any precondition fails. Never raises on a missing/stale/under-
    sampled artifact - it falls back to identity. Output clamped to [0, 1]."""
    return calibrate_confidence(
        confidence,
        sector_id=sector_id,
        horizon=horizon,
        calibration_artifact=calibration_artifact,
        enabled=enabled,
        min_directional_calls=min_directional_calls,
    ).confidence_calibrated


def calibrate_confidence(
    confidence: float,
    *,
    sector_id: str | None = None,
    horizon: str | None = None,
    calibration_artifact: dict | None = None,
    enabled: bool = False,
    min_directional_calls: int = DEFAULT_MIN_DIRECTIONAL_CALLS,
) -> CalibrationOutcome:
    """Full outcome variant: raw, calibrated, applied flag, and reason. Use this
    when emitting the optional report fields; `apply_confidence_calibration`
    wraps it for call sites that only want the number."""
    raw = _clamp01(confidence)

    if not enabled:
        return _identity(raw, "disabled")
    if not isinstance(calibration_artifact, dict):
        return _identity(raw, "artifact_missing")
    if calibration_artifact.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        return _identity(raw, "schema_version_unsupported")
    directional_calls = calibration_artifact.get("directional_calls", 0)
    if not isinstance(directional_calls, int) or directional_calls < min_directional_calls:
        return _identity(raw, "insufficient_sample")
    if horizon is None or horizon not in _supported_horizons(calibration_artifact):
        return _identity(raw, "horizon_unsupported")

    # Valve OPEN path. No learned transform exists yet, so this remains identity
    # until `_apply_transform` is implemented. Kept separate so the gating
    # contract above is stable when the real transform lands.
    calibrated = _clamp01(
        _apply_transform(
            raw, sector_id=sector_id, horizon=horizon, artifact=calibration_artifact
        )
    )
    return CalibrationOutcome(
        confidence_raw=raw,
        confidence_calibrated=calibrated,
        confidence_calibration_applied=True,
        confidence_calibration_reason="applied",
    )


def _apply_transform(
    confidence: float,
    *,
    sector_id: str | None,
    horizon: str | None,
    artifact: dict,
) -> float:
    """Placeholder learned transform. Identity until a fitted model (e.g.
    isotonic regression) is wired in here. Intentionally no dependency added."""
    return confidence


def _supported_horizons(artifact: dict) -> set[str]:
    horizons = artifact.get("horizons")
    if isinstance(horizons, (list, tuple, set)):
        return {str(h) for h in horizons}
    return set()


def _identity(raw: float, reason: str) -> CalibrationOutcome:
    return CalibrationOutcome(
        confidence_raw=raw,
        confidence_calibrated=raw,
        confidence_calibration_applied=False,
        confidence_calibration_reason=reason,
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
