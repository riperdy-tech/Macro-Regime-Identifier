from macro_engine.config.loader import load_all_configs
from macro_engine.outputs.report import build_markdown_report
from macro_engine.pipeline import classify_observations
from macro_engine.toy_data import build_toy_observations


def test_markdown_report_contains_disclaimer():
    config = load_all_configs("config")
    result = classify_observations(build_toy_observations(), config, "2026-05-08")

    report = build_markdown_report(result["payload"])

    assert "Macro Regime Report" in report
    assert "not investment advice" in report
