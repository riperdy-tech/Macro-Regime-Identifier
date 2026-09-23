"""S6.3: FITTED sector exposures, published in shadow.

Covers the binding conditions of MRI_PROBES_APPROVAL.md S6.3-a..e:
- S6.3-a: vintage-built history, proven by a truncation test.
- S6.3-b: the 11-sector pin (a subset cross-section is rejected).
- The shadow artifact never alters current_sector_ranking.json's live `validation` block.
- A planted-signal fixture is recovered; pure noise is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner
import yaml

from macro_engine.cli import app
from macro_engine.sectors.fit import (
    CORE_DIMENSION_IDS,
    GICS_11_SECTOR_IDS,
    build_annual_vintages,
    build_sector_fit_payload,
    compute_annual_oos_acceptance,
    compute_sensitivity_rows,
    evaluate_promotion,
    prepare_cross_section_matrices,
    score_series_from_vintages,
)
from macro_engine.sectors.config import SectorConfig, SectorDefinition
from macro_engine.storage.duckdb_store import DuckDBStore

runner = CliRunner()


# ── Synthetic fixtures ──────────────────────────────────────────────────────────────────


def _synthetic_dates(n_months: int, start: str = "2000-01-01") -> list[pd.Timestamp]:
    return [pd.Timestamp(start) + pd.DateOffset(months=i) for i in range(n_months)]


def _planted_signal_matrices(
    n_months: int = 220,
    n_sectors: int = 11,
    seed: int = 123,
) -> tuple[list[pd.Timestamp], list[str], np.ndarray, np.ndarray, np.ndarray]:
    """AR(1) macro factors with a planted, distinct per-sector loading -- the harness should
    recover a strong positive rank IC out of sample (mirrors
    tests/test_probe_sector_ceiling.py::test_planted_signal_recovered, at the annual cadence)."""
    rng = np.random.default_rng(seed)
    n_features = len(CORE_DIMENSION_IDS)
    dates = _synthetic_dates(n_months)
    sectors = list(GICS_11_SECTOR_IDS[:n_sectors])

    X = np.zeros((n_months, n_features))
    for t in range(1, n_months):
        X[t] = 0.7 * X[t - 1] + rng.normal(scale=0.7, size=n_features)

    B = rng.uniform(-1.0, 1.0, size=(n_sectors, n_features))
    alphas = rng.uniform(-0.02, 0.02, size=n_sectors)

    returns_mat = np.zeros((n_months, n_sectors))
    for s in range(n_sectors):
        returns_mat[:, s] = alphas[s] + X @ B[s] + rng.normal(scale=0.05, size=n_months)

    baseline_mat = rng.normal(scale=0.5, size=(n_months, n_sectors))  # noise hand-set baseline
    return dates, sectors, returns_mat, X, baseline_mat


def _noise_matrices(
    n_months: int = 220,
    n_sectors: int = 11,
    seed: int = 999,
) -> tuple[list[pd.Timestamp], list[str], np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n_features = len(CORE_DIMENSION_IDS)
    dates = _synthetic_dates(n_months)
    sectors = list(GICS_11_SECTOR_IDS[:n_sectors])
    X = rng.normal(size=(n_months, n_features))
    returns_mat = rng.normal(size=(n_months, n_sectors))
    baseline_mat = rng.normal(size=(n_months, n_sectors))
    return dates, sectors, returns_mat, X, baseline_mat


def _synthetic_frames(
    n_months: int = 220, seed: int = 123
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The same planted-signal fixture, shaped as dimension_scores / sector_validation_returns
    DataFrames -- what `prepare_cross_section_matrices` and `build_sector_fit_payload` actually
    consume."""
    dates, sectors, returns_mat, features_mat, baseline_mat = _planted_signal_matrices(
        n_months=n_months, seed=seed
    )
    dimension_rows = [
        {"dimension_id": dim_id, "date": date, "score": features_mat[t, j], "valid": True}
        for t, date in enumerate(dates)
        for j, dim_id in enumerate(CORE_DIMENSION_IDS)
    ]
    dimension_scores = pd.DataFrame(dimension_rows)

    validation_rows = []
    for t, date in enumerate(dates):
        for s, sector_id in enumerate(sectors):
            validation_rows.append(
                {
                    "sector_id": sector_id,
                    "score_date": date,
                    "confidence_adjusted_score": baseline_mat[t, s],
                    "relative_forward_1m_return": returns_mat[t, s] * 0.4,
                    "relative_forward_3m_return": returns_mat[t, s],
                    "valid": True,
                }
            )
    validation_returns = pd.DataFrame(validation_rows)
    return dimension_scores, validation_returns


