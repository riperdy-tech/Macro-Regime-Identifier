from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import math
from copy import deepcopy
import time
from typing import Any, Protocol

from macro_engine.news.config import NewsAIConfig, NewsThemesConfig
from macro_engine.news.schema import (
    NewsClassificationPayload,
    NewsClassificationRecord,
    NewsItem,
)


SYSTEM_PROMPT_TEMPLATE = """You classify news/events for macro and sector diagnostics.

Return valid JSON only. Do not include markdown.
Do not provide investment recommendations.
Do not use buy, sell, overweight, underweight, avoid, trade, allocation, portfolio allocation, or position sizing language.
Use only these macro theme IDs: {theme_ids}
Use only these sector IDs: {sector_ids}
Use only these secular theme IDs: {secular_theme_ids}
Use uncertainty when the article is ambiguous.
If the article is irrelevant to macro or sector diagnostics, use low severity and low confidence.
If the article does not relate to any secular theme, set secular_theme to null.

Expected JSON shape:
{{
  "summary": "short diagnostic summary",
  "macro_themes": [
    {{
      "theme_id": "inflation_pressure",
      "direction": "positive",
      "severity": 0.0,
      "confidence": 0.0,
      "time_horizon": "short_term",
      "rationale": "why this theme applies"
    }}
  ],
  "sector_impacts": [
    {{
      "sector_id": "energy",
      "impact_direction": "tailwind",
      "impact_score": 0.0,
      "confidence": 0.0,
      "rationale": "why this sector is affected"
    }}
  ],
  "entities": [
    {{
      "name": "entity",
      "entity_type": "company|country|central_bank|commodity|sector|other",
      "relevance": 0.0
    }}
  ],
  "secular_theme": "ai_compute",
  "overall_severity": 0.0,
  "overall_confidence": 0.0,
  "time_horizon": "short_term"
}}

secular_theme must be one of the allowed secular theme IDs above, or null if none apply.

Allowed enum values:
- theme direction: positive, negative, mixed, neutral, unclear
- sector impact_direction: tailwind, headwind, mixed, neutral, unclear
- entity_type: company, country, central_bank, commodity, sector, region, person, other
- time_horizon: immediate, short_term, medium_term, long_term, unclear

Numeric bounds:
- severity and confidence must be decimal numbers from 0.0 to 1.0
- impact_score must be a decimal number from -1.0 to 1.0
- entity relevance must be a decimal number from 0.0 to 1.0

When uncertain, use "unclear" or "neutral" and lower confidence.
Do not invent unsupported sector impacts.
"""


class NewsClassifier(Protocol):
    provider_name: str
    model_name: str

    def classify(self, item: NewsItem, themes: NewsThemesConfig) -> dict[str, Any]:
        """Return raw classification JSON for a news item."""


class MockNewsClassifier:
    provider_name = "mock"
    model_name = "mock-news-classifier"

    def classify(self, item: NewsItem, themes: NewsThemesConfig) -> dict[str, Any]:
        text = f"{item.title} {item.body}".lower()
        macro_themes = []
        sector_impacts = []
        secular_theme = None
        if "oil" in text or "energy" in text:
            macro_themes.append(
                {
                    "theme_id": "commodity_pressure",
                    "direction": "positive",
                    "severity": 0.6,
                    "confidence": 0.8,
                    "time_horizon": "short_term",
                    "rationale": "The item references energy or oil pressure.",
                }
            )
            sector_impacts.append(
                {
                    "sector_id": "energy",
                    "impact_direction": "tailwind",
                    "impact_score": 0.5,
                    "confidence": 0.75,
                    "rationale": "Energy references can be a diagnostic sector tailwind.",
                }
            )
        elif "fed" in text or "rate" in text or "central bank" in text:
            macro_themes.append(
                {
                    "theme_id": "monetary_tightening",
                    "direction": "positive",
                    "severity": 0.5,
                    "confidence": 0.7,
                    "time_horizon": "short_term",
                    "rationale": "The item references monetary policy or interest rates.",
                }
            )
            sector_impacts.append(
                {
                    "sector_id": "real_estate",
                    "impact_direction": "headwind",
                    "impact_score": -0.4,
                    "confidence": 0.7,
                    "rationale": "Rate-sensitive sectors can face diagnostic pressure.",
                }
            )
        else:
            macro_themes.append(
                {
                    "theme_id": "growth_slowdown",
                    "direction": "unclear",
                    "severity": 0.2,
                    "confidence": 0.35,
                    "time_horizon": "unclear",
                    "rationale": "The item has limited direct macro signal.",
                }
            )
        return {
            "summary": f"Synthetic diagnostic classification for: {item.title}",
            "macro_themes": macro_themes,
            "sector_impacts": sector_impacts,
            "entities": [],
            "secular_theme": secular_theme,
            "overall_severity": max([theme["severity"] for theme in macro_themes], default=0.0),
            "overall_confidence": max(
                [theme["confidence"] for theme in macro_themes] + [0.0]
            ),
            "time_horizon": macro_themes[0]["time_horizon"] if macro_themes else "unclear",
        }


