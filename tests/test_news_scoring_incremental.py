"""N1 C1: incremental news scoring equals a full recompute, row for row.

Each scenario changes a store that has been scored incrementally, scores it
again incrementally, and compares every score table with a full rebuild of a
copy of the same store."""
from __future__ import annotations

import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml

from macro_engine.news.scoring import build_stored_news_scores
from macro_engine.news.selection import assign_event_ids
from macro_engine.storage.duckdb_store import DuckDBStore

START = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)  # a Monday
SOURCES = ["src_a", "src_b", "src_c"]
SECTORS = ["energy", "financials", "technology"]
THEMES = ["inflation_pressure", "growth_slowdown"]

_KEYS = {
    "news_daily_theme_scores": ["score_date", "theme_id"],
    "news_daily_sector_scores": ["score_date", "sector_id"],
    "news_weekly_theme_scores": ["week_start_date", "theme_id"],
    "news_weekly_sector_scores": ["week_start_date", "sector_id"],
    "news_score_components": ["score_date", "news_id", "component_type", "theme_id", "sector_id"],
}


def _item(news_id: str, day: int, text: str, source: str = "src_a") -> dict:
    published = START + timedelta(days=day)
    return {
        "news_id": news_id,
        "source": source,
        "source_url": f"https://example.invalid/{news_id}",
        "title": text.split(" ", 1)[0],
        "body": text,
        "published_at": published,
        "ingested_at": published,
        "provider": "rss",
        "raw_metadata": {},
        "content_hash": f"hash_{news_id}",
    }


def _classify(
    store: DuckDBStore,
    news_id: str,
    seed: int,
    *,
    status: str = "success",
    classified_at: datetime | None = None,
    strong_energy: bool = False,
) -> None:
    rng = random.Random(seed)
    classified_at = classified_at or START
    themes = [
        {
            "news_id": news_id,
            "theme_id": theme,
            "direction": rng.choice(["positive", "negative", "mixed"]),
            "severity": round(rng.uniform(0.02, 1.0), 3),
            "confidence": round(rng.uniform(0.05, 1.0), 3),
            "time_horizon": "short_term",
        }
        for theme in rng.sample(THEMES, rng.randint(1, 2))
    ]
    impacts = [
        {
            "news_id": news_id,
            "sector_id": sector,
            "impact_direction": rng.choice(["tailwind", "headwind"]),
            "impact_score": round(rng.uniform(-1.0, 1.0), 3),
            "confidence": round(rng.uniform(0.05, 1.0), 3),
            "rationale": f"r{seed}",
        }
        for sector in rng.sample(SECTORS, rng.randint(1, 3))
    ]
    if strong_energy:  # enough on its own to bind the event cap in energy
        impacts = [
            {"news_id": news_id, "sector_id": "energy", "impact_direction": "tailwind",
             "impact_score": 0.9, "confidence": 1.0, "rationale": "strong"}
        ]
    if status != "success":
        themes, impacts = [], []
    classification = {
        "classification_id": f"cls_{news_id}_{status}_{classified_at:%Y%m%d%H%M%S}",
        "news_id": news_id,
        "classified_at": classified_at,
        "ai_provider": "deepseek",
        "ai_model": "deepseek-chat",
        "macro_themes": [],
        "sector_impacts": [],
        "entities": [],
        "time_horizon": "short_term",
        "severity": (0.9 if strong_energy else round(rng.uniform(0.02, 1.0), 3))
        if status == "success"
        else None,
        "confidence": round(rng.uniform(0.05, 1.0), 3) if status == "success" else None,
        "summary": "s",
        "raw_ai_response": {},
        "classification_status": status,
        "error_message": None if status == "success" else "timeout",
    }
    store.write_news_classifications(
        pd.DataFrame([classification]),
        pd.DataFrame(themes, columns=list(themes[0]) if themes else ["news_id"]),
        pd.DataFrame(impacts, columns=list(impacts[0]) if impacts else ["news_id"]),
    )


def _config(tmp_path: Path, *, decay: bool) -> Path:
    path = tmp_path / f"news_scoring_{decay}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "news_scoring": {
                    "freshness_decay": {"enabled": decay, "half_life_days": 7, "max_age_days": 21},
                    "max_single_event_daily_contribution": 0.4,
                    "output_dir": str(tmp_path / "outputs"),
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _seed(db_path: Path) -> DuckDBStore:
    store = DuckDBStore(db_path)
    store.initialize()
    items = []
    for i in range(40):
        day = i
        items.append(_item(f"n{i:03d}", day, f"story{i} unique{i} detail{i} macro{i}", SOURCES[i % 3]))
    # Near-duplicate narratives on one day from several sources: one event each.
    for j, source in enumerate(SOURCES):
        items.append(_item(f"dup{j}", 10, "oil supply outage lifts crude energy prices", source))
    # Two stories that are separate events until an unclassified bridge arrives.
    items.append(_item("ev_b", 12, "alpha bravo charlie delta echo foxtrot golf hotel"))
    items.append(_item("ev_c", 12, "delta echo foxtrot golf hotel india juliet kilo"))
    store.upsert_news_items(pd.DataFrame(items))
    for i in range(0, 34):
        if i % 5 != 3:  # n003, n008, ... stay unclassified for the late scenario
            _classify(store, f"n{i:03d}", i)
    for j in range(3):
        _classify(store, f"dup{j}", 100 + j)
    _classify(store, "ev_b", 200, strong_energy=True)
    _classify(store, "ev_c", 201, strong_energy=True)
    return store


