from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.news.report import FORBIDDEN_REPORT_TERMS
from macro_engine.storage.duckdb_store import DuckDBStore


USAGE_REPORT_DISCLAIMER = (
    "This is a diagnostic operating-cost audit for live AI classifications. "
    "It is not investment advice, market action guidance, execution guidance, "
    "or instructions for changing holdings."
)


USAGE_FIELDS = [
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
]


def write_live_ai_usage_report(
    *,
    db_path: str | Path = "data/macro_engine.duckdb",
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    store = DuckDBStore(db_path)
    store.initialize()
    payload = build_live_ai_usage_report(store.read_table("news_classifications"))
    markdown = live_ai_usage_report_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "live_ai_usage_report.json"
    markdown_path = output / "live_ai_usage_report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_live_ai_usage_report(classifications: pd.DataFrame) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    if classifications.empty:
        return {
            "valid": False,
            "reason": "no_news_classifications",
            "generated_at": generated_at,
            "classification_count": 0,
            "usage_classification_count": 0,
            "missing_usage_count": 0,
            "latest_classified_at": None,
            "totals": _empty_usage_totals(),
            "provider_model_breakdown": [],
            "disclaimer": USAGE_REPORT_DISCLAIMER,
        }
    rows = [_usage_row(row) for row in classifications.to_dict(orient="records")]
    usage_rows = [row for row in rows if row["has_usage"]]
    latest = _latest_classified_at(rows)
    totals = _sum_usage(row["usage"] for row in usage_rows)
    return _json_safe(
        {
            "valid": True,
            "generated_at": generated_at,
            "classification_count": len(rows),
            "usage_classification_count": len(usage_rows),
            "missing_usage_count": len(rows) - len(usage_rows),
            "latest_classified_at": latest,
            "totals": totals,
            "provider_model_breakdown": _provider_model_breakdown(rows),
            "disclaimer": USAGE_REPORT_DISCLAIMER,
        }
    )


def live_ai_usage_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return (
            "# Live AI Usage Report\n\n"
            "No stored news classifications were available.\n\n"
            f"{payload['disclaimer']}\n"
        )
    totals = payload["totals"]
    groups = "\n".join(_group_lines(payload["provider_model_breakdown"])) or "- None"
    return f"""# Live AI Usage Report

Mode: diagnostic operating-cost audit from stored classification metadata.

Generated at: {payload["generated_at"]}
Latest classified at: {payload["latest_classified_at"]}

## Summary

- Classifications: {payload["classification_count"]}
- Classifications with provider usage: {payload["usage_classification_count"]}
- Classifications missing provider usage: {payload["missing_usage_count"]}
- Prompt tokens: {totals["prompt_tokens"]}
- Completion tokens: {totals["completion_tokens"]}
- Total tokens: {totals["total_tokens"]}
- Prompt cache hit tokens: {totals["prompt_cache_hit_tokens"]}
- Prompt cache miss tokens: {totals["prompt_cache_miss_tokens"]}

## Provider / Model

{groups}

{payload["disclaimer"]}
"""


def _usage_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = _parse_raw_response(row.get("raw_ai_response_json") or row.get("raw_ai_response"))
    usage = raw.get("response", {}).get("_provider_usage")
    provider = _string_or_unknown(row.get("ai_provider") or row.get("provider"))
    model = _string_or_unknown(row.get("ai_model") or row.get("model"))
    normalized_usage = _normalize_usage(usage)
    return {
        "provider": provider,
        "model": model,
        "classified_at": row.get("classified_at"),
        "has_usage": normalized_usage is not None,
        "usage": normalized_usage or _empty_usage_totals(),
    }


def _provider_model_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["provider"], row["model"]), []).append(row)
    breakdown = []
    for (provider, model), group_rows in sorted(groups.items()):
        usage_rows = [row for row in group_rows if row["has_usage"]]
        breakdown.append(
            {
                "provider": provider,
                "model": model,
                "classification_count": len(group_rows),
                "usage_classification_count": len(usage_rows),
                "missing_usage_count": len(group_rows) - len(usage_rows),
                "latest_classified_at": _latest_classified_at(group_rows),
                "totals": _sum_usage(row["usage"] for row in usage_rows),
            }
        )
    return breakdown


def _group_lines(groups: list[dict[str, Any]]) -> list[str]:
    lines = []
    for group in groups:
        totals = group["totals"]
        lines.append(
            "- {provider} / {model}: classifications {classifications}, with usage {with_usage}, "
            "missing usage {missing_usage}, total tokens {total_tokens}, prompt tokens "
            "{prompt_tokens}, completion tokens {completion_tokens}".format(
                provider=group["provider"],
                model=group["model"],
                classifications=group["classification_count"],
                with_usage=group["usage_classification_count"],
                missing_usage=group["missing_usage_count"],
                total_tokens=totals["total_tokens"],
                prompt_tokens=totals["prompt_tokens"],
                completion_tokens=totals["completion_tokens"],
            )
        )
    return lines


def _parse_raw_response(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or pd.isna(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {field: _to_int(value.get(field)) for field in USAGE_FIELDS}


def _sum_usage(items) -> dict[str, int]:
    totals = _empty_usage_totals()
    for item in items:
        for field in USAGE_FIELDS:
            totals[field] += _to_int(item.get(field))
    return totals


def _empty_usage_totals() -> dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def _latest_classified_at(rows: list[dict[str, Any]]) -> str | None:
    values = [pd.to_datetime(row.get("classified_at"), errors="coerce", utc=True) for row in rows]
    values = [value for value in values if pd.notna(value)]
    if not values:
        return None
    return max(values).isoformat()


def _string_or_unknown(value: Any) -> str:
    if value is None or pd.isna(value):
        return "unknown"
    return str(value)


def _to_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"live AI usage report contains forbidden language: {violations}")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