def _toy_sector_config() -> SectorConfig:
    return SectorConfig(
        sectors=[
            SectorDefinition(sector_id=sector_id, label=sector_id.replace("_", " ").title())
            for sector_id in GICS_11_SECTOR_IDS
        ],
        exposures={sector_id: {"growth_momentum": 0.1} for sector_id in GICS_11_SECTOR_IDS},
        regime_priors={},
    )


# ── S6.3-b: the 11-sector pin ────────────────────────────────────────────────────────────


def test_gate_cross_section_rejects_a_subset():
    dimension_scores, validation_returns = _synthetic_frames(n_months=90)
    subset = GICS_11_SECTOR_IDS[:-1]
    with pytest.raises(ValueError, match="pinned to the 11 published GICS sectors"):
        prepare_cross_section_matrices(
            validation_returns,
            dimension_scores,
            dimension_ids=CORE_DIMENSION_IDS,
            horizon_months=3,
            sector_ids=subset,
        )


def test_gate_cross_section_accepts_the_full_11_by_default():
    dimension_scores, validation_returns = _synthetic_frames(n_months=90)
    _dates, sectors, returns_mat, _features, _baseline = prepare_cross_section_matrices(
        validation_returns,
        dimension_scores,
        dimension_ids=CORE_DIMENSION_IDS,
        horizon_months=3,
    )
    assert sectors == list(GICS_11_SECTOR_IDS)
    assert returns_mat.shape[1] == 11


def test_leave_one_sector_out_requires_explicit_opt_in():
    dimension_scores, validation_returns = _synthetic_frames(n_months=90)
    restricted = [s for s in GICS_11_SECTOR_IDS if s != "energy"]
    # Same call, but the sensitivity-only escape hatch: allowed, and clearly separate from
    # the gate path above (never used by build_sector_fit_payload's own promotion decision).
    _dates, sectors, _returns, _features, _baseline = prepare_cross_section_matrices(
        validation_returns,
        dimension_scores,
        dimension_ids=CORE_DIMENSION_IDS,
        horizon_months=3,
        sector_ids=restricted,
        allow_subset=True,
    )
    assert sectors == restricted
    assert len(sectors) == 10


# ── S6.3-a: vintage-built history is truncation-safe ────────────────────────────────────


def test_truncation_reproduces_every_score_at_or_before_the_cut():
    dates, sectors, returns_mat, features_mat, _baseline = _planted_signal_matrices(n_months=220)

    vintages_full = build_annual_vintages(
        dates, sectors, CORE_DIMENSION_IDS, returns_mat, features_mat, horizon_months=3
    )
    scores_full, _active_full, _reasons_full = score_series_from_vintages(
        dates, sectors, CORE_DIMENSION_IDS, features_mat, vintages_full
    )
    assert len(vintages_full) >= 2, "fixture must produce at least two annual vintages to be a real test"

    for cut_idx in (100, 140, 180, 219):
        dates_trunc = dates[: cut_idx + 1]
        returns_trunc = returns_mat[: cut_idx + 1]
        features_trunc = features_mat[: cut_idx + 1]

        vintages_trunc = build_annual_vintages(
            dates_trunc,
            sectors,
            CORE_DIMENSION_IDS,
            returns_trunc,
            features_trunc,
            horizon_months=3,
        )
        scores_trunc, _active_trunc, _reasons_trunc = score_series_from_vintages(
            dates_trunc, sectors, CORE_DIMENSION_IDS, features_trunc, vintages_trunc
        )

        np.testing.assert_array_equal(
            scores_trunc,
            scores_full[: cut_idx + 1],
            err_msg=f"truncating at index {cut_idx} changed a score at or before the cut",
        )
        # And the vintages themselves that exist in the truncated run are identical (same
        # parameters) to the corresponding vintage in the full run -- not just the scores
        # they produced.
        for v_trunc in vintages_trunc:
            match = next(v for v in vintages_full if v.vintage_date == v_trunc.vintage_date)
            assert v_trunc.alphas == match.alphas
            assert v_trunc.betas == match.betas