def build_system_prompt(themes: NewsThemesConfig) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        theme_ids=", ".join(sorted(themes.active_theme_ids)),
        sector_ids=", ".join(sorted(themes.sector_ids)),
        secular_theme_ids=", ".join(sorted(themes.secular_theme_ids)),
    )


def classify_news_item(
    item: NewsItem,
    *,
    classifier: NewsClassifier,
    themes: NewsThemesConfig,
    enable_schema_repair: bool = True,
    max_retries: int = 0,
    retry_backoff_seconds: float = 0.0,
) -> NewsClassificationRecord:
    raw: dict[str, Any]
    raw_attempts: list[dict[str, Any]] = []
    repair_notes: list[str] = []
    validation_errors: list[str] = []
    retry_count = 0
    try:
        raw = classifier.classify(item, themes)
        raw_attempts.append(raw)
        payload, attempt_notes = _validate_with_optional_repair(
            raw,
            themes,
            enable_schema_repair=enable_schema_repair,
        )
        repair_notes.extend(attempt_notes)
        status = "success"
        error = None
    except Exception as exc:
        validation_errors.append(str(exc))
        payload = None
        while retry_count < max_retries and _classifier_can_retry(classifier):
            retry_count += 1
            try:
                if retry_backoff_seconds:
                    time.sleep(retry_backoff_seconds)
                previous = raw_attempts[-1] if raw_attempts else None
                retry_max_tokens = _truncation_retry_max_tokens(classifier, previous)
                # Only pass the override on a truncation bump so classifiers
                # without a max_tokens parameter keep working on normal retries.
                extra = {} if retry_max_tokens is None else {"max_tokens": retry_max_tokens}
                raw_retry = classifier.classify_with_feedback(  # type: ignore[attr-defined]
                    item,
                    themes,
                    validation_error=str(exc),
                    previous_response=previous,
                    **extra,
                )
                raw_attempts.append(raw_retry)
                payload, attempt_notes = _validate_with_optional_repair(
                    raw_retry,
                    themes,
                    enable_schema_repair=enable_schema_repair,
                )
                repair_notes.extend(attempt_notes)
                status = "success"
                error = None
                break
            except Exception as retry_exc:
                validation_errors.append(str(retry_exc))
                exc = retry_exc
        else:
            status = "error"
            error = str(exc)
    raw = _raw_response_with_metadata(
        raw_attempts=raw_attempts,
        repair_notes=repair_notes,
        validation_errors=validation_errors,
        retry_count=retry_count,
        status=status,
    )
    now = datetime.now(UTC)
    classification_id = _classification_id(item.news_id, classifier.provider_name, now)
    return NewsClassificationRecord(
        classification_id=classification_id,
        news_id=item.news_id,
        classified_at=now,
        ai_provider=classifier.provider_name,
        ai_model=classifier.model_name,
        macro_themes=[] if payload is None else [theme.model_dump() for theme in payload.macro_themes],
        sector_impacts=[]
        if payload is None
        else [impact.model_dump() for impact in payload.sector_impacts],
        entities=[] if payload is None else [entity.model_dump() for entity in payload.entities],
        secular_theme=None if payload is None else payload.secular_theme,
        time_horizon=None if payload is None else payload.time_horizon,
        severity=None if payload is None else payload.overall_severity,
        confidence=None if payload is None else payload.overall_confidence,
        summary=None if payload is None else payload.summary,
        raw_ai_response=raw,
        classification_status=status,
        error_message=error,
    )


