"""events-daily (Phase 5b): grouping modes, idempotent bytes, failure handling, freshness, firewall, CLI."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from test_events_firewall_hash import _build_inputs
from test_news_history import _classification, _item
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.events import daily
from macro_engine.events.daily import (
    NOTICE,
    REGISTER_FILENAME,
    RUN_FILENAME,
    load_grouping,
    run_events_daily,
)
from macro_engine.storage.duckdb_store import DuckDBStore

DAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
TITLES = {
    "n1": "Houthi missile attack hits Riyadh airport",
    "n2": "Central bank holds rates steady in surprise move",
    "n3": "Copper inventories fall to a decade low across exchanges",
    "n4": "Apple unveils a new laptop with faster chips",
}


@pytest.fixture(autouse=True)
def no_resolver(monkeypatch):
    def boom():
        raise FileNotFoundError("no screener data in tests")

    monkeypatch.setattr(daily, "load_resolver", boom)


@pytest.fixture()
def world(tmp_path):
    out, db = _build_inputs(tmp_path)   # protected artifacts + a raw_observations table in `db`
    store = DuckDBStore(db)
    store.initialize()
    rows = []
    for i, (nid, title) in enumerate(TITLES.items()):
        seen = datetime(2026, 10, 18, 1 + i, 0, 0)
        rows.append(_item(nid, title=title, published_at=datetime(2026, 10, 18, i, 30, 0),
                          ingested_at=seen, first_seen_at=seen))
    store.merge_news_items(pd.DataFrame(rows))
    for nid in ("n1", "n2", "n3"):   # n4 stays unclassified
        store.write_news_classifications(
            pd.DataFrame([_classification(f"c_{nid}", nid, classified_at=datetime(2026, 10, 18, 5, 0, tzinfo=UTC))]),
            pd.DataFrame(), pd.DataFrame(), origin="live")
    con = duckdb.connect(str(db))
    con.execute("UPDATE news_items SET source_url = 'https://www.federalreserve.gov/x' WHERE news_id = 'n2'")
    con.close()
    return tmp_path, out, db


def run(world, grouping="syndication", **kw):
    _, out, db = world
    args = {"db_path": db, "outputs_dir": out, "as_of_day": DAY, "grouping": grouping, "now": NOW}
    args.update(kw)
    return run_events_daily(**args)


def doc_of(out: Path):
    return json.loads((out / REGISTER_FILENAME).read_text(encoding="utf-8"))


def test_syndication_shape(world):
    _, out, _ = world
    code, rec = run(world)
    assert code == 0 and rec["status"] == "ok" and rec["firewall"]["ok"] is True
    doc = doc_of(out)
    assert doc["grouping_mode"] == "syndication" and doc["experimental"] is False
    assert "not confirmed events" in doc["notice"] and doc["notice"] == NOTICE
    assert doc["as_of_date"] == "2026-10-18" and doc["candidate_count"] == 4
    assert doc["linker"]["arm"] == "none"
    by_title = {c["title"]: c for c in doc["candidates"]}
    c = by_title[TITLES["n1"]]
    assert c["deduped_report_count"] == 1 and c["related_candidates"] == [] and c["storyline"] is None
    assert c["onset"] == "2026-10-18T00:30:00" and c["first_available_at"] == "2026-10-18T01:00:00"
    assert c["publisher_count"] == 1 and c["official_source"] is False
    assert c["corroboration"]["category"] == "single_source" and "single_source" in c["flags"]
    assert c["classification_status"]["classified_reports"] == 1
    # E2 (2026-10-22) fills the block; this fixture has no price series, so nothing is computed and nothing is guessed.
    mr = c["market_response"]
    assert mr["bond_data"] == "unavailable" and mr["company_checks"] == []
    assert all(chk["result"] in ("not_scored", "display_only", "data_unavailable") for chk in mr["checks"])
    assert "market_checks" in doc and "market_response_method" in doc
    assert c["themes"] and all(t["role"] == "annotation_only" for t in c["themes"])
    assert by_title[TITLES["n2"]]["official_source"] is True
    assert by_title[TITLES["n2"]]["corroboration"]["category"] == "officially_sourced"
    assert by_title[TITLES["n4"]]["classification_status"]["classified_reports"] == 0
    assert "unclassified_reports" in by_title[TITLES["n4"]]["flags"]
    assert "confirmed event" not in json.dumps(doc).lower().replace("not confirmed events", "")
    assert "entity_resolver_unavailable:FileNotFoundError" in doc["warnings"]
    assert rec["summary"]["candidates"] == 4 and rec["summary"]["single_source"] == 3
    assert rec["summary"]["unclassified_share"] == 0.25


def test_same_day_twice_is_byte_identical_and_archived(world):
    _, out, _ = world
    run(world)
    first = (out / REGISTER_FILENAME).read_bytes()
    code, rec = run(world)
    assert code == 0 and rec["register_changed"] is False
    assert (out / REGISTER_FILENAME).read_bytes() == first
    archived = out / "archive" / "2026-10-18" / "events_daily" / REGISTER_FILENAME
    assert archived.read_bytes() == first
    assert (out / RUN_FILENAME).exists()


def test_old_reports_are_closed_on_the_first_run_so_the_second_run_changes_nothing(world):
    _, out, db = world
    DuckDBStore(db).merge_news_items(pd.DataFrame([_item(
        "old1", title="Port strike halts container traffic on the west coast", published_at=datetime(2026, 10, 11, 8, 0),
        ingested_at=datetime(2026, 10, 12, 8, 0), first_seen_at=datetime(2026, 10, 12, 8, 0))]))
    run(world)
    first = (out / REGISTER_FILENAME).read_bytes()
    old = next(c for c in doc_of(out)["candidates"] if c["report_ids"] == ["old1"])
    assert old["status_window"]["status"] == "closed" and old["status_window"]["closed_on"] == "2026-10-18"
    code, rec = run(world)
    assert code == 0 and rec["register_changed"] is False and (out / REGISTER_FILENAME).read_bytes() == first


def test_stale_store_fails_without_touching_the_register(world):
    _, out, _ = world
    out.mkdir(exist_ok=True)
    (out / REGISTER_FILENAME).write_text('{"yesterday": true}\n', encoding="utf-8")
    code, rec = run(world, now=datetime(2026, 10, 25, tzinfo=UTC))
    assert code == 1 and rec["status"] == "failed"
    assert any(e.startswith("store_stale") for e in rec["errors"])
    assert (out / REGISTER_FILENAME).read_text(encoding="utf-8") == '{"yesterday": true}\n'
    saved = json.loads((out / RUN_FILENAME).read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and saved["previous_register_kept"] is True
    assert not (out / "archive").exists()


def test_stale_store_with_no_prior_register_writes_none(world):
    _, out, _ = world
    code, _ = run(world, now=datetime(2026, 10, 25, tzinfo=UTC))
    assert code == 1 and not (out / REGISTER_FILENAME).exists() and (out / RUN_FILENAME).exists()


def test_build_failure_keeps_yesterday_and_marks_failed(world, monkeypatch):
    _, out, _ = world
    run(world)
    before = (out / REGISTER_FILENAME).read_bytes()

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(daily, "_build", boom)
    code, rec = run(world, as_of_day=date(2026, 10, 19))
    assert code == 1 and rec["status"] == "failed" and "disk on fire" in rec["errors"][0]
    assert (out / REGISTER_FILENAME).read_bytes() == before
    assert json.loads((out / RUN_FILENAME).read_text(encoding="utf-8"))["status"] == "failed"
    assert not list(out.glob(".*.tmp"))


def test_firewall_violation_aborts_before_any_register_write(world, monkeypatch):
    _, out, _ = world
    real_build = daily._build

    def tamper(*a, **k):
        res = real_build(*a, **k)
        (out / "current_regime.json").write_text(json.dumps({"built_at": "t", "k": 2.0}), encoding="utf-8")
        return res

    monkeypatch.setattr(daily, "_build", tamper)
    code, rec = run(world)
    assert code == 1 and rec["firewall"]["ok"] is False
    assert "artifact changed: current_regime.json" in rec["firewall"]["differences"]
    assert not (out / REGISTER_FILENAME).exists()


def test_grouping_modes_are_selectable_and_experimental_ones_are_labelled(world, tmp_path):
    con = duckdb.connect(str(world[2]))
    from macro_engine.events.embeddings import EmbeddingStore

    EmbeddingStore(con)   # arm B reads the (empty) vector table
    con.close()
    for mode in ("arm_a", "arm_b"):
        # a fresh store per mode: one store holds one grouping mode
        db = tmp_path / f"{mode}.duckdb"
        db.write_bytes(Path(world[2]).read_bytes())
        out = world[1] / mode
        for name in ("current_regime.json", "current_sector_ranking.json", "cost_of_capital_anchor.json",
                     "long_run_growth_anchor.json", "sector_multiple_bands.json", "shock_register.json"):
            out.mkdir(exist_ok=True)
            (out / name).write_text(json.dumps({"built_at": "t", "k": 1.0}), encoding="utf-8")
        code, rec = run_events_daily(db_path=db, outputs_dir=out, as_of_day=DAY, grouping=mode, now=NOW)
        assert code == 0, rec["errors"]
        doc = json.loads((out / REGISTER_FILENAME).read_text(encoding="utf-8"))
        assert doc["grouping_mode"] == mode and doc["experimental"] is True
        assert "EXPERIMENTAL" in doc["notice"] and NOTICE in doc["notice"]
        assert doc["linker"]["arm"] == ("B" if mode == "arm_b" else "A")
        assert all("related_candidates" in c for c in doc["candidates"])


def test_one_store_one_grouping_mode(world):
    run(world)
    code, rec = run(world, grouping="arm_a")
    assert code == 1 and "written under grouping 'syndication'" in rec["errors"][0]


def test_syndication_refuses_a_store_that_holds_linker_groups(world, tmp_path):
    db = tmp_path / "linked.duckdb"
    db.write_bytes(Path(world[2]).read_bytes())
    out = world[1]
    code, _ = run_events_daily(db_path=db, outputs_dir=out, as_of_day=DAY, grouping="arm_a", now=NOW)
    assert code == 0
    # simulate a store from a linker run that joined two reports, with no mode recorded
    con = duckdb.connect(str(db))
    con.execute("DELETE FROM event_state_meta WHERE k = ?", [daily.MODE_META_KEY])
    ev = con.execute("SELECT event_id FROM event_state_events LIMIT 1").fetchone()[0]
    con.execute("UPDATE event_state_reports SET event_id = ?", [ev])
    con.close()
    code, rec = run_events_daily(db_path=db, outputs_dir=out, as_of_day=DAY, grouping="syndication", now=NOW)
    assert code == 1 and "multi-report candidates" in rec["errors"][0]


def test_a_report_classified_after_first_sight_shows_its_classification(world):
    _, out, db = world
    run(world)
    c4 = next(c for c in doc_of(out)["candidates"] if c["title"] == TITLES["n4"])
    assert c4["classification_status"]["classified_reports"] == 0
    DuckDBStore(db).write_news_classifications(
        pd.DataFrame([_classification("c_n4", "n4", classified_at=datetime(2026, 10, 18, 7, 0, tzinfo=UTC))]),
        pd.DataFrame(), pd.DataFrame(), origin="live")
    code, rec = run(world)
    assert code == 0 and rec["counts"]["details_refreshed"] == 1
    c4 = next(c for c in doc_of(out)["candidates"] if c["title"] == TITLES["n4"])
    assert c4["classification_status"]["classified_reports"] == 1
    assert rec["summary"]["unclassified_share"] == 0.0


def test_load_grouping_default_and_validation(tmp_path):
    assert load_grouping() == "syndication"
    bad = tmp_path / "c.yaml"
    bad.write_text("register:\n  grouping: bogus\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_grouping(bad)


def test_cli_exit_codes(world):
    _, out, db = world
    args = ["events-daily", "--db-path", str(db), "--outputs-dir", str(out), "--as-of", "2026-10-18",
            "--max-age-hours", "99999"]
    ok = CliRunner().invoke(app, args)
    assert ok.exit_code == 0, ok.output
    assert doc_of(out)["grouping_mode"] == "syndication"
    stale = CliRunner().invoke(app, ["events-daily", "--db-path", str(db), "--outputs-dir", str(out),
                                     "--as-of", "2026-10-18", "--max-age-hours", "0.001"])
    assert stale.exit_code == 1
    assert json.loads((out / RUN_FILENAME).read_text(encoding="utf-8"))["status"] == "failed"


def test_window_keeps_recent_candidates_and_says_how_many_it_left_out():
    from macro_engine.events.daily import window_candidates

    doc = {"candidate_count": 3, "candidates": [
        {"candidate_id": "evt_old", "last_report_at": "2026-09-01T10:00:00"},
        {"candidate_id": "evt_edge", "last_report_at": "2026-09-22T00:00:00"},
        {"candidate_id": "evt_new", "last_report_at": "2026-10-21T08:00:00"},
    ]}
    out = window_candidates(doc, date(2026, 10, 22), 30)
    assert [c["candidate_id"] for c in out["candidates"]] == ["evt_edge", "evt_new"]
    assert out["candidate_count"] == 2
    assert out["window"]["omitted_older"] == 1 and out["window"]["since"] == "2026-09-22"
