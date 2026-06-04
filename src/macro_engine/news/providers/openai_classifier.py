from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import os
from typing import Any

import requests

from macro_engine.news.classify import (
    build_system_prompt,
    parse_ai_json_response,
    retry_user_prompt,
    truncate_for_prompt,
)
from macro_engine.news.config import NewsAIConfig, NewsThemesConfig
from macro_engine.news.schema import NewsItem

# Reusable single-worker pool so a hung request can be abandoned without the
# context manager blocking on the stuck thread (which dies on the read timeout).
_POST_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deepseek-post")
# Extra wall-clock budget beyond the per-read timeout, as a hard ceiling.
_HARD_TIMEOUT_BUFFER_SECONDS = 15


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
            user_content=_user_prompt(item, max_body_chars=self.config.max_prompt_body_chars),
        )
        return self._post(payload)

    def classify_with_feedback(
        self,
        item: NewsItem,
        themes: NewsThemesConfig,
        *,
        validation_error: str,
        previous_response: dict[str, Any] | None,
        max_tokens: int | None = None,
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
                max_body_chars=self.config.max_prompt_body_chars,
                max_previous_response_chars=self.config.max_retry_response_chars,
            ),
            max_tokens=max_tokens,
        )
        return self._post(payload)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Hard total deadline: requests' scalar timeout is per-read only, so a
        # server that trickles bytes can hang forever. Run the call on a worker
        # and abandon it past a wall-clock ceiling so the batch loop proceeds
        # (a Timeout becomes a failed record upstream, not a frozen run).
        hard_timeout = self.config.request_timeout_seconds + _HARD_TIMEOUT_BUFFER_SECONDS
        future = _POST_EXECUTOR.submit(self._post_request, payload)
        try:
            return future.result(timeout=hard_timeout)
        except FuturesTimeout as exc:
            future.cancel()  # thread keeps running until its read timeout fires
            raise requests.exceptions.Timeout(
                f"deepseek request exceeded hard timeout {hard_timeout}s"
            ) from exc

    def _post_request(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            # (connect, read) tuple so a slow connect also bounds quickly.
            timeout=(15, self.config.request_timeout_seconds),
        )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
        provider_usage = _provider_usage_metadata(data.get("usage"), choice.get("finish_reason"))
        try:
            parsed = parse_ai_json_response(content)
        except ValueError as exc:
            parsed = _invalid_provider_payload(str(exc), provider_usage)
            return parsed
        if provider_usage:
            parsed["_provider_usage"] = provider_usage
        return parsed


def _request_payload(
    *,
    config: NewsAIConfig,
    themes: NewsThemesConfig,
    user_content: str,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": build_system_prompt(themes)},
            {"role": "user", "content": user_content},
        ],
        "temperature": config.temperature,
        "max_tokens": max_tokens or config.max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def _provider_usage_metadata(
    usage: dict[str, Any] | None,
    finish_reason: str | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if isinstance(usage, dict):
        for key in [
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "completion_tokens_details",
            "prompt_tokens_details",
        ]:
            if key in usage:
                metadata[key] = usage[key]
    if finish_reason is not None:
        metadata["finish_reason"] = finish_reason
    return metadata


def _invalid_provider_payload(error: str, provider_usage: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": "",
        "macro_themes": [],
        "sector_impacts": [],
        "entities": [],
        "overall_severity": 0.0,
        "overall_confidence": 0.0,
        "time_horizon": "unclear",
        "_provider_error": error,
    }
    if provider_usage:
        payload["_provider_usage"] = provider_usage
    return payload


def _user_prompt(item: NewsItem, *, max_body_chars: int = 8000) -> str:
    published = "unknown" if item.published_at is None else item.published_at.isoformat()
    return (
        "Classify this article/event as JSON only.\n\n"
        f"Title: {item.title}\n"
        f"Source: {item.source}\n"
        f"Published at: {published}\n"
        f"Body:\n{truncate_for_prompt(item.body, max_body_chars)}"
    )