def _tables(db_path: Path) -> dict[str, pd.DataFrame]:
    store = DuckDBStore(db_path)
    tables = {}
    for table, keys in _KEYS.items():
        frame = store.read_table(table).drop(columns=["created_at"], errors="ignore")
        tables[table] = frame.sort_values(keys, na_position="first").reset_index(drop=True)
    runs = store.read_table("news_scoring_runs")
    tables["runs"] = runs[["status", "item_count", "scored_item_count", "error_message"]]
    return tables


def _created_at(db_path: Path) -> pd.DataFrame:
    frame = DuckDBStore(db_path).read_table("news_daily_sector_scores")
    return frame[["score_date", "sector_id", "created_at"]]


def _sorted(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(["score_date", "sector_id"]).reset_index(drop=True)


def _assert_matches_full(db_path: Path, config: Path, tmp_path: Path, label: str) -> None:
    incremental = build_stored_news_scores(config_path=config, db_path=db_path)
    reference = tmp_path / f"full_{label}.duckdb"
    shutil.copy(db_path, reference)
    build_stored_news_scores(config_path=config, db_path=reference, full=True)
    got, want = _tables(db_path), _tables(reference)
    for table in want:
        assert not want[table].empty, (label, table)
        pd.testing.assert_frame_equal(
            got[table], want[table], check_exact=False, rtol=1e-12, atol=0.0, obj=f"{label}:{table}"
        )
    assert incremental.runs.iloc[0]["scored_item_count"] == want["runs"].iloc[0]["scored_item_count"]


@pytest.mark.parametrize("decay", [True, False])
def test_incremental_scoring_equals_full_recompute(tmp_path: Path, decay: bool):
    config = _config(tmp_path, decay=decay)
    db_path = tmp_path / "macro.duckdb"
    store = _seed(db_path)

    # First build has no state: a full build.
    first = build_stored_news_scores(config_path=config, db_path=db_path)
    assert not first.daily_sector_scores.empty
    _assert_matches_full(db_path, config, tmp_path, "nothing_changed")

    # Nothing changed: nothing rewritten, every row keeps its created_at.
    before = _created_at(db_path)
    quiet = build_stored_news_scores(config_path=config, db_path=db_path)
    assert quiet.daily_sector_scores.empty and quiet.components.empty
    pd.testing.assert_frame_equal(_sorted(_created_at(db_path)), _sorted(before))

    # A normal day: the newest items get classified, the range grows by one item.
    before = _created_at(db_path)
    store.upsert_news_items(pd.DataFrame([_item("n040", 40, "story40 unique40 detail40")]))
    for i in (34, 35, 36, 37, 38, 39, 40):
        _classify(store, f"n{i:03d}", i)
    daily = build_stored_news_scores(config_path=config, db_path=db_path)
    if decay:
        # Old weeks are left alone: the first week's rows were not rewritten.
        first_week = pd.Timestamp(START.date()) + pd.Timedelta(days=6)
        kept = _created_at(db_path)
        kept = _sorted(kept[kept["score_date"] <= first_week])
        assert not kept.empty
        pd.testing.assert_frame_equal(kept, _sorted(before[before["score_date"] <= first_week]))
        assert 0 < len(daily.daily_sector_scores) < len(_created_at(db_path))
    _assert_matches_full(db_path, config, tmp_path, "daily")

    # A late classification for an old item.
    _classify(store, "n008", 8, classified_at=START + timedelta(days=45))
    _assert_matches_full(db_path, config, tmp_path, "late")

    # A retry: an error row, then a later success for the same item.
    _classify(store, "n013", 13, status="error", classified_at=START + timedelta(days=46))
    _assert_matches_full(db_path, config, tmp_path, "error_row")
    _classify(store, "n013", 13, classified_at=START + timedelta(days=47))
    _assert_matches_full(db_path, config, tmp_path, "retry_success")

    # An unclassified item that bridges two scored events changes their event ids.
    events_before = assign_event_ids(store.read_table("news_items"), similarity_threshold=0.6)
    bridge = _item("ev_a", 30, "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo")
    store.upsert_news_items(pd.DataFrame([bridge]))
    events_after = assign_event_ids(store.read_table("news_items"), similarity_threshold=0.6)
    assert events_before["ev_b"] != events_before["ev_c"]
    assert events_after["ev_b"] == events_after["ev_c"] == "ev_a"
    _assert_matches_full(db_path, config, tmp_path, "event_bridge")

    # An unclassified item past the end extends the range: older items age into the new days.
    store.upsert_news_items(pd.DataFrame([_item("n050", 50, "story50 unique50")]))
    _assert_matches_full(db_path, config, tmp_path, "range_extended")

    # An item leaves the store: the first item, so the range start moves too.
    with store._connect() as con:  # noqa: SLF001 - test setup
        for table in ("news_items", "news_classifications", "news_theme_scores", "news_sector_impacts"):
            con.execute(f"DELETE FROM {table} WHERE news_id = 'n000'")
    _assert_matches_full(db_path, config, tmp_path, "removed")


def test_config_change_and_error_force_full_rebuild(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "macro.duckdb"
    _seed(db_path)
    build_stored_news_scores(config_path=_config(tmp_path, decay=True), db_path=db_path)
    store = DuckDBStore(db_path)
    assert store.read_news_score_state() is not None

    # A different config cannot reuse the stored rows.
    changed = build_stored_news_scores(config_path=_config(tmp_path, decay=False), db_path=db_path)
    assert len(changed.daily_sector_scores) == len(store.read_table("news_daily_sector_scores"))

    # A failed build clears the state as well as the outputs.
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("macro_engine.news.scoring.build_news_scores", boom)
    with pytest.raises(RuntimeError):
        build_stored_news_scores(config_path=_config(tmp_path, decay=False), db_path=db_path)
    assert store.read_news_score_state() is None