def test_truncation_at_payload_level_does_not_error(tmp_path: Path):
    """The same invariant, exercised through the DataFrame-shaped public entry point."""
    dimension_scores, validation_returns = _synthetic_frames(n_months=220)
    config = _toy_sector_config()

    full_payload = build_sector_fit_payload(
        dimension_scores=dimension_scores,
        validation_returns=validation_returns,
        sector_config=config,
    )
    cut_date = pd.Timestamp("2015-01-01")
    truncated_dim = dimension_scores[pd.to_datetime(dimension_scores["date"]) <= cut_date]
    truncated_val = validation_returns[pd.to_datetime(validation_returns["score_date"]) <= cut_date]
    truncated_payload = build_sector_fit_payload(
        dimension_scores=truncated_dim,
        validation_returns=truncated_val,
        sector_config=config,
    )
    assert full_payload["valid"] is True
    assert truncated_payload["valid"] is True
    assert truncated_payload["asof"] <= str(cut_date.date())


# ── Planted signal recovered; pure noise is not ─────────────────────────────────────────


def test_planted_signal_is_recovered_out_of_sample():
    dates, sectors, returns_mat, features_mat, baseline_mat = _planted_signal_matrices(n_months=260)
    acceptance = compute_annual_oos_acceptance(
        dates, sectors, CORE_DIMENSION_IDS, returns_mat, features_mat, baseline_mat, horizon_months=3
    )
    oos = acceptance["oos"]
    assert oos is not None
    assert oos["mean_ic"] is not None and oos["mean_ic"] > 0.30
    assert oos["t_stat"] is not None and oos["t_stat"] > 3.0
    assert oos["n_dates"] > 0


def test_pure_noise_is_not_recovered():
    dates, sectors, returns_mat, features_mat, baseline_mat = _noise_matrices(n_months=260)
    acceptance = compute_annual_oos_acceptance(
        dates, sectors, CORE_DIMENSION_IDS, returns_mat, features_mat, baseline_mat, horizon_months=3
    )
    oos = acceptance["oos"]
    assert oos is not None
    assert oos["mean_ic"] is not None
    assert abs(oos["mean_ic"]) < 0.15


# ── S6.3-d: promotion rule arithmetic ────────────────────────────────────────────────────


def _acceptance_fixture(
    *,
    mean_ic: float,
    t_stat: float,
    half_ic: tuple[float, float],
    hand_set_ic: tuple[float, float],
    non_overlap_t: tuple[float, float, float],
) -> dict:
    return {
        "oos": {
            "mean_ic": mean_ic,
            "t_stat": t_stat,
            "sd_ic": 0.4,
            "positive_share": 0.6,
            "n_dates": 200,
            "halves": {
                "first": {
                    "fitted": {"mean_ic": half_ic[0]},
                    "hand_set_baseline": {"mean_ic": hand_set_ic[0]},
                },
                "second": {
                    "fitted": {"mean_ic": half_ic[1]},
                    "hand_set_baseline": {"mean_ic": hand_set_ic[1]},
                },
            },
            "non_overlapping": {
                f"phase_{i}": {"t_stat": t} for i, t in enumerate(non_overlap_t)
            },
            "hand_set_baseline": {"mean_ic": -0.01},
        }
    }


