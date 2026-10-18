"""E0.2b: the REAL GdeltPacer driven through the real ingest source loop across several sources.

Nothing is injected through `gdelt_fetch` (which bypasses the pacer). Only the clock, sleep, jitter and the
bottom-level HTTP call are faked, so spacing, retries, Retry-After, the shared 180 s wait budget and the
2-consecutive-exhausted-sources breaker are exercised together, in simulated time."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
import yaml

from macro_engine.news import ingest
from macro_engine.news.ingest import GdeltPacer, load_news_items_with_report

REQUEST_SECONDS = 2.0


class World:
    def __init__(self, script: list):
        self.now = 1000.0
        self.script = list(script)  # one entry per HTTP request, in order: body str or HTTPError
        self.starts: list[float] = []
        self.ends: list[float] = []
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def http(self, url: str) -> str:
        self.starts.append(self.now)
        self.now += REQUEST_SECONDS
        self.ends.append(self.now)
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _429(retry_after: str | None = None) -> HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return HTTPError("https://api.gdeltproject.org/api/v2/doc/doc", 429, "Too Many Requests", headers, None)


OK = json.dumps({"articles": []})


def _run(tmp_path: Path, monkeypatch, n_sources: int, script: list, **pacer_kw):
    world = World(script)
    cfg = tmp_path / "news.yaml"
    cfg.write_text(yaml.safe_dump({"news_sources": [
        {"source_id": f"gdelt_{i}", "provider": "gdelt", "enabled": True, "query": f"q{i}",
         "source_group": "macro_general", "max_items": 5} for i in range(n_sources)]}), encoding="utf-8")
    monkeypatch.setattr(ingest, "GdeltPacer", lambda: GdeltPacer(
        sleep=world.sleep, clock=lambda: world.now, jitter=lambda: 1.0, http_fetch=world.http, **pacer_kw))
    _, results = load_news_items_with_report(cfg)  # no gdelt_fetch: the pacer is on the path
    return world, {r.source_id: r.status for r in results}, [r.status for r in results]


def test_retries_and_retry_after_across_sources_keep_the_polite_spacing(tmp_path, monkeypatch):
    # source A: 429 then ok; source B: 429 with Retry-After 75 then ok; source C: ok
    world, _, statuses = _run(tmp_path, monkeypatch, 3, [_429(), OK, _429("75"), OK, OK])
    assert statuses == ["empty", "empty", "empty"]
    assert len(world.starts) == 5 and world.script == []
    assert sorted(s for s in world.sleeps if s >= 20.0) == [20.0, 75.0]  # exponential step 1; Retry-After wins
    # every request starts >= 5 s after the previous request ENDED, including across source boundaries
    for prev_end, start in zip(world.ends, world.starts[1:]):
        assert start - prev_end >= 5.0 - 1e-9


def test_budget_is_shared_and_two_consecutive_exhausted_sources_open_the_breaker(tmp_path, monkeypatch):
    # 4 sources, GDELT answers 429 forever. Source 0 spends 20+40+80 = 140 s of the 180 s budget (4 requests).
    # Source 1 gets 20 s more (160), cannot afford 40 -> 2 requests. Two exhausted in a row -> sources 2, 3 are
    # circuit_open and make NO request.
    world, _, statuses = _run(tmp_path, monkeypatch, 4, [_429()] * 20)
    assert statuses == ["rate_limited", "rate_limited", "circuit_open", "circuit_open"]
    assert len(world.starts) == 6
    backoffs = [s for s in world.sleeps if s >= 20.0]
    assert backoffs == [20.0, 40.0, 80.0, 20.0]
    assert sum(backoffs) == 160.0 <= 180.0  # the run's total backoff never exceeds the 180 s budget


def test_one_exhausted_source_does_not_open_the_breaker_and_the_next_still_runs(tmp_path, monkeypatch):
    world, _, statuses = _run(tmp_path, monkeypatch, 3, [_429()] * 4 + [OK, OK])
    assert statuses == ["rate_limited", "empty", "empty"]
    assert len(world.starts) == 6


def test_a_success_between_exhausted_sources_resets_the_consecutive_count(tmp_path, monkeypatch):
    # exhausted, ok, exhausted, ok: never two exhausted in a row, so the breaker never opens.
    # Budget made large so each exhausted source can spend its full schedule.
    script = [_429()] * 4 + [OK] + [_429()] * 4 + [OK]
    world, _, statuses = _run(tmp_path, monkeypatch, 4, script, wait_budget_s=1000.0)
    assert statuses == ["rate_limited", "empty", "rate_limited", "empty"]
    assert len(world.starts) == 10


@pytest.mark.parametrize("budget", [0.0, 19.0])
def test_a_budget_smaller_than_the_first_backoff_means_no_retry_at_all(tmp_path, monkeypatch, budget):
    world, _, statuses = _run(tmp_path, monkeypatch, 1, [_429()] * 5, wait_budget_s=budget)
    assert statuses == ["rate_limited"] and len(world.starts) == 1
