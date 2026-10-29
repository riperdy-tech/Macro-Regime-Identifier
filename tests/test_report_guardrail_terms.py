"""Quoted article text in the news reports is scrubbed against every term the daily
guardrail rejects. On 2026-10-29 an article summary saying a committee was
"recommending over 100 changes" passed the report scrubber and failed the guardrail."""
from __future__ import annotations

from pathlib import Path

import pytest

from macro_engine.guardrails import audit_markdown_reports
from macro_engine.news import report, score_report
from macro_engine.news.report import EXTRA_FORBIDDEN_TERMS, FORBIDDEN_REPORT_TERMS


@pytest.mark.parametrize("scrub", [report._safe_report_text, score_report._safe_report_text])
def test_scrubbed_article_text_passes_the_guardrail(tmp_path: Path, scrub):
    texts = [f"Officials said they {term.upper()}ed it." for term in FORBIDDEN_REPORT_TERMS + EXTRA_FORBIDDEN_TERMS]
    texts.append("A committee found failures, recommending over 100 changes.")
    path = tmp_path / "report.md"
    path.write_text("\n".join(scrub(text) for text in texts), encoding="utf-8")

    result = audit_markdown_reports([path])

    assert result.passed, result.violations
