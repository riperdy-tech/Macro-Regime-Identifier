"""Tests of the daily macro brief (WO_MB1) on synthetic inputs. The fetches and the model call are injected: no test
touches the network, the model, or the repository's outputs/ folder."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine.brief import checks, inputs, run as brief_run, sources as src

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "config" / "phase_b_sources.yaml"
RUN_DATE = date(2026, 12, 2)
REGIME_DIMS = ["credit_liquidity", "growth_momentum", "inflation_pressure", "policy_stance", "yield_curve"]
SCORES = {"growth_momentum": 0.27, "inflation_pressure": 0.33, "policy_stance": -0.38, "credit_liquidity": 0.88,
          "yield_curve": 0.12, "monetary_liquidity": 0.68, "housing_activity": -0.47, "real_rates": 1.39,
          "commodity": 1.27, "dollar": 0.10}
LATEST = {"INDPRO": "2026-10-01", "PAYEMS": "2026-10-01", "UNRATE": "2026-10-01", "ICSA": "2026-11-07",
          "CPIAUCSL": "2026-09-01", "PCEPI": "2026-09-01", "FEDFUNDS": "2026-10-01", "BAA10Y": "2026-11-13",
          "NFCI": "2026-11-06", "T10Y2Y": "2026-11-16"}


def make_current() -> dict:
    cur = {
        "date": "2026-11-01T00:00:00",
        "dimension_scores": [{"dimension_id": d, "date": "2026-11-01T00:00:00", "score": s, "valid": True, "reason": "ok"}
                             for d, s in SCORES.items()],
        "feature_health": [{"series_id": s, "latest_valid_date": f"{d}T00:00:00"} for s, d in LATEST.items()],
    }
    cur["regime_probabilities"] = inputs.regime_reading({}, "2026-11-01", cur, CONFIG)["probabilities"]
    return cur


@pytest.fixture()
def env(tmp_path):
    """A synthetic world: a store with 60 months of scores, current_regime.json, an event register, folders."""
    store = tmp_path / "store.duckdb"
    rng = np.random.default_rng(7)
    rows = []
    for d in SCORES:
        level = 0.0
        for m in pd.date_range("2021-11-01", "2026-10-01", freq="MS"):
            level += float(rng.normal(0, 0.2))
            rows.append((d, m.to_pydatetime(), level, True))
    con = duckdb.connect(str(store))
    con.execute("create table dimension_scores (dimension_id varchar, date timestamp, score double, valid boolean)")
    con.executemany("insert into dimension_scores values (?, ?, ?, ?)", rows)
    con.close()
    inp = tmp_path / "inputs"
    inp.mkdir()
    (inp / "current_regime.json").write_text(json.dumps(make_current()), encoding="utf-8")
    (inp / "event_register.json").write_text(json.dumps({"candidates": [
        {"title": "Oil jumps on supply news", "last_report_at": "2026-11-20T10:00:00", "publisher_count": 3},
        {"title": "Old story", "last_report_at": "2026-10-01T10:00:00", "publisher_count": 9}]}), encoding="utf-8")
    return {"store": store, "inputs": inp, "out": tmp_path / "out", "sources": tmp_path / "sources"}


SPECS = [
    {"id": "src_a", "agency": "Agency A", "what": "prices", "url": "http://a.test/page", "date_hint": r"current release:\s*"},
    {"id": "src_b", "agency": "Agency B", "what": "jobs", "url": "http://b.test/page", "date_hint": r"current release:\s*"},
]
PAGES = {
    "http://a.test/page": b"<html><body><p>Current release: December 1, 2026</p><p>Consumer prices rose  0.5 percent "
                          b"in October,\nthe largest rise in a year.</p></body></html>",
    "http://b.test/page": b"<html><body><p>Current release: November 3, 2026</p><p>Payrolls rose by 100,000.</p></body></html>",
}


def fake_get(calls=None):
    def get(url):
        if calls is not None:
            calls.append(url)
        if url in PAGES:
            return 200, PAGES[url]
        return 403, b"<html><title>Access Denied</title></html>"
    return get


def reply(**over):
    base = {"dimensions": {d: {"change": "unchanged", "size": "none", "quotes": [], "reason": "nothing new"}
                           for d in REGIME_DIMS},
            "events": [{"date": "2026-11-20", "event": "Oil jumps", "source": "event register"}],
            "summary": "One. Two. Three."}
    base["dimensions"].update(over)
    return json.dumps(base)


def run(env, ask, get=None, **kw):
    return brief_run.run_brief(store=env["store"], config=CONFIG, inputs_dir=env["inputs"], out_dir=env["out"],
                               sources_dir=env["sources"], run_date=RUN_DATE, get=get or fake_get(), ask=ask,
                               now=datetime(2026, 12, 2, 6, 0, tzinfo=timezone.utc), source_specs=SPECS, **kw)


def up(source="src_a", text="Consumer prices rose 0.5 percent in October, the largest rise in a year.", size="typical",
       change="higher"):
    return {"change": change, "size": size, "quotes": [{"source": source, "text": text}], "reason": "Prices rose."}


# ---------------------------------------------------------------------------------------------------- the checks
SOURCES = {"s": {"status": "fetched", "release_date": "2026-12-01", "text": "Prices rose   0.5 percent\nin October."}}


def test_quote_found_word_for_word_ignoring_whitespace():
    assert checks.quote_check({"source": "s", "text": "Prices rose 0.5 percent in October."}, SOURCES, ["s"], "2026-11-17") == []


def test_quote_not_word_for_word_fails():
    fails = checks.quote_check({"source": "s", "text": "Prices rose 0.6 percent in October."}, SOURCES, ["s"], "2026-11-17")
    assert any("not found word for word" in f for f in fails)


def test_quote_from_unknown_or_unoffered_source_fails():
    assert checks.quote_check({"source": "zz", "text": "x"}, SOURCES, ["s"], "2026-11-17")
    fails = checks.quote_check({"source": "s", "text": "Prices rose"}, SOURCES, [], "2026-11-17")
    assert any("not offered" in f for f in fails)


def test_release_date_must_be_after_the_data_date():
    ok = checks.quote_check({"source": "s", "text": "Prices rose"}, SOURCES, ["s"], "2026-11-30")
    same = checks.quote_check({"source": "s", "text": "Prices rose"}, SOURCES, ["s"], "2026-12-01")
    older = checks.quote_check({"source": "s", "text": "Prices rose"}, SOURCES, ["s"], "2026-12-15")
    assert ok == []
    assert any("not after" in f for f in same)
    assert any("not after" in f for f in older)


def test_move_needs_size_and_quote_but_unchanged_needs_nothing():
    assert checks.check_dimension({"change": "unchanged"}, SOURCES, ["s"], "2026-11-17")["passed"]
    assert not checks.check_dimension({"change": "higher", "size": "typical", "quotes": [], "reason": "r"},
                                      SOURCES, ["s"], "2026-11-17")["passed"]
    assert not checks.check_dimension({"change": "higher", "size": "huge", "reason": "r", "quotes": [
        {"source": "s", "text": "Prices rose"}]}, SOURCES, ["s"], "2026-11-17")["passed"]
    assert checks.check_dimension({"change": "higher", "size": "small", "reason": "r", "quotes": [
        {"source": "s", "text": "Prices rose"}]}, SOURCES, ["s"], "2026-11-17")["passed"]


# ---------------------------------------------------------------------------------------------------- the bound
def test_bound_percentiles_and_signs():
    idx = pd.date_range("2026-01-01", periods=7, freq="MS")
    s = pd.Series([0.0, 1.0, 3.0, 6.0, 10.0, 15.0, 100.0], index=idx)  # one-month absolute changes 1,2,3,4,5,85
    month = idx[5]  # the current month; its own change (5) and the later ones are not used
    p = inputs.change_percentiles(s, month)
    assert p["n_changes"] == 4  # 1, 2, 3, 4
    assert p["small"] == pytest.approx(np.percentile([1, 2, 3, 4], 25))
    assert p["typical"] == pytest.approx(2.5)
    assert p["large"] == pytest.approx(np.percentile([1, 2, 3, 4], 90))
    assert checks.score_change("higher", "typical", p) == pytest.approx(2.5)
    assert checks.score_change("lower", "large", p) == pytest.approx(-p["large"])


def test_bound_none_without_history():
    s = pd.Series([1.0], index=pd.date_range("2026-01-01", periods=1, freq="MS"))
    assert inputs.change_percentiles(s, pd.Timestamp("2026-01-01")) is None


# ---------------------------------------------------------------------------------------------------- the run
def test_nothing_changes_gives_identical_readings(env):
    out = run(env, lambda prompt: (reply(), "agy: SUCCESS"))
    assert out["call"]["status"] == "ok"
    assert out["readings_identical"] is True
    assert out["as_of_today"]["probabilities"] == out["as_measured"]["probabilities"]
    assert out["as_of_today"]["leader"] == out["as_measured"]["leader"]
    assert "identical" in out["identical_note"]
    assert out["as_measured"]["max_abs_difference_from_current_regime_json"] == pytest.approx(0.0, abs=1e-12)


def test_accepted_change_moves_the_reading_and_halves_and_doubles(env):
    out = run(env, lambda prompt: (reply(inflation_pressure=up()), "agy: SUCCESS"))
    d = out["dimensions"]["inflation_pressure"]
    assert d["status"] == "accepted" and d["change"] == "higher" and d["size"] == "typical"
    assert d["score_change_applied"] == pytest.approx(d["bound_percentiles"]["typical"]) and d["score_change_applied"] > 0
    assert out["readings_identical"] is False
    assert out["as_of_today"]["scores"]["inflation_pressure"] == pytest.approx(SCORES["inflation_pressure"] + d["score_change_applied"])
    assert out["at_half_the_bound"]["scores"]["inflation_pressure"] == pytest.approx(
        SCORES["inflation_pressure"] + 0.5 * d["score_change_applied"])
    assert out["at_double_the_bound"]["scores"]["inflation_pressure"] == pytest.approx(
        SCORES["inflation_pressure"] + 2 * d["score_change_applied"])
    assert out["as_of_today"]["probabilities"] != out["as_measured"]["probabilities"]
    assert out["dimensions"]["growth_momentum"]["status"] == "unchanged"


def test_lower_change_is_negative(env):
    out = run(env, lambda prompt: (reply(policy_stance=up(change="lower", size="small")), "agy: SUCCESS"))
    assert out["dimensions"]["policy_stance"]["score_change_applied"] < 0


def test_refused_update_stays_as_measured_and_is_listed(env):
    bad = up(text="Consumer prices fell sharply in October.")
    out = run(env, lambda prompt: (reply(inflation_pressure=bad), "agy: SUCCESS"))
    d = out["dimensions"]["inflation_pressure"]
    assert d["status"] == "refused" and d["change"] == "unchanged" and d["score_change_applied"] == 0.0
    assert d["claimed"] == {"change": "higher", "size": "typical"}
    assert any("not found word for word" in f for f in d["check"]["failures"])
    assert out["readings_identical"] is True
    assert out["as_of_today"]["probabilities"] == out["as_measured"]["probabilities"]


def test_source_published_before_the_data_date_is_not_offered_and_a_quote_from_it_is_refused(env):
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return reply(growth_momentum=up(source="src_b", text="Payrolls rose by 100,000.")), "agy: SUCCESS"

    out = run(env, ask)
    # src_b was released 2026-11-03: before the growth data was public (claims to 2026-11-07), so it is not offered for it
    assert out["dimensions"]["growth_momentum"]["sources_offered"] == ["src_a"]
    assert out["dimensions"]["growth_momentum"]["status"] == "refused"
    assert any("not offered" in f for f in out["dimensions"]["growth_momentum"]["check"]["failures"])


def test_call_failure_writes_an_output_that_says_so(env):
    out = run(env, lambda prompt: (None, "agy: quota exhausted"))
    assert out["call"]["status"] == "failed" and "quota" in out["call"]["detail"]
    assert out["readings_identical"] is True
    assert out["summary"] is None
    on_disk = json.loads((env["out"] / "macro_brief.json").read_text(encoding="utf-8"))
    assert on_disk["call"]["status"] == "failed"


def test_unreadable_reply_is_a_failed_call(env):
    out = run(env, lambda prompt: ("I think things are fine.", "agy: SUCCESS"))
    assert out["call"]["status"] == "failed" and "could not be read" in out["call"]["detail"]
    assert out["readings_identical"] is True


def test_history_line_is_appended_and_never_rewritten(env):
    run(env, lambda prompt: (reply(inflation_pressure=up()), "agy: SUCCESS"))
    path = env["out"] / "macro_brief_history.jsonl"
    first = path.read_text(encoding="utf-8")
    lines = first.splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["date"] == "2026-12-02" and row["call_status"] == "ok"
    assert row["changes"]["inflation_pressure"]["status"] == "accepted"
    assert set(row["as_of_today"]) == {"probabilities", "leader"} and row["scores_measured"]["policy_stance"] == SCORES["policy_stance"]
    run(env, lambda prompt: (None, "agy: down"), force=True)
    second = path.read_text(encoding="utf-8")
    assert second.startswith(first) and len(second.splitlines()) == 2


def test_one_call_a_day_unless_forced(env):
    calls = []

    def ask(prompt):
        calls.append(1)
        return reply(), "agy: SUCCESS"

    run(env, ask)
    again = run(env, ask)
    assert len(calls) == 1 and again.get("already_ran_today") is True
    run(env, ask, force=True)
    assert len(calls) == 2


def test_no_offered_source_makes_no_call(env):
    calls = []
    out = run(env, lambda p: calls.append(p) or (reply(), "x"), get=lambda url: (403, b"Access Denied"))
    assert calls == [] and out["call"]["status"] == "not_made"
    assert all(s["status"] == "missing" for s in out["sources"])
    assert out["readings_identical"] is True


def test_no_regime_name_check_annotates_but_does_not_gate(env):
    r = json.loads(reply(inflation_pressure=up()))
    r["summary"] = "This looks like stagflation."
    out = run(env, lambda p: (json.dumps(r), "agy: SUCCESS"))
    assert out["regime_words_in_model_text"] == ["stagflation"]
    assert out["dimensions"]["inflation_pressure"]["status"] == "accepted"


def test_events_are_checked(env):
    r = json.loads(reply())
    r["events"] = [{"date": "not a date", "event": "x"}] + [{"date": "2026-11-2%d" % i, "event": "e"} for i in range(6)]
    out = run(env, lambda p: (json.dumps(r), "agy: SUCCESS"))
    assert len(out["events"]) <= checks.MAX_EVENTS
    assert any("event 0" in x for x in out["refused_events"])


# ---------------------------------------------------------------------------------------------------- the sources
def test_each_source_is_fetched_once_a_day(tmp_path):
    calls = []
    now = datetime(2026, 12, 2, tzinfo=timezone.utc)
    a = src.load_or_fetch(RUN_DATE, tmp_path, fake_get(calls), SPECS, now)
    b = src.load_or_fetch(RUN_DATE, tmp_path, fake_get(calls), SPECS, now)
    assert len(calls) == 2 and a == b
    assert a[0]["status"] == "fetched" and a[0]["release_date"] == "2026-12-01"
    assert "Consumer prices rose 0.5 percent in October, the largest rise in a year." in a[0]["text"]


def test_a_refused_source_is_recorded_as_missing(tmp_path):
    rec = src.load_or_fetch(RUN_DATE, tmp_path, lambda u: (403, b"<title>Access Denied</title>"), SPECS[:1],
                            datetime(2026, 12, 2, tzinfo=timezone.utc))[0]
    assert rec["status"] == "missing" and "403" in rec["reason"] and rec["text"] is None


def test_a_fetch_error_is_recorded_not_raised(tmp_path):
    def boom(url):
        raise TimeoutError("slow")

    rec = src.load_or_fetch(RUN_DATE, tmp_path, boom, SPECS[:1], datetime(2026, 12, 2, tzinfo=timezone.utc))[0]
    assert rec["status"] == "missing" and "TimeoutError" in rec["reason"]


def test_release_date_parsing():
    assert src.parse_release_date("x Current release: October 29, 2026Next release: November 25, 2026",
                                  r"current release:\s*") == "2026-10-29"
    assert src.parse_release_date("... FOR RELEASE 8:30 a.m. (ET) Thursday, October 24, 2024 ...",
                                  r"for release.{0,60}?") == "2024-10-24"
    assert src.parse_release_date("October 28, 2026 Federal Reserve issues FOMC statement") == "2026-10-28"
    assert src.parse_release_date("no date here") is None


def test_fed_source_resolves_the_newest_statement_from_the_feed(tmp_path):
    feed = (b"<rss><channel><item><title>Schedule</title><link><![CDATA[http://fed.test/schedule]]></link></item>"
            b"<item><title>Federal Reserve issues FOMC statement</title>"
            b"<link><![CDATA[http://fed.test/statement2]]></link></item>"
            b"<item><title>Federal Reserve issues FOMC statement</title><link><![CDATA[http://fed.test/old]]></link></item>"
            b"</channel></rss>")
    pages = {"http://fed.test/feed": (200, feed),
             "http://fed.test/statement2": (200, b"<html><body>December 10, 2026 Statement: the range is 4 percent."
                                                  b"</body></html>")}
    spec = {"id": "fed", "agency": "Fed", "what": "statement", "url": "http://fed.test/feed",
            "resolver": "fed_latest_statement"}
    rec = src.fetch_one(spec, lambda u: pages.get(u, (404, b"")), datetime(2026, 12, 11, tzinfo=timezone.utc))
    assert rec["status"] == "fetched" and rec["url"] == "http://fed.test/statement2"
    assert rec["release_date"] == "2026-12-10"


def test_the_user_agent_is_generic():
    assert "@" not in src.USER_AGENT and "http" not in src.USER_AGENT
