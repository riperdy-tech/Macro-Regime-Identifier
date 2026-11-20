"""Tests for the daily read (src/macro_engine/daily_read.py, WO_MN1), on a synthetic store and files and a fake `ask`.

No test makes a model call: `ask` is always a fake function.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from macro_engine import daily_read as dr
from macro_engine.cli import app

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
AS_OF = "2026-11-19"


def make_store(path, rows):
    """rows: dict(news_id, title, body, hours_ago, severity, confidence, status, provider, [classified_at_offset])."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE news_items (news_id VARCHAR, source VARCHAR, source_url VARCHAR, title VARCHAR, body VARCHAR, published_at TIMESTAMP)")
    con.execute("CREATE TABLE news_classifications (classification_id VARCHAR, news_id VARCHAR, classified_at TIMESTAMP, ai_provider VARCHAR, "
                "severity DOUBLE, confidence DOUBLE, summary VARCHAR, classification_status VARCHAR)")
    naive = NOW.replace(tzinfo=None)
    for k, r in enumerate(rows):
        nid = r["news_id"]
        con.execute("INSERT INTO news_items VALUES (?,?,?,?,?,?)",
                    [nid, r.get("source", "wire"), f"https://x/{nid}", r["title"], r.get("body", "body " + nid), naive - timedelta(hours=r["hours_ago"])])
        con.execute("INSERT INTO news_classifications VALUES (?,?,?,?,?,?,?,?)",
                    [f"c{k}", nid, naive - timedelta(hours=1, minutes=r.get("cls_age", 0)), r.get("provider", "deepseek"),
                     r["severity"], r["confidence"], "sum " + nid, r.get("status", "success")])
    con.close()


def good_rows(n=8):
    return [dict(news_id=f"a{i}", title=f"Headline number {i} about markets", body=f"Treasury yields climbed sharply on item {i} as investors sold bonds.",
                 hours_ago=2 + i, severity=0.5 + 0.01 * i, confidence=0.8) for i in range(n)]


MARKET = {
    "schema": "mri.market_state.v1", "asof": AS_OF, "step": "stress", "since": "2026-11-05", "driver": "rates", "driver_window": 21,
    "backdrop": None, "summary": "Stress since 2026-11-05. Led by rates (10-year yield +30 basis points in 21 sessions).",
    "facts": {"spy": {"return_5": -1.2, "return_21": -4.0, "return_63": 1.0}, "vix": {"level": 24.5},
              "series": {"DGS2": {"level": 4.1, "change_5": 5.0, "change_21": 20.0, "change_63": None},
                         "DGS10": {"level": 4.6, "change_5": 8.0, "change_21": 30.0, "change_63": 40.0}}},
    "leaders": [{"fund": "XLE", "sector_id": "energy", "relative_21": 3.2}],
    "laggards": [{"fund": "XLRE", "sector_id": "real_estate", "relative_21": -4.1}],
    "degraded": False,
}
ECONOMY = {"asof": AS_OF, "summary": "Inflation is above its reference.", "degraded": False}
EXPOSURES = {"as_of": AS_OF, "summary": "Over 21 sessions the 10-year yield rose 30 basis points.", "degraded": False}


