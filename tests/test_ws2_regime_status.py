from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.regime_status import build_regime_status, write_regime_status


runner = CliRunner()


def test_build_regime_status_from_outputs(tmp_path: Path):
    (tmp_path / "current_regime.json").write_text(
        json.dumps(
            {
                "valid": True,
                "reported_regime": "reflation",
                "reported_regime_probability": 0.42,
                "reported_confidence": 0.12,
                "raw_dominant_regime": "tightening",
                "raw_dominant_probability": 0.39,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "news_accumulation_report.json").write_text(
        json.dumps({"readiness_label": "monitor_ready"}),
        encoding="utf-8",
    )
    (tmp_path / "secular_theme_scores.json").write_text(
        json.dumps(
            {
                "computed_at": "2026-05-27T00:00:00+00:00",
                "themes": {"ai_compute": {"score": 0.5, "item_count": 3}},
            }
        ),
        encoding="utf-8",
    )

    status = build_regime_status(outputs_dir=tmp_path)

    assert status["dominant_regime"] == "reflation"
    assert status["regime_probability"] == 0.42
    assert status["raw_dominant_regime"] == "tightening"
    assert status["monitor_ready"] is True
    assert status["status"] == "monitor_ready"
    assert status["secular_theme_scores"]["ai_compute"]["score"] == 0.5
    assert "not investment advice" in status["disclaimer"]


def test_build_regime_status_falls_back_to_daily_summary(tmp_path: Path):
    (tmp_path / "daily_diagnostic_summary.json").write_text(
        json.dumps(
            {
                "macro": {
                    "reported_regime": "goldilocks",
                    "reported_regime_probability": 0.31,
                    "reported_confidence": 0.08,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "news_accumulation_report.json").write_text(
        json.dumps({"readiness_label": "insufficient_history"}),
        encoding="utf-8",
    )

    status = build_regime_status(outputs_dir=tmp_path)

    assert status["dominant_regime"] == "goldilocks"
    assert status["regime_probability"] == 0.31
    assert status["monitor_ready"] is False
    assert status["status"] == "diagnostic_only"


def test_write_regime_status_cli(tmp_path: Path):
    (tmp_path / "news_accumulation_report.json").write_text(
        json.dumps({"readiness_label": "validation_candidate"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "write-regime-status",
            "--outputs-dir",
            str(tmp_path),
            "--db-path",
            str(tmp_path / "macro.duckdb"),
        ],
    )

    assert result.exit_code == 0, result.output
    output_path = tmp_path / "regime_status.json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["monitor_ready"] is True
    assert payload["feature_freshness"]["stale"] is None
    assert payload["vintage_freshness"] == []


def test_write_regime_status_function(tmp_path: Path):
    path = write_regime_status(outputs_dir=tmp_path)
    assert path == tmp_path / "regime_status.json"
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "diagnostic_only"


def test_feature_freshness_stale_when_features_trail_raw_observations(tmp_path: Path):
    """A features build that silently stopped running is otherwise invisible until a much
    later regime score goes stale."""
    from macro_engine.storage.duckdb_store import DuckDBStore

    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_raw_observations(
        pd.DataFrame(
            {
                "series_id": ["DGS10"],
                "date": [pd.Timestamp("2026-09-01")],
                "value": [4.2],
                "realtime_start": [pd.Timestamp("2026-09-01").date()],
                "realtime_end": [pd.Timestamp("2026-09-01").date()],
                "source": ["FRED"],
                "fetched_at": [pd.Timestamp("2026-09-01", tz="UTC")],
                "frequency": ["daily"],
                "units": ["Percent"],
            }
        )
    )
    store.upsert_features(
        pd.DataFrame(
            [
                {
                    "feature_id": "ten_year_level",
                    "series_id": "DGS10",
                    "date": pd.Timestamp("2026-05-03"),  # 121 days behind the raw observation
                    "raw_value": 4.0,
                    "transformed_value": 4.0,
                    "normalized_value": 0.1,
                    "transform": "level",
                    "normalization": "none",
                    "window_start": pd.Timestamp("2026-05-03"),
                    "window_end": pd.Timestamp("2026-05-03"),
                    "valid": True,
                    "reason": "ok",
                }
            ]
        )
    )

    status = build_regime_status(outputs_dir=tmp_path, db_path=db_path)

    freshness = status["feature_freshness"]
    assert freshness["max_raw_observation_date"] == "2026-09-01"
    assert freshness["max_feature_date"] == "2026-05-03"
    assert freshness["gap_days"] == 121
    assert freshness["stale"] is True


def test_vintage_freshness_flags_a_series_trailing_by_120_days(tmp_path: Path):
    """A series whose newest vintage has fallen behind is otherwise invisible until a
    point-in-time evaluation date resolves stale and gets rejected."""
    from macro_engine.storage.duckdb_store import DuckDBStore

    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    stale_realtime_start = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=120)
    store.upsert_raw_observation_vintages(
        pd.DataFrame(
            {
                "series_id": ["DGS10"],
                "date": [stale_realtime_start],
                "value": [4.1],
                "realtime_start": [stale_realtime_start.date()],
                "realtime_end": [stale_realtime_start.date()],
                "source": ["ALFRED"],
                "fetched_at": [pd.Timestamp.now(tz="UTC")],
                "frequency": ["daily"],
                "units": [None],
            }
        )
    )

    status = build_regime_status(outputs_dir=tmp_path, db_path=db_path)

    freshness = status["vintage_freshness"]
    assert len(freshness) == 1
    assert freshness[0]["series_id"] == "DGS10"
    assert freshness[0]["age_days"] == 120
    assert freshness[0]["stale"] is True