def validate_classification_payload(
    payload: dict[str, Any],
    themes: NewsThemesConfig,
) -> NewsClassificationPayload:
    parsed = NewsClassificationPayload.model_validate(payload)
    unknown_themes = {
        theme.theme_id for theme in parsed.macro_themes if theme.theme_id not in themes.active_theme_ids
    }
    if unknown_themes:
        raise ValueError(f"unknown news theme ids: {sorted(unknown_themes)}")
    unknown_sectors = {
        impact.sector_id for impact in parsed.sector_impacts if impact.sector_id not in themes.sector_ids
    }
    if unknown_sectors:
        raise ValueError(f"unknown sector ids: {sorted(unknown_sectors)}")
    if parsed.secular_theme is not None and parsed.secular_theme not in themes.secular_theme_ids:
        raise ValueError(
            f"unknown secular_theme '{parsed.secular_theme}'; "
            f"must be one of {sorted(themes.secular_theme_ids)} or null"
        )
    return parsed


def parse_ai_json_response(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("AI response JSON must be an object")
    return payload


def repair_classification_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    repaired = deepcopy(payload)
    notes: list[str] = []
    for theme in _list_items(repaired, "macro_themes"):
        _repair_enum(theme, "direction", _THEME_DIRECTION_ALIASES, notes)
        _clamp_field(theme, "severity", 0.0, 1.0, notes)
        _clamp_field(theme, "confidence", 0.0, 1.0, notes)
        _repair_enum(theme, "time_horizon", _TIME_HORIZON_ALIASES, notes)
    for impact in _list_items(repaired, "sector_impacts"):
        _repair_enum(impact, "impact_direction", _IMPACT_DIRECTION_ALIASES, notes)
        _clamp_field(impact, "impact_score", -1.0, 1.0, notes)
        _clamp_field(impact, "confidence", 0.0, 1.0, notes)
    for entity in _list_items(repaired, "entities"):
        _repair_enum(entity, "entity_type", _ENTITY_TYPE_ALIASES, notes)
        _clamp_field(entity, "relevance", 0.0, 1.0, notes)
    _clamp_field(repaired, "overall_severity", 0.0, 1.0, notes)
    _clamp_field(repaired, "overall_confidence", 0.0, 1.0, notes)
    _repair_enum(repaired, "time_horizon", _TIME_HORIZON_ALIASES, notes)
    for key in ["macro_themes", "sector_impacts", "entities"]:
        if key in repaired and repaired[key] in [None, ""]:
            repaired[key] = []
            notes.append(f"{key}:empty_optional_array")
    return repaired, notes


def should_use_mock_classifier(config: NewsAIConfig) -> bool:
    return config.mock_mode or not config.enable_live_ai


def retry_user_prompt(
    item: NewsItem,
    *,
    validation_error: str,
    previous_response: dict[str, Any] | None,
    max_body_chars: int = 8000,
    max_previous_response_chars: int = 4000,
) -> str:
    previous_response_text = truncate_for_prompt(
        json.dumps(previous_response or {}, ensure_ascii=False),
        max_previous_response_chars,
    )
    return (
        "Your previous JSON did not validate. Return corrected JSON only.\n\n"
        f"Validation error:\n{validation_error}\n\n"
        "Remember: enum values must exactly match the allowed lists, and numeric scores "
        "must be bounded decimals.\n\n"
        f"Previous response:\n{previous_response_text}\n\n"
        f"Article title: {item.title}\n"
        f"Article body:\n{truncate_for_prompt(item.body, max_body_chars)}"
    )


def truncate_for_prompt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = f"\n\n[truncated_to_{max_chars}_chars]"
    return text[:max_chars].rstrip() + suffix


def _classification_id(news_id: str, provider: str, classified_at: datetime) -> str:
    digest = hashlib.sha256(
        f"{news_id}|{provider}|{classified_at.isoformat()}".encode("utf-8")
    ).hexdigest()
    return f"classification_{digest[:16]}"


def _validate_with_optional_repair(
    raw: dict[str, Any],
    themes: NewsThemesConfig,
    *,
    enable_schema_repair: bool,
) -> tuple[NewsClassificationPayload, list[str]]:
    try:
        return validate_classification_payload(raw, themes), []
    except Exception:
        if not enable_schema_repair:
            raise
        repaired, notes = repair_classification_payload(raw)
        if not notes:
            raise
        return validate_classification_payload(repaired, themes), notes


def _raw_response_with_metadata(
    *,
    raw_attempts: list[dict[str, Any]],
    repair_notes: list[str],
    validation_errors: list[str],
    retry_count: int,
    status: str,
) -> dict[str, Any]:
    latest = raw_attempts[-1] if raw_attempts else {}
    return {
        "response": latest,
        "attempts": raw_attempts,
        "was_repaired": bool(repair_notes),
        "repair_notes": repair_notes,
        "validation_errors": validation_errors,
        "retry_count": retry_count,
        "classification_status": status,
    }


def _classifier_can_retry(classifier: NewsClassifier) -> bool:
    return callable(getattr(classifier, "classify_with_feedback", None))


def _response_was_truncated(raw: Any) -> bool:
    """True if a provider response hit the output token ceiling
    (finish_reason == 'length'), captured in _provider_usage."""
    if not isinstance(raw, dict):
        return False
    usage = raw.get("_provider_usage")
    return isinstance(usage, dict) and usage.get("finish_reason") == "length"


def _truncation_retry_max_tokens(classifier: NewsClassifier, previous: Any) -> int | None:
    """When the prior attempt truncated, return a larger token budget for the
    retry so an oversize article self-heals; otherwise None (use default)."""
    if not _response_was_truncated(previous):
        return None
    config = getattr(classifier, "config", None)
    base = getattr(config, "max_tokens", None)
    if not base:
        return None
    multiplier = getattr(config, "truncation_retry_multiplier", 2.0) or 2.0
    return int(math.ceil(base * multiplier))


def _list_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key, [])
    return value if isinstance(value, list) else []


