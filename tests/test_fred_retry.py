"""FRED client retries transient 429 / 5xx instead of dropping the series."""

from __future__ import annotations

import pytest

from macro_engine.ingest.fred import FredClient, FredError


class _Resp:
    def __init__(self, status_code, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


_META = {"seriess": [{"title": "X", "frequency_short": "M"}]}


def _client(session):
    return FredClient(api_key="k", session=session, backoff_base_seconds=0.0, backoff_cap_seconds=0.0)


def test_retries_429_then_succeeds():
    session = _FakeSession([_Resp(429, text="rate"), _Resp(429, text="rate"), _Resp(200, _META)])
    client = _client(session)
    meta = client.get_series_metadata("UNRATE")
    assert meta["series_id"] == "UNRATE"
    assert session.calls == 3  # two 429s retried, third ok


def test_retries_5xx_then_succeeds():
    session = _FakeSession([_Resp(503, text="down"), _Resp(200, _META)])
    assert _client(session).get_series_metadata("UNRATE")["title"] == "X"


def test_gives_up_after_max_retries():
    session = _FakeSession([_Resp(429, text="rate")] * 10)
    client = FredClient(api_key="k", session=session, max_retries=3, backoff_base_seconds=0.0)
    with pytest.raises(FredError, match="HTTP 429"):
        client.get_series_metadata("UNRATE")
    assert session.calls == 4  # initial + 3 retries, then gives up


def test_non_retryable_400_raises_immediately():
    session = _FakeSession([_Resp(400, text="bad key")])
    with pytest.raises(FredError, match="HTTP 400"):
        _client(session).get_series_metadata("UNRATE")
    assert session.calls == 1
