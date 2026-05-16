from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
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
Use uncertainty when the article is ambiguous.
If the article is irrelevant to macro or sector diagnostics, use low severity and low confidence.

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
  "overall_severity": 0.0,
  "overall_confidence": 0.0,
  "time_horizon": "short_term"
}}
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
    )


def classify_news_item(
    item: NewsItem,
    *,
    classifier: NewsClassifier,
    themes: NewsThemesConfig,
) -> NewsClassificationRecord:
    raw: dict[str, Any]
    try:
        raw = classifier.classify(item, themes)
        payload = validate_classification_payload(raw, themes)
        status = "success"
        error = None
    except Exception as exc:
        raw = {"error": str(exc)}
        payload = None
        status = "error"
        error = str(exc)
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
    return parsed


def parse_ai_json_response(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("AI response JSON must be an object")
    return payload


def should_use_mock_classifier(config: NewsAIConfig) -> bool:
    return config.mock_mode or not config.enable_live_ai


def _classification_id(news_id: str, provider: str, classified_at: datetime) -> str:
    digest = hashlib.sha256(
        f"{news_id}|{provider}|{classified_at.isoformat()}".encode("utf-8")
    ).hexdigest()
    return f"classification_{digest[:16]}"