def _repair_enum(
    payload: dict[str, Any],
    key: str,
    aliases: dict[str, str],
    notes: list[str],
) -> None:
    if key not in payload or payload[key] is None:
        return
    original = str(payload[key]).strip()
    normalized = aliases.get(original.lower(), original.lower())
    if normalized != payload[key]:
        payload[key] = normalized
        notes.append(f"{key}:{original}->{normalized}")


def _clamp_field(
    payload: dict[str, Any],
    key: str,
    lower: float,
    upper: float,
    notes: list[str],
) -> None:
    if key not in payload or payload[key] is None:
        return
    try:
        original = float(payload[key])
    except (TypeError, ValueError):
        return
    clamped = max(lower, min(upper, original))
    payload[key] = clamped
    if clamped != original:
        notes.append(f"{key}:{original}->{clamped}")


_THEME_DIRECTION_ALIASES = {
    "uncertainty": "unclear",
    "uncertain": "unclear",
    "unknown": "unclear",
    "positive_for_macro": "positive",
    "negative_for_macro": "negative",
}
_IMPACT_DIRECTION_ALIASES = {
    "uncertainty": "unclear",
    "uncertain": "unclear",
    "unknown": "unclear",
    "positive": "tailwind",
    "negative": "headwind",
    "positive_for_sector": "tailwind",
    "negative_for_sector": "headwind",
}
_TIME_HORIZON_ALIASES = {
    "near_term": "short_term",
    "short-term": "short_term",
    "medium-term": "medium_term",
    "long-term": "long_term",
    "unknown": "unclear",
    "uncertain": "unclear",
}
_ENTITY_TYPE_ALIASES = {
    "central bank": "central_bank",
    "government": "country",
    "geography": "region",
    "geographic_region": "region",
    "individual": "person",
}
