"""Export-contract safety for the additive anchor artifacts.

The load-bearing claim: adding the anchors to the export CANNOT change `data_status`.
That is enforced structurally (a separate OPTIONAL_OUTPUT_FILES list that
`_data_status()` never sees), and these tests assert the structure rather than trusting
it — a future refactor that folds the two lists back together must fail here.
"""

from __future__ import annotations

import json
from pathlib import Path

from macro_engine.dashboard_export import (
    DASHBOARD_OUTPUT_FILES,
    HISTORY_INDEX_FILE,
    OPTIONAL_OUTPUT_FILES,
    export_dashboard_data,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_optional_and_required_lists_are_disjoint():
    """An artifact in both lists would reintroduce the failure this design prevents."""
    assert set(OPTIONAL_OUTPUT_FILES) & set(DASHBOARD_OUTPUT_FILES) == set()


def test_anchor_artifacts_are_declared_optional_not_required():
    for filename in (
        "cost_of_capital_anchor.json",
        "long_run_growth_anchor.json",
        "sector_multiple_bands.json",
        "rs2_repair_package.json",
        "news_advisory_block.json",
    ):
        assert filename in OPTIONAL_OUTPUT_FILES
        assert filename not in DASHBOARD_OUTPUT_FILES


def test_data_status_stays_complete_when_anchors_are_absent(tmp_path: Path):
    """The core guarantee: every required file present, no anchor built."""
    outputs = tmp_path / "outputs"
    for filename in DASHBOARD_OUTPUT_FILES:
        _write(outputs / filename, {"ok": True})
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "complete"
    assert manifest["missing_files"] == []
    assert manifest["optional_available_files"] == []
    assert set(manifest["optional_missing_files"]) == set(OPTIONAL_OUTPUT_FILES)
    assert all(name not in manifest["available_files"] for name in OPTIONAL_OUTPUT_FILES)


def test_data_status_stays_complete_when_anchors_are_present(tmp_path: Path):
    outputs = tmp_path / "outputs"
    for filename in DASHBOARD_OUTPUT_FILES:
        _write(outputs / filename, {"ok": True})
    for filename in OPTIONAL_OUTPUT_FILES:
        _write(outputs / filename, {"anchor": filename})
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "complete"
    assert set(manifest["optional_available_files"]) == set(OPTIONAL_OUTPUT_FILES)
    assert manifest["optional_missing_files"] == []
    # Optional files are still copied, so a consumer that wants them can read them.
    for filename in OPTIONAL_OUTPUT_FILES:
        assert (data_dir / filename).exists()


def test_a_missing_required_file_still_flips_data_status_to_partial(tmp_path: Path):
    """The existing contract must be untouched: this is the behaviour the design keeps."""
    outputs = tmp_path / "outputs"
    for filename in DASHBOARD_OUTPUT_FILES[:-1]:
        _write(outputs / filename, {"ok": True})
    for filename in OPTIONAL_OUTPUT_FILES:
        _write(outputs / filename, {"anchor": filename})
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "partial"
    assert manifest["missing_files"] == [DASHBOARD_OUTPUT_FILES[-1]]


def test_optional_availability_cannot_mask_a_missing_required_file(tmp_path: Path):
    """Publishing every anchor must not paper over a broken required artifact."""
    outputs = tmp_path / "outputs"
    _write(outputs / "daily_diagnostic_summary.json", {"ok": True})
    for filename in OPTIONAL_OUTPUT_FILES:
        _write(outputs / filename, {"anchor": filename})
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "partial"
    assert "daily_diagnostic_summary.json" not in manifest["missing_files"]
    assert HISTORY_INDEX_FILE in manifest["available_files"]


def test_regime_status_exposes_anchors_and_tolerates_their_absence(tmp_path: Path):
    """regime_status.py is the safe exposure surface: absent anchors must not raise."""
    from macro_engine.regime_status import build_regime_status

    outputs = tmp_path / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    status = build_regime_status(outputs_dir=outputs)
    assert status["capital_market_anchors"]["available"] is False
    assert status["capital_market_anchors"]["cost_of_capital"] is None
    assert status["capital_market_anchors"]["terminal_g_suggestion"] is None
    assert status["source_files"]["cost_of_capital_anchor"] is None
    assert status["status"] in {"monitor_ready", "diagnostic_only"}


def test_regime_status_surfaces_anchor_values_when_present(tmp_path: Path):
    from macro_engine.regime_status import build_regime_status

    outputs = tmp_path / "outputs"
    _write(
        outputs / "cost_of_capital_anchor.json",
        {
            "asof": "2026-04-30",
            "risk_free": {"nominal_10y": 0.0445},
            "implied_cost_of_equity": 0.081,
            "erp_basis": "universe",
            "degraded": False,
        },
    )
    _write(
        outputs / "long_run_growth_anchor.json",
        {"asof": "2026-04-30", "terminal_g_suggestion": 0.0375, "degraded": False},
    )
    _write(
        outputs / "sector_multiple_bands.json",
        {"asof": "2026-04-30", "panel_source": "unavailable", "degraded": True},
    )

    status = build_regime_status(outputs_dir=outputs)
    anchors = status["capital_market_anchors"]
    assert anchors["available"] is True
    assert anchors["degraded"] is True  # any one anchor degraded marks the set
    assert anchors["level_cost_of_equity"] == 0.081
    assert anchors["level_cost_of_equity_source"] == "implied_cost_of_equity(universe)"
    assert anchors["terminal_g_suggestion"] == 0.0375
    assert status["source_files"]["long_run_growth_anchor"] == "long_run_growth_anchor.json"


def test_replay_never_publishes_the_live_anchors(tmp_path: Path):
    """The anchors are a live shared input; a replay reconstitutes history and must not
    overwrite them with numbers describing a different world."""
    from macro_engine.operations_config import load_daily_pipeline_config

    config = load_daily_pipeline_config("config/daily_pipeline.yaml")
    assert config.anchors.enabled is True, "the shipped daily config must run the anchors"
    assert config.anchors.required is False, "a non-fatal step must not be able to fail the run"

    # The replay config is derived from the shipped one, so assert the override it applies.
    import inspect

    from macro_engine import replay

    source = inspect.getsource(replay._write_daily_replay_config)
    assert 'payload.setdefault("anchors", {})["enabled"] = False' in source


def test_regime_status_flags_a_future_dated_snapshot(tmp_path: Path):
    """The known 2031 defect: a consumer must not have to guess that the date is synthetic."""
    from macro_engine.regime_status import build_regime_status

    outputs = tmp_path / "outputs"
    _write(
        outputs / "current_regime.json",
        {"valid": True, "date": "2031-08-01", "reported_regime": "tightening"},
    )
    status = build_regime_status(outputs_dir=outputs)
    assert status["current_regime_date"] == "2031-08-01"
    assert status["current_regime_future_dated"] is True
    assert status["current_regime_stale"] is False
    assert status["current_regime_age_days"] < 0


def test_regime_status_flags_a_stale_snapshot(tmp_path: Path):
    from macro_engine.regime_status import build_regime_status

    outputs = tmp_path / "outputs"
    _write(
        outputs / "current_regime.json",
        {"valid": True, "date": "2020-01-01", "reported_regime": "goldilocks"},
    )
    status = build_regime_status(outputs_dir=outputs)
    assert status["current_regime_stale"] is True
    assert status["current_regime_future_dated"] is False


def test_regime_status_reports_no_date_instead_of_guessing(tmp_path: Path):
    from macro_engine.regime_status import build_regime_status

    outputs = tmp_path / "outputs"
    _write(outputs / "current_regime.json", {"valid": True, "reported_regime": "reflation"})
    status = build_regime_status(outputs_dir=outputs)
    assert status["current_regime_date"] is None
    assert status["current_regime_future_dated"] is None
    assert status["current_regime_stale"] is None


def test_regime_status_survives_an_unparseable_date(tmp_path: Path):
    from macro_engine.regime_status import build_regime_status

    outputs = tmp_path / "outputs"
    _write(outputs / "current_regime.json", {"valid": True, "date": "not-a-date"})
    status = build_regime_status(outputs_dir=outputs)
    assert status["current_regime_date"] == "not-a-date"
    assert status["current_regime_future_dated"] is None


def test_daily_anchors_step_records_the_paths_it_actually_wrote(tmp_path: Path, monkeypatch):
    """The daily run archives `generated_artifacts`, so the paths it records must be the
    paths the build wrote — not a parallel guess at the output directory."""
    from types import SimpleNamespace

    from macro_engine import daily
    from macro_engine.anchors import service as anchor_service

    anchor_config = tmp_path / "anchors.yaml"
    anchor_config.write_text(
        "anchors:\n  output_dir: " + (tmp_path / "out").as_posix() + "\n"
        "cost_of_capital:\n"
        "  risk_free:\n"
        "    nominal_10y: {series: DGS10, units: percent}\n"
        "    real_10y: {series: DFII10, units: percent}\n"
        "    breakeven_10y: {series: T10YIE, units: percent}\n"
        "  inflation: {cpi: CPIAUCSL, pce: PCEPI, yoy_periods: 12}\n",
        encoding="utf-8",
    )
    written: dict[str, Path] = {}

    def fake_build_anchors(**kwargs):
        target = tmp_path / "out"
        target.mkdir(parents=True, exist_ok=True)
        for name in (
            "cost_of_capital_anchor.json",
            "long_run_growth_anchor.json",
            "sector_multiple_bands.json",
            "rs2_repair_package.json",
        ):
            (target / name).write_text("{}", encoding="utf-8")
            written[name] = target / name
        return SimpleNamespace(degraded=False, degradation_reasons=[])

    outputs: list[str] = []
    services = {
        "build_anchors": fake_build_anchors,
        "write_news_advisory_block": lambda **_: (
            tmp_path / "out" / "news_advisory_block.json",
            tmp_path / "out" / "news_advisory_block.md",
        ),
    }
    config = SimpleNamespace(
        anchors=SimpleNamespace(
            config_path=str(anchor_config),
            macro_config_path="x",
            sector_config_path="y",
            advisory_block=SimpleNamespace(enabled=True, config_path="z"),
        )
    )
    monkeypatch.setattr(anchor_service, "load_anchor_config", lambda _p: SimpleNamespace(output_dir=str(tmp_path / "out")))
    daily._run_anchors(config, tmp_path / "db.duckdb", outputs, services)

    recorded = [p for p in outputs if p.endswith(".json")]
    assert recorded, "the step recorded no artifacts for archiving"
    for name, path in written.items():
        assert str(path) in outputs, f"{name} written but not recorded for archiving"


def test_optional_anchors_step_failure_is_non_fatal(tmp_path: Path):
    """A failing optional step must downgrade the run, not take the diagnostic down.

    `_run_step` records `failed_optional` for a non-required step, and `_status_from_steps`
    treats only `failed` as fatal. This is the end-to-end assertion of that contract, so a
    future edit that collapses the two statuses fails here rather than in production.
    """
    from macro_engine.daily import _run_step, _status_from_steps
    from macro_engine.operations_config import load_daily_pipeline_config

    config = load_daily_pipeline_config("config/daily_pipeline.yaml")
    statuses: dict[str, str] = {}
    errors: list[str] = []

    def boom():
        raise RuntimeError("anchor input unavailable")

    _run_step("anchors", statuses, errors, boom, fail=False, optional=True)
    assert statuses["anchors_status"] == "failed_optional"
    assert errors and "anchor input unavailable" in errors[0]
    assert _status_from_steps(statuses, [], config, False) == "success_with_warnings"

    # A REQUIRED step failing is still fatal, and still marks the run failed.
    strict: dict[str, str] = {}
    _run_step("anchors", strict, [], boom, fail=False, optional=False)
    assert strict["anchors_status"] == "failed"
    assert _status_from_steps(strict, [], config, False) == "failed"


def test_publish_guard_decides_per_file_on_a_partially_degraded_build(tmp_path: Path):
    """A mixed build must be judged per artifact, not by one global flag."""
    from macro_engine.anchors.models import (
        AnchorBundle,
        CostOfCapitalAnchor,
        LongRunGrowthAnchor,
        SectorMultipleBandsPayload,
    )
    from macro_engine.anchors.service import (
        COST_OF_CAPITAL_JSON,
        LONG_RUN_GROWTH_JSON,
        REPAIR_PACKAGE_JSON,
        write_anchor_outputs,
    )

    def bundle(*, coc_degraded: bool, growth_degraded: bool, bands_degraded: bool) -> AnchorBundle:
        return AnchorBundle(
            asof="2026-04-30",
            built_at="2026-04-30T00:00:00+00:00",
            cost_of_capital=CostOfCapitalAnchor(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                risk_free={"nominal_10y": None if coc_degraded else 0.0445},
                inflation={},
                degraded=coc_degraded,
            ),
            long_run_growth=LongRunGrowthAnchor(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                terminal_g_suggestion=None if growth_degraded else 0.035,
                degraded=growth_degraded,
            ),
            sector_multiple_bands=SectorMultipleBandsPayload(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                degraded=bands_degraded,
            ),
            # Same aggregate rule build_anchors applies.
            degraded=coc_degraded or growth_degraded or bands_degraded,
        )

    outputs = tmp_path / "outputs"
    # Seed everything clean.
    write_anchor_outputs(
        bundle=bundle(coc_degraded=False, growth_degraded=False, bands_degraded=False),
        output_dir=outputs,
    )
    assert json.loads((outputs / REPAIR_PACKAGE_JSON).read_text(encoding="utf-8"))[
        "degraded"
    ] is False

    # Rebuild with ONLY the cost-of-capital anchor degraded.
    write_anchor_outputs(
        bundle=bundle(coc_degraded=True, growth_degraded=False, bands_degraded=False),
        output_dir=outputs,
    )
    coc = json.loads((outputs / COST_OF_CAPITAL_JSON).read_text(encoding="utf-8"))
    growth = json.loads((outputs / LONG_RUN_GROWTH_JSON).read_text(encoding="utf-8"))
    repair = json.loads((outputs / REPAIR_PACKAGE_JSON).read_text(encoding="utf-8"))

    # Deferred PER FILE, not by one global flag: the degraded artifact keeps its real evidence
    # while the clean one is refreshed, and the repair package (degraded overall) is guarded.
    assert coc["risk_free"]["nominal_10y"] == 0.0445
    assert growth["terminal_g_suggestion"] == 0.035
    assert repair["degraded"] is False


def test_anchor_publish_guard_keeps_real_evidence_over_nulls(tmp_path: Path):
    """A degraded build must not overwrite a non-degraded artifact.

    These files feed another system. A run against a thin or empty database would
    otherwise publish a set of nulls straight over good evidence, and the consumer
    cannot tell the difference. A stale-but-real anchor beats a fresh-but-empty one.
    """
    from macro_engine.anchors.models import (
        AnchorBundle,
        AnchorProvenance,
        CostOfCapitalAnchor,
        LongRunGrowthAnchor,
        SectorMultipleBandsPayload,
    )
    from macro_engine.anchors.service import COST_OF_CAPITAL_JSON, write_anchor_outputs

    def bundle(*, degraded: bool) -> AnchorBundle:
        return AnchorBundle(
            asof="2026-04-30",
            built_at="2026-04-30T00:00:00+00:00",
            cost_of_capital=CostOfCapitalAnchor(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                risk_free={"nominal_10y": None if degraded else 0.0445},
                inflation={},
                degraded=degraded,
                provenance=AnchorProvenance(),
            ),
            long_run_growth=LongRunGrowthAnchor(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                degraded=degraded,
            ),
            sector_multiple_bands=SectorMultipleBandsPayload(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                degraded=degraded,
            ),
        )

    outputs = tmp_path / "outputs"
    write_anchor_outputs(bundle=bundle(degraded=False), output_dir=outputs)
    good = json.loads((outputs / COST_OF_CAPITAL_JSON).read_text(encoding="utf-8"))
    assert good["risk_free"]["nominal_10y"] == 0.0445

    write_anchor_outputs(bundle=bundle(degraded=True), output_dir=outputs)
    after = json.loads((outputs / COST_OF_CAPITAL_JSON).read_text(encoding="utf-8"))
    assert after["risk_free"]["nominal_10y"] == 0.0445

    # A non-degraded build always replaces, so the anchor is never frozen.
    write_anchor_outputs(bundle=bundle(degraded=False), output_dir=outputs)
    assert json.loads((outputs / COST_OF_CAPITAL_JSON).read_text(encoding="utf-8"))[
        "degraded"
    ] is False


def test_degraded_build_still_writes_when_nothing_is_there_yet(tmp_path: Path):
    """The guard protects existing evidence; it must not block a first publish."""
    from macro_engine.anchors.models import (
        AnchorBundle,
        CostOfCapitalAnchor,
        LongRunGrowthAnchor,
        SectorMultipleBandsPayload,
    )
    from macro_engine.anchors.service import COST_OF_CAPITAL_JSON, write_anchor_outputs

    outputs = tmp_path / "outputs"
    write_anchor_outputs(
        bundle=AnchorBundle(
            asof="2026-04-30",
            built_at="2026-04-30T00:00:00+00:00",
            cost_of_capital=CostOfCapitalAnchor(
                asof="2026-04-30",
                built_at="2026-04-30T00:00:00+00:00",
                risk_free={"nominal_10y": None},
                inflation={},
                degraded=True,
            ),
            long_run_growth=LongRunGrowthAnchor(
                asof="2026-04-30", built_at="2026-04-30T00:00:00+00:00", degraded=True
            ),
            sector_multiple_bands=SectorMultipleBandsPayload(
                asof="2026-04-30", built_at="2026-04-30T00:00:00+00:00", degraded=True
            ),
        ),
        output_dir=outputs,
    )
    payload = json.loads((outputs / COST_OF_CAPITAL_JSON).read_text(encoding="utf-8"))
    assert payload["degraded"] is True
    assert payload["risk_free"]["nominal_10y"] is None


def test_anchor_status_never_labels_a_risk_free_rate_as_a_cost_of_equity(tmp_path: Path):
    """The two are different claims and the status view must say which one it is showing."""
    from macro_engine.anchors.service import anchor_status

    outputs = tmp_path / "outputs"
    _write(
        outputs / "cost_of_capital_anchor.json",
        {
            "asof": "2026-04-30",
            "risk_free": {"nominal_10y": 0.0445},
            "market_implied_coe": None,
            "degraded": True,
        },
    )
    status = anchor_status(outputs_dir=outputs)
    assert status["level_cost_of_equity"] == 0.0445
    assert status["level_cost_of_equity_source"] == "risk_free_10y_only_not_a_cost_of_equity"
    assert status["available"] is False


def test_anchor_status_reports_a_measured_cost_of_equity_as_such(tmp_path: Path):
    from macro_engine.anchors.service import anchor_status

    outputs = tmp_path / "outputs"
    _write(
        outputs / "cost_of_capital_anchor.json",
        {
            "asof": "2026-04-30",
            "risk_free": {"nominal_10y": 0.0445},
            "implied_cost_of_equity": 0.0812,
            "erp_basis": "universe",
            "degraded": False,
        },
    )
    status = anchor_status(outputs_dir=outputs)
    assert status["level_cost_of_equity"] == 0.0812
    assert status["level_cost_of_equity_source"] == "implied_cost_of_equity(universe)"