def write_files(out: Path, market=MARKET, economy=ECONOMY, exposures=EXPOSURES):
    out.mkdir(parents=True, exist_ok=True)
    for name, doc in (("market_state", market), ("economy_state", economy), ("driver_exposures", exposures)):
        if doc is not None:
            (out / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def reply(driver="rates", quotes=None, why="Yields rose as bonds were sold. Stocks fell with them.", **kw):
    quotes = quotes if quotes is not None else [
        {"item": "N1", "text": "Treasury yields climbed sharply"},
        {"item": "N2", "text": "as investors sold bonds"},
    ]
    doc = {"driver": driver, "why": why, "changed_since_previous": "nothing material",
           "exposed": [{"what": "banks", "direction": "helped", "reason": "wider margins"}],
           "agrees_with_prices": "yes", "quotes": quotes}
    doc.update(kw)
    return json.dumps(doc)


def fake(text, detail="agy: SUCCESS"):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return text, detail

    ask.calls = calls
    return ask


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "s.duckdb"
    make_store(db, good_rows())
    out = tmp_path / "outputs"
    write_files(out)
    return db, out


def item(title="Yields climbed sharply today", body="Investors sold   bonds\nall day."):
    return {"id": "N1", "title": title, "body": body, "source": "wire", "source_url": "u", "published_at": "2026-11-19T01:00:00Z"}


# --------------------------------------------------------------------------- item selection


def test_window_and_ranking(tmp_path):
    rows = [
        dict(news_id="old", title="Too old", hours_ago=73, severity=0.9, confidence=0.9),
        dict(news_id="lo", title="Low", hours_ago=5, severity=0.2, confidence=0.5),
        dict(news_id="hi", title="High", hours_ago=30, severity=0.8, confidence=0.9),
        dict(news_id="tie_new", title="Tie new", hours_ago=3, severity=0.5, confidence=0.5),
        dict(news_id="tie_old", title="Tie old", hours_ago=9, severity=0.5, confidence=0.5),
        dict(news_id="zero", title="Zero severity", hours_ago=1, severity=0.0, confidence=0.9),
        dict(news_id="fut", title="Future", hours_ago=-2, severity=0.9, confidence=0.9),
    ]
    make_store(tmp_path / "s.duckdb", rows)
    items = dr.select_items(tmp_path / "s.duckdb", NOW)
    assert [i["title"] for i in items] == ["High", "Tie new", "Tie old", "Low", "Zero severity"]
    assert [i["id"] for i in items] == ["N1", "N2", "N3", "N4", "N5"]
    assert set(items[0]) >= {"title", "body", "source", "source_url", "published_at", "summary", "severity"}


def test_status_filter_and_newest_classification(tmp_path):
    rows = [
        dict(news_id="ok", title="Classified", hours_ago=2, severity=0.5, confidence=0.5),
        dict(news_id="err", title="Errored", hours_ago=2, severity=0.9, confidence=0.9, status="error"),
        dict(news_id="mock", title="Mocked", hours_ago=2, severity=0.9, confidence=0.9, provider="mock"),
    ]
    make_store(tmp_path / "s.duckdb", rows)
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    # a newer classification for "ok" with a different severity wins; a newer errored one is not a success and is ignored
    naive = NOW.replace(tzinfo=None)
    con.execute("INSERT INTO news_classifications VALUES ('n1','ok',?,'deepseek',0.7,0.7,'newer','success')", [naive])
    con.execute("INSERT INTO news_classifications VALUES ('n2','ok',?,'deepseek',NULL,NULL,'bad','error')", [naive + timedelta(minutes=5)])
    con.close()
    items = dr.select_items(tmp_path / "s.duckdb", NOW)
    assert [i["title"] for i in items] == ["Classified"]
    assert items[0]["summary"] == "newer" and items[0]["severity"] == 0.7


def test_duplicate_titles_offered_once(tmp_path):
    rows = [
        dict(news_id="a", title="Fed holds  rates", hours_ago=5, severity=0.5, confidence=0.5),
        dict(news_id="b", title="fed holds rates", hours_ago=4, severity=0.8, confidence=0.8),
        dict(news_id="c", title="Other", hours_ago=4, severity=0.1, confidence=0.1),
    ]
    make_store(tmp_path / "s.duckdb", rows)
    items = dr.select_items(tmp_path / "s.duckdb", NOW)
    assert [i["title"] for i in items] == ["fed holds rates", "Other"]


def test_cap_on_items_offered(tmp_path):
    rows = [dict(news_id=f"n{i}", title=f"Title {i}", hours_ago=1 + i * 0.1, severity=0.5, confidence=0.5) for i in range(dr.MAX_ITEMS + 15)]
    make_store(tmp_path / "s.duckdb", rows)
    items = dr.select_items(tmp_path / "s.duckdb", NOW)
    assert len(items) == dr.MAX_ITEMS and items[-1]["id"] == f"N{dr.MAX_ITEMS}"


def test_too_few_items_no_call(tmp_path):
    make_store(tmp_path / "s.duckdb", good_rows(4))
    write_files(tmp_path / "o")
    ask = fake(reply())
    doc = dr.build_daily_read(tmp_path / "s.duckdb", tmp_path / "o", NOW, ask)
    assert doc["status"] == "none" and "too_few_items" in doc["reasons"] and ask.calls == []
    assert doc["prices"]["stress_step"] == "stress"


def test_missing_market_state_no_call(tmp_path):
    make_store(tmp_path / "s.duckdb", good_rows())
    write_files(tmp_path / "o", market=None)
    ask = fake(reply())
    doc = dr.build_daily_read(tmp_path / "s.duckdb", tmp_path / "o", NOW, ask)
    assert doc["status"] == "none" and ask.calls == []
    assert any(r.startswith("market_state_unavailable") for r in doc["reasons"])


def test_null_step_no_call(tmp_path):
    make_store(tmp_path / "s.duckdb", good_rows())
    write_files(tmp_path / "o", market={**MARKET, "step": None, "degraded": True})
    ask = fake(reply())
    doc = dr.build_daily_read(tmp_path / "s.duckdb", tmp_path / "o", NOW, ask)
    assert doc["status"] == "none" and ask.calls == []


# --------------------------------------------------------------------------- the prompt


def test_prompt_has_facts_and_every_item(env):
    db, out = env
    ask = fake(reply())
    dr.build_daily_read(db, out, NOW, ask)
    p = ask.calls[0]
    for needle in ("Stress step: stress since 2026-11-05", "Price driver: rates (window 21 sessions)", MARKET["summary"],
                   "21 sessions -4.0", "VIX: 24.5", "2-year yield: 4.10%", "63 sessions n/a", "energy (XLE) +3.2", "real_estate (XLRE) -4.1",
                   ECONOMY["summary"], EXPOSURES["summary"], "PREVIOUS READ\nnone", "unclear"):
        assert needle in p, needle
    for i in range(8):
        assert f"Headline number {i} about markets" in p
    assert p.index("MARKET TODAY") < p.index("ECONOMY") < p.index("MEASURED SENSITIVITIES") < p.index("PREVIOUS READ") < p.index("NEWS ITEMS")
    assert "RULES" in p and "WORD FOR WORD" in p


def test_missing_economy_and_exposures_stated_not_available(env):
    db, out = env
    (out / "economy_state.json").unlink()
    (out / "driver_exposures.json").write_text(json.dumps({**EXPOSURES, "degraded": True, "summary": None}), encoding="utf-8")
    ask = fake(reply())
    doc = dr.build_daily_read(db, out, NOW, ask)
    p = ask.calls[0]
    assert "ECONOMY\nnot available" in p and "MEASURED SENSITIVITIES\nnot available" in p
    assert any(r.startswith("economy_state_unavailable") for r in doc["reasons"])
    assert any(r.startswith("driver_exposures_unavailable") for r in doc["reasons"])
    assert doc["status"] == "fresh"


# --------------------------------------------------------------------------- parse_reply


def test_parse_reply_good_and_wrapped():
    got, why = dr.parse_reply("Here you go:\n```json\n" + reply() + "\n```")
    assert why == "" and got["driver"] == "rates" and len(got["quotes"]) == 2
    got, _ = dr.parse_reply(reply(driver="sector:banks"))
    assert got["driver"] == "sector:banks"


@pytest.mark.parametrize("text", [
    "no json at all",
    "{not json",
    reply(driver="vibes"),
    reply(driver="sector:"),
    reply(why=""),
    reply(agrees_with_prices="maybe"),
    reply(exposed=[{"what": "x", "direction": "up", "reason": "r"}]),
    reply(exposed=[{"what": "x", "direction": "hurt", "reason": "r"}] * 6),
    reply(quotes="N1"),
    reply(quotes=[{"item": "N1"}]),
    json.dumps({"driver": "rates"}),
])
def test_parse_reply_rejects(text):
    got, why = dr.parse_reply(text)
    assert got is None and why


# --------------------------------------------------------------------------- check_quote


def test_check_quote():
    it = item()
    assert dr.check_quote("Yields climbed sharply today", it)  # title
    assert dr.check_quote("Investors sold bonds all day.", it)  # body, whitespace differs
    assert dr.check_quote("Yields   climbed\nsharply today", it)  # the quote's whitespace is collapsed too
    assert not dr.check_quote("Yields climbed steeply today", it)  # a changed word
    assert not dr.check_quote("yields climbed sharply today", it)  # case is kept
    assert not dr.check_quote("Yields climbed sharply", dict(it, title="x", body="y")), "not in the item"
    assert not dr.check_quote("Yields climbed", it)  # under 4 words
    assert not dr.check_quote("Yields climbed sharply today", None)  # not offered


# --------------------------------------------------------------------------- acceptance


def test_acceptance_needs_two_items(env):
    db, out = env
    one_item = [{"item": "N1", "text": "Treasury yields climbed sharply"}, {"item": "N1", "text": "as investors sold bonds"}]
    doc = dr.build_daily_read(db, out, NOW, fake(reply(quotes=one_item)))
    assert doc["status"] == "none" and doc["read"] is None
    assert "1 items" in doc["reasons"][-1] or "from 1 items" in doc["reasons"][-1]
    two = dr.build_daily_read(db, out, NOW, fake(reply()))
    assert two["status"] == "fresh" and len(two["quotes"]) == 2 and two["quotes_rejected"] == 0
    assert two["quotes"][0]["item"] == "N1" and two["quotes"][0]["source_url"].startswith("https://x/")


def test_failed_quotes_dropped_and_counted(env):
    db, out = env
    quotes = [{"item": "N1", "text": "Treasury yields climbed sharply"}, {"item": "N2", "text": "as investors sold bonds"},
              {"item": "N3", "text": "a made up sentence entirely"}, {"item": "N99", "text": "Treasury yields climbed sharply"}]
    doc = dr.build_daily_read(db, out, NOW, fake(reply(quotes=quotes)))
    assert doc["status"] == "fresh" and len(doc["quotes"]) == 2 and doc["quotes_rejected"] == 2
    assert all(q["text"] in ("Treasury yields climbed sharply", "as investors sold bonds") for q in doc["quotes"])


def test_pinned_fact_check(env):
    db, out = env
    for why in ("The market is calm today. Yields rose.", "Markets are stable. Bonds fell.", "There is No Stress here. Yields rose."):
        doc = dr.build_daily_read(db, out, NOW, fake(reply(why=why)))
        assert doc["status"] == "none" and any("contradicts" in r for r in doc["reasons"]), why
    assert dr.check_why("The market is calm today.", "calm")  # the check binds only on stress or crisis
    assert not dr.check_why("calm", "crisis")


def test_unclear_accepted_with_no_quotes(env):
    db, out = env
    doc = dr.build_daily_read(db, out, NOW, fake(reply(driver="unclear", quotes=[], why="The items do not explain the move. Nothing stands out.")))
    assert doc["status"] == "fresh" and doc["quotes"] == [] and doc["read"]["driver"] == "unclear"
    assert doc["summary"].startswith("News read (2026-11-19): The items do not explain")
    assert doc["agree"] is None


def test_summary_sentence(env):
    db, out = env
    doc = dr.build_daily_read(db, out, NOW, fake(reply()))
    assert doc["summary"] == "News read (2026-11-19): Yields rose as bonds were sold. Stocks fell with them. Prices say: rates."
    assert doc["prices"] == {"stress_step": "stress", "driver": "rates", "backdrop": None}
    assert doc["agree"] is True and doc["items_offered"] == 8 and doc["model"] == "gemini-4-hydrogen-high"


# --------------------------------------------------------------------------- kept_previous


def test_kept_previous_and_five_day_limit(env):
    db, out = env
    first = dr.build_daily_read(db, out, NOW, fake(reply()))
    (out / dr.FILENAME).write_text(json.dumps(first), encoding="utf-8")
    # today's call fails; the fresh read of the same as_of is carried
    bad = dr.build_daily_read(db, out, NOW, fake(None, "agy: QUOTA"))
    assert bad["status"] == "kept_previous" and bad["carried_from"] == AS_OF and bad["detail"] == "agy: QUOTA"
    assert bad["read"] == first["read"] and bad["quotes"] == first["quotes"]
    (out / dr.FILENAME).write_text(json.dumps(bad), encoding="utf-8")
    # the next day: still carried from the original date, and the prompt names that date
    write_files(out, market={**MARKET, "asof": "2026-11-24"})
    ask = fake("garbage")
    nxt = dr.build_daily_read(db, out, NOW, ask)
    assert nxt["status"] == "kept_previous" and nxt["carried_from"] == AS_OF
    assert f"PREVIOUS READ\n{AS_OF} | driver: rates" in ask.calls[0]
    # six days after the fresh read: expired
    write_files(out, market={**MARKET, "asof": "2026-11-25"})
    gone = dr.build_daily_read(db, out, NOW, fake("garbage"))
    assert gone["status"] == "none" and gone["read"] is None and gone["quotes"] == []
    assert any(r.startswith("carry_expired") for r in gone["reasons"])


def test_no_previous_failed_call_is_none(env):
    db, out = env
    doc = dr.build_daily_read(db, out, NOW, fake(None, "RuntimeError: agy not found"))
    assert doc["status"] == "none" and doc["detail"] == "RuntimeError: agy not found"


# --------------------------------------------------------------------------- agree


@pytest.mark.parametrize("model,price,want", [
    ("rates", "rates", True), ("rates", "growth_credit", False),
    ("growth", "growth_credit", True), ("credit", "growth_credit", True),
    ("inflation", "inflation", True), ("inflation", "rates", False),
    ("sector:banks", "sector_led:banks", True), ("sector:banks", "rates", False),
    ("policy", "rates", False), ("earnings", "sector_led:tech", False),
    ("unclear", "rates", None), ("rates", "unclear", None), ("rates", "none", None), ("rates", None, None),
])
def test_agree(model, price, want):
    assert dr.agree(model, price) is want


# --------------------------------------------------------------------------- writing


def test_write_history_archive_no_nan(env):
    db, out = env
    main, archive = dr.write_daily_read(db, out, NOW, fake(reply()))
    assert main.read_text(encoding="utf-8") == archive.read_text(encoding="utf-8")
    assert archive == out / "archive" / "2026-11-20" / "daily_read" / "daily_read.json"
    json.loads(main.read_text(encoding="utf-8"), parse_constant=lambda c: pytest.fail(f"non-finite {c}"))
    dr.write_daily_read(db, out, NOW, fake(None, "agy: QUOTA"))  # a second run: kept_previous
    lines = [json.loads(ln) for ln in (out / dr.HISTORY_FILENAME).read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0] == {"as_of": AS_OF, "built_at": "2026-11-20T06:00:00Z", "status": "fresh", "driver": "rates", "agree": True,
                        "quotes_passed": 2, "quotes_rejected": 0, "detail": "agy: SUCCESS"}
    assert lines[1]["status"] == "kept_previous" and lines[1]["quotes_passed"] == 0 and lines[1]["detail"] == "agy: QUOTA"


def test_store_untouched_no_table_written(env):
    db, out = env

    def snapshot():
        con = duckdb.connect(str(db), read_only=True)
        try:
            return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for (t,) in con.execute("show tables").fetchall()}
        finally:
            con.close()

    before = snapshot()
    dr.write_daily_read(db, out, NOW, fake(reply()))
    assert snapshot() == before and set(before) == {"news_items", "news_classifications"}


def test_cli_with_fake_ask(env, monkeypatch):
    db, out = env
    real = dr.build_daily_read
    monkeypatch.setattr(dr, "build_daily_read", lambda db_path, outputs_dir, now=None, ask=None: real(db_path, outputs_dir, NOW, fake(reply())))
    res = CliRunner().invoke(app, ["write-daily-read", "--db-path", str(db), "--outputs-dir", str(out)])
    assert res.exit_code == 0, res.output
    assert json.loads((out / dr.FILENAME).read_text(encoding="utf-8"))["status"] == "fresh"