def test_promotion_passes_when_every_condition_clears():
    acceptance = _acceptance_fixture(
        mean_ic=0.07,
        t_stat=2.1,
        half_ic=(0.09, 0.05),
        hand_set_ic=(-0.03, -0.01),
        non_overlap_t=(1.6, 1.7, 1.9),
    )
    result = evaluate_promotion(acceptance)
    assert result["passed"] is True


def test_promotion_fails_on_t_below_2():
    acceptance = _acceptance_fixture(
        mean_ic=0.07,
        t_stat=1.96,
        half_ic=(0.09, 0.05),
        hand_set_ic=(-0.03, -0.01),
        non_overlap_t=(1.6, 1.7, 1.9),
    )
    result = evaluate_promotion(acceptance)
    assert result["passed"] is False
    assert result["checks"]["oos_t_above_min"] is False


def test_promotion_fails_when_a_half_is_below_hand_set():
    acceptance = _acceptance_fixture(
        mean_ic=0.07,
        t_stat=2.5,
        half_ic=(0.09, -0.02),  # second half fitted IC below hand-set and negative
        hand_set_ic=(-0.03, -0.01),
        non_overlap_t=(1.6, 1.7, 1.9),
    )
    result = evaluate_promotion(acceptance)
    assert result["passed"] is False
    assert result["checks"]["halves_pass"] is False


def test_promotion_fails_when_a_non_overlapping_phase_is_below_1_5():
    acceptance = _acceptance_fixture(
        mean_ic=0.07,
        t_stat=2.5,
        half_ic=(0.09, 0.05),
        hand_set_ic=(-0.03, -0.01),
        non_overlap_t=(1.6, 1.35, 1.9),
    )
    result = evaluate_promotion(acceptance)
    assert result["passed"] is False
    assert result["checks"]["non_overlapping_pass"] is False


def test_promotion_fails_closed_with_no_legitimate_vintage():
    result = evaluate_promotion({"oos": None, "reason": "no_legitimate_vintage"})
    assert result["passed"] is False


# ── S6.3-e: sensitivity rows are printed, never gate ────────────────────────────────────


def test_sensitivity_rows_are_present_and_do_not_affect_the_gate():
    dimension_scores, validation_returns = _synthetic_frames(n_months=220)
    sensitivity = compute_sensitivity_rows(validation_returns, dimension_scores, horizon_months=3)
    assert set(sensitivity) == {
        "no_nfci",
        "nw_lag_3",
        "features_one_month_older",
        "leave_one_sector_out",
    }
    assert "credit_liquidity" not in sensitivity["no_nfci"]["dimensions"]
    assert len(sensitivity["leave_one_sector_out"]["t_stat_by_dropped_sector"]) == 11


# ── The shadow artifact never touches the live validation block ─────────────────────────


