from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from macro_engine.news.report import FORBIDDEN_REPORT_TERMS


EXTRA_FORBIDDEN_TERMS = [
    "recommend",
    "should own",
    "should short",
    "invest in",
]


@dataclass(frozen=True)
class GuardrailAuditResult:
    status: str
    violations: list[dict[str, str]]

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def audit_markdown_reports(paths: list[str | Path]) -> GuardrailAuditResult:
    violations: list[dict[str, str]] = []
    terms = sorted(set(FORBIDDEN_REPORT_TERMS + EXTRA_FORBIDDEN_TERMS), key=len, reverse=True)
    for path_like in paths:
        path = Path(path_like)
        if not path.exists() or path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for term in terms:
            if re.search(re.escape(term), text, flags=re.IGNORECASE):
                violations.append({"path": str(path), "term": term})
    return GuardrailAuditResult(
        status="failed" if violations else "passed",
        violations=violations,
    )
