"""FRED client retries transient 429 / 5xx instead of dropping the series."""

from __future__ import annotations

import pytest
import requests

from macro_engine.ingest.fred import FredClient, FredError


class _Resp:
    def __init__(self, status_code, payload=None, headers=None, text="", json_error=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


class _RaisingSession:
    """Every request raises a transport fault — a socket, not an HTTP status."""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        raise self._exc


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


def test_retries_transport_timeout_then_succeeds():
    """A read timeout is a socket fault, not a verdict: retry it like a 5xx.

    Regression: a single ReadTimeout mid-backfill killed a ~6,500-request vintage run,
    because the exception was neither retried here nor a FredError the caller tolerates.
    """
    session = _FakeSession([_Resp(200, _META)])
    calls = {"n": 0}
    real_get = session.get

    def flaky_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ReadTimeout("read timed out")
        return real_get(url, params=params, timeout=timeout)

    session.get = flaky_get
    assert _client(session).get_series_metadata("UNRATE")["title"] == "X"
    assert calls["n"] == 3


def test_transport_failure_surfaces_as_fred_error():
    """Exhausted transport retries must land as FredError so callers can skip the pair."""
    session = _RaisingSession(requests.exceptions.ReadTimeout("read timed out"))
    client = FredClient(api_key="k", session=session, max_retries=2, backoff_base_seconds=0.0)
    with pytest.raises(FredError, match="ReadTimeout"):
        client.get_series_metadata("UNRATE")
    assert session.calls == 3  # initial + 2 retries


def test_retries_truncated_json_body():
    """A garbled body is a transport fault too — same retry budget."""
    session = _FakeSession(
        [_Resp(200, None, text="{trunc", json_error=ValueError("truncated")), _Resp(200, _META)]
    )
    assert _client(session).get_series_metadata("UNRATE")["title"] == "X"
    assert session.calls == 2


_ALFRED_ABSENT = (
    '{"error_code":400,"error_message":"Bad Request.  The series does not exist in ALFRED '
    'but may exist in FRED."}'
)


def test_alfred_absent_series_is_an_empty_vintage_not_a_failure():
    """A pre-archive date is "no", not "broken".

    ALFRED reports a 1995 vintage request for a series it only began archiving in 2005 as HTTP
    400. Counting that as a failed fetch buried the real failures under thousands of phantoms
    in a full-history backfill, and contradicted this method's own documented contract.
    """
    session = _FakeSession([_Resp(400, text=_ALFRED_ABSENT)])
    frame = _client(session).get_series_observations_vintage("DFII10", as_of="1995-01-01")
    assert frame.empty
    assert session.calls == 1  # not retried: the answer will not change


def test_a_genuine_400_is_still_a_failure():
    session = _FakeSession([_Resp(400, text='{"error_code":400,"error_message":"Bad API key"}')])
    with pytest.raises(FredError, match="HTTP 400"):
        _client(session).get_series_observations_vintage("DFII10", as_of="1995-01-01")
