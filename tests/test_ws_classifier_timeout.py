"""DeepSeek classifier hard total-timeout: a hung request must not freeze the run."""

from __future__ import annotations

import time

import pytest
import requests

import macro_engine.news.providers.openai_classifier as oc
from macro_engine.news.config import NewsAIConfig


def _classifier(timeout_s: float) -> oc.DeepSeekNewsClassifier:
    cfg = NewsAIConfig(enable_live_ai=True, mock_mode=False, request_timeout_seconds=int(max(1, timeout_s)))
    return oc.DeepSeekNewsClassifier(cfg)


def test_post_raises_timeout_when_request_hangs(monkeypatch):
    clf = _classifier(1)
    monkeypatch.setattr(oc, "_HARD_TIMEOUT_BUFFER_SECONDS", 0)
    # Simulate a hung network call (no exception, just blocks).
    monkeypatch.setattr(clf, "_post_request", lambda payload: time.sleep(10))

    start = time.monotonic()
    with pytest.raises(requests.exceptions.Timeout):
        clf._post({"x": 1})
    # Returns near the hard ceiling (~1s), not the 10s hang.
    assert time.monotonic() - start < 5


def test_post_returns_normally_when_fast(monkeypatch):
    clf = _classifier(5)
    monkeypatch.setattr(clf, "_post_request", lambda payload: {"summary": "ok"})
    assert clf._post({"x": 1}) == {"summary": "ok"}
