"""E0.2b: GdeltPacer spacing and 429 backoff, with a fake clock and fake HTTP (no real time, no network)."""
from __future__ import annotations

from urllib.error import HTTPError

import pytest

from macro_engine.news.ingest import GdeltPacer


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _err(code: int, retry_after: str | None = None) -> HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return HTTPError("https://example/gdelt", code, "err", headers, None)


def _pacer(ft: FakeTime, responses: list, **kw) -> tuple[GdeltPacer, list[float]]:
    calls: list[float] = []
    it = iter(responses)

    def http(url: str) -> str:
        calls.append(ft.now)
        ft.now += 2.0  # each request takes 2 s
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    return GdeltPacer(sleep=ft.sleep, clock=ft.clock, jitter=lambda: 1.0, http_fetch=http, **kw), calls


def test_first_request_waits_the_polite_interval_then_returns():
    ft = FakeTime()
    p, calls = _pacer(ft, ["{}"])
    assert p.fetch("u", "s") == "{}"
    assert ft.sleeps == [5.0] and len(calls) == 1


def test_spacing_is_measured_from_the_end_of_the_previous_request():
    ft = FakeTime()
    p, calls = _pacer(ft, ["a", "b"])
    p.fetch("u", "s1")
    ft.now += 1.0  # 1 s of unrelated work after the first request ended
    p.fetch("u", "s2")
    # 2nd call: 1 s elapsed since the last request ended -> sleeps only the remaining 4 s
    assert ft.sleeps[1] == pytest.approx(4.0)


def test_no_sleep_when_the_gap_already_exceeds_the_interval():
    ft = FakeTime()
    p, _ = _pacer(ft, ["a", "b"])
    p.fetch("u", "s1")
    ft.now += 30.0
    p.fetch("u", "s2")
    assert len(ft.sleeps) == 1  # only the initial polite sleep


def test_429_is_retried_with_exponential_backoff_until_success():
    ft = FakeTime()
    p, calls = _pacer(ft, [_err(429), _err(429), "ok"])
    assert p.fetch("u", "s") == "ok"
    backoffs = [x for x in ft.sleeps if x >= 20.0]
    assert backoffs == [20.0, 40.0]
    assert len(calls) == 3 and p.waited_s == 60.0


def test_gives_up_after_the_schedule_is_spent():
    ft = FakeTime()
    p, calls = _pacer(ft, [_err(429)] * 10)
    with pytest.raises(HTTPError):
        p.fetch("u", "s")
    assert len(calls) == 4  # first try + 3 retries
    assert [x for x in ft.sleeps if x >= 20.0] == [20.0, 40.0, 80.0]


def test_wait_budget_is_shared_across_sources_in_a_run():
    ft = FakeTime()
    p, calls = _pacer(ft, [_err(429)] * 20, wait_budget_s=70.0)
    with pytest.raises(HTTPError):
        p.fetch("u", "s1")  # waits 20 + 40 = 60, next wait 80 would exceed 70
    assert p.waited_s == 60.0
    before = len(calls)
    with pytest.raises(HTTPError):
        p.fetch("u", "s2")  # only 10 s of budget left: no retry possible
    assert len(calls) == before + 1


def test_retry_after_header_is_honoured_when_longer_than_the_schedule():
    ft = FakeTime()
    p, _ = _pacer(ft, [_err(429, retry_after="75"), "ok"])
    p.fetch("u", "s")
    assert [x for x in ft.sleeps if x >= 20.0] == [75.0]


def test_non_429_error_is_raised_immediately_without_retry():
    ft = FakeTime()
    p, calls = _pacer(ft, [_err(500), "ok"])
    with pytest.raises(HTTPError):
        p.fetch("u", "s")
    assert len(calls) == 1 and p.waited_s == 0.0


def test_jitter_scales_the_backoff():
    ft = FakeTime()
    calls = iter([_err(429), "ok"])

    def http(url):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    p = GdeltPacer(sleep=ft.sleep, clock=ft.clock, jitter=lambda: 1.1, http_fetch=http)
    p.fetch("u", "s")
    assert [x for x in ft.sleeps if x >= 20.0] == [pytest.approx(22.0)]
