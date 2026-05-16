from __future__ import annotations

import os
from typing import Any

import requests

from macro_engine.news.classify import build_system_prompt, parse_ai_json_response, retry_user_prompt
from macro_engine.news.config import NewsAIConfig, NewsThemesConfig
from macro_engine.news.schema import NewsItem


class DeepSeekNewsClassifier:
    def __init__(self, config: NewsAIConfig) -> None:
        self.config = config
        self.provider_name = "deepseek"
        self.model_name = config.model

    def classify(self, item: NewsItem, themes: NewsThemesConfig) -> dict[str, Any]:
        if not self.config.enable_live_ai:
            raise ValueError("live AI classification is disabled by config")
        payload = _request_payload(
            config=self.config,
            themes=themes,
            user_content=_user_prompt(item),
        )
        return self._post(payload)

    def classify_with_feedback(
        self,
        item: NewsItem,
        themes: NewsThemesConfig,
        *,
        validation_error: str,
        previous_response: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not self.config.enable_live_ai:
            raise ValueError("live AI classification is disabled by config")
        payload = _request_payload(
            config=self.config,
            themes=themes,
            user_content=retry_user_prompt(
                item,
                validation_error=validation_error,
                previous_response=previous_response,
            ),
        )
        return self._post(payload)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            raise ValueError(f"{self.config.api_key_env} is required for live AI classification")
        response = requests.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_ai_json_response(content)


def _request_payload(
    *,
    config: NewsAIConfig,
    themes: NewsThemesConfig,
    user_content: str,
) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": build_system_prompt(themes)},
            {"role": "user", "content": user_content},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def _user_prompt(item: NewsItem) -> str:
    published = "unknown" if item.published_at is None else item.published_at.isoformat()
    return (
        "Classify this article/event as JSON only.\n\n"
        f"Title: {item.title}\n"
        f"Source: {item.source}\n"
        f"Published at: {published}\n"
        f"Body:\n{item.body}"
    )
