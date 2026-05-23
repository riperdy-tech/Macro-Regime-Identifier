"""Tests for v1.2 GitHub Actions daily automation."""

from pathlib import Path
import json
import tempfile

import pytest
import yaml

from macro_engine.automation import build_automation_summary, write_automation_summary


class TestAutomationSummary:
    def test_build_with_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = build_automation_summary(outputs_dir=tmp, dashboard_data_dir=tmp)
            assert "generated_at" in summary
            assert summary["macro"]["status"] == "missing"
            assert summary["dashboard"]["status"] == "missing"

    def test_build_with_regime_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            regime = {"date": "2026-05-01", "reported_regime": "reflation",
                       "reported_confidence": 0.03, "valid": True}
            Path(tmp, "current_regime.json").write_text(json.dumps(regime))
            summary = build_automation_summary(outputs_dir=tmp, dashboard_data_dir=tmp)
            assert summary["macro"]["regime"] == "reflation"
            assert summary["macro"]["confidence"] == 0.03

    def test_build_with_dashboard_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {"data_status": "complete", "missing_files": [],
                        "latest_macro_date": "2026-05-01", "latest_news_score_date": "2026-05-21"}
            Path(tmp, "manifest.json").write_text(json.dumps(manifest))
            summary = build_automation_summary(outputs_dir=tmp, dashboard_data_dir=tmp)
            assert summary["dashboard"]["data_status"] == "complete"

    def test_write_automation_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path, md_path = write_automation_summary(outputs_dir=tmp)
            assert json_path.exists()
            assert md_path.exists()
            data = json.loads(json_path.read_text())
            assert "generated_at" in data
            content = md_path.read_text()
            assert "Automation Run Summary" in content
            assert "not investment advice" in content

    def test_github_env_vars_captured(self, monkeypatch):
        monkeypatch.setenv("GITHUB_RUN_ID", "12345")
        monkeypatch.setenv("GITHUB_RUN_NUMBER", "42")
        monkeypatch.setenv("GITHUB_SHA", "abcdef")
        with tempfile.TemporaryDirectory() as tmp:
            summary = build_automation_summary(outputs_dir=tmp, dashboard_data_dir=tmp)
            assert summary["github_run_id"] == "12345"
            assert summary["github_run_number"] == "42"
            assert summary["github_sha"] == "abcdef"


class TestGitHubWorkflowConfig:
    def test_workflow_yaml_exists(self):
        workflow = Path(".github/workflows/daily-dashboard.yml")
        assert workflow.exists(), "daily-dashboard.yml must exist"

    def test_workflow_has_required_sections(self):
        workflow = Path(".github/workflows/daily-dashboard.yml")
        # PyYAML parses 'on' as True (YAML 1.1 boolean). Check raw text.
        content = workflow.read_text()
        assert "name:" in content
        assert "jobs:" in content
        assert "daily:" in content

    def test_workflow_supports_manual_trigger(self):
        workflow = Path(".github/workflows/daily-dashboard.yml")
        content = workflow.read_text()
        assert "workflow_dispatch" in content

    def test_workflow_supports_schedule(self):
        workflow = Path(".github/workflows/daily-dashboard.yml")
        content = workflow.read_text()
        assert "schedule:" in content
        assert "cron:" in content

    def test_workflow_does_not_expose_secrets_in_yaml(self):
        workflow = Path(".github/workflows/daily-dashboard.yml")
        content = workflow.read_text()
        # Secrets are referenced as ${{ secrets.X }} — no actual keys in YAML
        assert "secrets.FRED_API_KEY" in content
        # No hardcoded key values
        for line in content.split("\n"):
            if "secrets." in line and "${{" in line:
                continue  # expected reference format
            # Should not contain anything that looks like a 32-char API key
            import re
            if re.search(r"[a-z0-9]{32}", line):
                pytest.fail(f"Possible hardcoded secret in: {line}")


class TestGitHubDailyPipelineConfig:
    def test_github_config_exists(self):
        config = Path("config/daily_pipeline_github.yaml")
        assert config.exists(), "daily_pipeline_github.yaml must exist"

    def test_github_config_uses_mock_default(self):
        config = Path("config/daily_pipeline_github.yaml")
        content = yaml.safe_load(config.read_text())
        news = content["daily_pipeline"]["news"]
        assert news["allow_live_ai"] is False
        assert news["mock_mode_default"] is True

    def test_github_config_uses_synthetic_profile(self):
        config = Path("config/daily_pipeline_github.yaml")
        content = yaml.safe_load(config.read_text())
        news = content["daily_pipeline"]["news"]
        assert news["source_profile"] == "synthetic_sample"

    def test_github_config_does_not_reference_local_files(self):
        config = Path("config/daily_pipeline_github.yaml")
        lines = config.read_text().split("\n")
        # Exclude comment lines (they explain what NOT to use)
        code_lines = [line for line in lines if not line.strip().startswith("#")]
        code = "\n".join(code_lines)
        assert "data/news_pilot" not in code
        assert "last_30_days" not in code

    def test_no_secrets_in_config(self):
        config = Path("config/daily_pipeline_github.yaml")
        content = config.read_text()
        assert "FRED_API_KEY" not in content
        assert "DEEPSEEK_API_KEY" not in content
        assert "api_key" not in content.lower()


class TestAutomationSecretSafety:
    def test_export_dashboard_data_has_no_secrets(self):
        """Dashboard export must not contain API keys."""
        manifest_path = Path("dashboard/public/data/manifest.json")
        if manifest_path.exists():
            data = manifest_path.read_text()
            # No 32-char hex strings that look like API keys
            import re
            keys = re.findall(r"[a-z0-9]{32}", data)
            assert len(keys) == 0, f"Possible API keys in manifest: {keys}"

    def test_summary_json_has_no_secrets(self):
        summary_path = Path("outputs/automation_run_summary.json")
        if summary_path.exists():
            data = summary_path.read_text()
            assert "FRED_API_KEY" not in data
            assert "DEEPSEEK_API_KEY" not in data