def _write_macro_config(tmp_path: Path) -> Path:
    data = yaml.safe_load(Path("config/phase_b_sources.yaml").read_text())
    data["reports"]["output_dir"] = str(tmp_path / "outputs")
    path = tmp_path / "phase_b_sources.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _seed_minimal_store(db_path: Path) -> DuckDBStore:
    """Just enough for a real (if `validation_missing`) current_sector_ranking.json:
    one dimension-score date, one regime date, one timeline row. No sector_validation_returns
    is seeded, so write_sector_fit_report's own input is empty and it returns
    `valid: False, reason: "no_input_data"` -- proving the "never touches the live block"
    property does not depend on the shadow build having real data to chew on."""
    store = DuckDBStore(db_path)
    store.initialize()
    regime_scores = pd.DataFrame(
        [
            {
                "regime_id": regime_id,
                "date": "2026-01-01",
                "raw_score": p,
                "probability": p,
                "rank": rank,
                "valid_dimension_count": 5,
                "configured_dimension_count": 5,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            }
            for rank, (regime_id, p) in enumerate(
                {
                    "goldilocks": 0.1,
                    "reflation": 0.4,
                    "stagflation": 0.2,
                    "recession": 0.1,
                    "tightening": 0.2,
                }.items(),
                start=1,
            )
        ]
    )
    regime_health = pd.DataFrame(
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
    contributions = pd.DataFrame(
        columns=[
            "regime_id", "dimension_id", "date", "dimension_score", "weight",
            "normalized_weight", "polarity", "transformed_dimension_value", "contribution",
            "valid", "reason",
        ]
    )
    store.replace_regime_outputs(contributions, regime_scores, regime_health)

    dimension_scores = pd.DataFrame(
        [
            {
                "dimension_id": dim_id,
                "date": "2026-01-01",
                "score": 0.2,
                "valid_feature_count": 1,
                "configured_feature_count": 1,
                "total_configured_weight": 1.0,
                "used_weight": 1.0,
                "coverage_ratio": 1.0,
                "valid": True,
                "reason": "ok",
            }
            for dim_id in CORE_DIMENSION_IDS
        ]
    )
    store.replace_dimension_outputs(pd.DataFrame(), dimension_scores, pd.DataFrame())

    timeline = pd.DataFrame(
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
                "coverage": 0.8,
                "peakedness": 0.25,
                "entropy": 1.4,
                "valid_regime_count": 5,
                "valid": True,
                "transition_filter_applied": True,
                "transition_filter_reason": "raw_signal_confirmed",
                "reason": "revised_data_diagnostic",
            }
        ]
    )
    store.replace_diagnostic_outputs(timeline, pd.DataFrame(), pd.DataFrame())
    return store


def test_shadow_report_never_changes_the_live_validation_block(tmp_path: Path):
    macro_config = _write_macro_config(tmp_path)
    db_path = tmp_path / "macro.duckdb"
    parquet_dir = tmp_path / "parquet"
    _seed_minimal_store(db_path)

    build_result = runner.invoke(
        app,
        [
            "build-sector-scores",
            "--config", str(macro_config),
            "--db-path", str(db_path),
            "--parquet-dir", str(parquet_dir),
        ],
    )
    assert build_result.exit_code == 0, build_result.output

    output_dir = tmp_path / "outputs"

    report_before = runner.invoke(
        app, ["write-sector-report", "--config", str(macro_config), "--db-path", str(db_path)]
    )
    assert report_before.exit_code == 0, report_before.output
    payload_before = json.loads((output_dir / "current_sector_ranking.json").read_text())

    fit_report = runner.invoke(
        app, ["write-sector-fit-report", "--config", str(macro_config), "--db-path", str(db_path)]
    )
    assert fit_report.exit_code == 0, fit_report.output
    shadow_path = output_dir / "sector_exposures_fitted.json"
    assert shadow_path.exists()
    shadow_payload = json.loads(shadow_path.read_text())
    assert shadow_payload["mode"] == "shadow"
    assert shadow_payload["affects_quotas"] is False

    report_after = runner.invoke(
        app, ["write-sector-report", "--config", str(macro_config), "--db-path", str(db_path)]
    )
    assert report_after.exit_code == 0, report_after.output
    payload_after = json.loads((output_dir / "current_sector_ranking.json").read_text())

    # The byte-equality assertion: the live `validation` block (what the screener's gate
    # reads, P0_0_MRI_TARGET_ARCHITECTURE.md §7.1) is identical whether or not the shadow
    # report ran in between.
    assert json.dumps(payload_before["validation"], sort_keys=True) == json.dumps(
        payload_after["validation"], sort_keys=True
    )
    # And exposure_source on every sector row stays hand_set_v1 -- the shadow never flips it.
    assert all(row["exposure_source"] == "hand_set_v1" for row in payload_before["sector_ranking"])
    assert all(row["exposure_source"] == "hand_set_v1" for row in payload_after["sector_ranking"])
    assert json.dumps(payload_before["sector_ranking"], sort_keys=True) == json.dumps(
        payload_after["sector_ranking"], sort_keys=True
    )
