"""N1.6: news health -- gates that bite. Pure function, no I/O, no DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from macro_engine.news.health import compute_news_health


def _row(
    *,
    run_id: str,
    run_at: datetime,
    source_id: str,
    provider: str = "rss",
    source_group: str = "macro_general",
    status: str = "ok",
    items_fetched: int = 5,
    items_new: int = 5,
    newest_published_at: datetime | None = None,
    undated_count: int = 0,
    error: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "run_at": run_at,
        "source_id": source_id,
        "provider": provider,
        "source_group": source_group,
        "status": status,
        "items_fetched": items_fetched,
        "items_new": items_new,
        "newest_published_at": run_at if newest_published_at is None else newest_published_at,
        "undated_count": undated_count,
        "error": error,
        "elapsed_seconds": 1.0,
    }


def _history(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


BASE = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _base_kwargs(**overrides) -> dict:
    kwargs = dict(
        run_id="run_today",
        run_at=BASE,
        source_runs_history=pd.DataFrame(),
        stale_after_hours={"feed_a": 72},
        profile_groups={"macro_general": ["feed_a"]},
        classification_result=None,
        hydrate_result={},
        export_result={},
        store_was_cold=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_ok_status_with_no_data():
    result = compute_news_health(**_base_kwargs())
    assert result["status"] == "ok"
    assert result["reasons"] == []


def test_f1_news_blackout_when_zero_new_items_this_run():
    history = _history([_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=0)])
    result = compute_news_health(**_base_kwargs(source_runs_history=history))
    assert result["status"] == "failed"
    assert "news_blackout" in result["reasons"]


def test_f1_does_not_fire_when_ingestion_did_not_run_this_run():
    # No rows at all for run_id "run_today" -- ingestion was skipped, not blacked out.
    history = _history([_row(run_id="run_yesterday", run_at=BASE - timedelta(days=1), source_id="feed_a")])
    result = compute_news_health(**_base_kwargs(source_runs_history=history))
    assert result["status"] == "ok"


def test_f4_cold_hydrate_failed():
    result = compute_news_health(
        **_base_kwargs(
            store_was_cold=True,
            hydrate_result={"manifest_totals": {"items": 100}, "errors": ["snapshot_partition_corrupt:x"]},
        )
    )
    assert result["status"] == "failed"
    assert "cold_hydrate_failed" in result["reasons"]


def test_f4_does_not_fire_when_store_was_not_cold():
    result = compute_news_health(
        **_base_kwargs(
            store_was_cold=False,
            hydrate_result={"manifest_totals": {"items": 100}, "errors": ["snapshot_partition_corrupt:x"]},
        )
    )
    assert "cold_hydrate_failed" not in result["reasons"]


def test_d1_source_dead_via_consecutive_bad_runs():
    rows = [
        _row(run_id=f"run_{i}", run_at=BASE - timedelta(days=3 - i), source_id="feed_a", status="empty", items_new=0)
        for i in range(3)
    ]
    rows.append(_row(run_id="run_today", run_at=BASE, source_id="feed_a", status="empty", items_new=0))
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(source_runs_history=history, profile_groups={"macro_general": ["feed_a"]})
    )
    assert result["status"] == "failed"  # also trips F1 (zero new items this run)
    assert "source_dead:feed_a" in result["reasons"]
    source_row = next(s for s in result["sources"] if s["source_id"] == "feed_a")
    assert source_row["dead"] is True
    assert source_row["consecutive_bad_runs"] == 4


def test_d1_source_dead_via_stale_after_hours():
    old = BASE - timedelta(hours=200)
    rows = [
        _row(run_id="run_old", run_at=old, source_id="feed_a", status="ok", items_new=3, newest_published_at=old),
        # Genuinely stale: today's fetch still reports the same old item -- no
        # new items, and no evidence of anything published more recently.
        _row(run_id="run_today", run_at=BASE, source_id="feed_a", status="ok", items_new=0, newest_published_at=old),
        # A second source keeps the run from being an F1 blackout.
        _row(run_id="run_today", run_at=BASE, source_id="feed_b", status="ok", items_new=2),
    ]
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"feed_a": 72, "feed_b": 72},
            profile_groups={"macro_general": ["feed_a", "feed_b"]},
        )
    )
    assert "source_dead:feed_a" in result["reasons"]
    assert result["status"] == "degraded"


def test_n16_fix_cold_run_with_fresh_published_evidence_is_not_dead():
    """N1.6 fix: reproduces the fed_press_rss row from the 2026-09-23 cold-start
    run -- a source's very first history row, 5 items fetched, none of them
    new (a cold history has nothing to be "new" relative to yet), newest item
    published 22h before run_at. 22h is well inside any real
    stale_after_hours threshold, so this must not be flagged dead."""
    published = BASE - timedelta(hours=22)
    rows = [
        _row(
            run_id="run_today", run_at=BASE, source_id="fed_press_rss",
            status="ok", items_fetched=5, items_new=0, newest_published_at=published,
        ),
        _row(run_id="run_today", run_at=BASE, source_id="feed_b", items_new=2),
    ]
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"fed_press_rss": 336, "feed_b": 72},
            profile_groups={"macro_general": ["fed_press_rss", "feed_b"]},
        )
    )
    source_row = next(s for s in result["sources"] if s["source_id"] == "fed_press_rss")
    assert source_row["dead"] is False
    assert "source_dead:fed_press_rss" not in result["reasons"]


def test_n16_fix_stale_published_evidence_with_no_new_items_is_dead():
    old_published = BASE - timedelta(hours=200)
    rows = [
        _row(
            run_id="run_a", run_at=BASE - timedelta(hours=100), source_id="feed_a",
            status="ok", items_new=0, newest_published_at=old_published,
        ),
        _row(
            run_id="run_today", run_at=BASE, source_id="feed_a",
            status="ok", items_new=0, newest_published_at=old_published,
        ),
        _row(run_id="run_today", run_at=BASE, source_id="feed_b", items_new=2),
    ]
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"feed_a": 72, "feed_b": 72},
            profile_groups={"macro_general": ["feed_a", "feed_b"]},
        )
    )
    source_row = next(s for s in result["sources"] if s["source_id"] == "feed_a")
    assert source_row["dead"] is True
    assert "source_dead:feed_a" in result["reasons"]


def test_n16_fix_cold_empty_source_not_dead_on_first_run():
    rows = [
        _row(
            run_id="run_today", run_at=BASE, source_id="feed_a",
            status="empty", items_fetched=0, items_new=0, newest_published_at=pd.NaT,
        ),
        _row(run_id="run_today", run_at=BASE, source_id="feed_b", items_new=2),
    ]
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"feed_a": 72, "feed_b": 72},
            profile_groups={"macro_general": ["feed_a", "feed_b"]},
        )
    )
    source_row = next(s for s in result["sources"] if s["source_id"] == "feed_a")
    assert source_row["dead"] is False
    assert source_row["consecutive_bad_runs"] == 1


def test_n16_fix_cold_empty_source_dead_after_consecutive_bad_streak():
    rows = [
        _row(
            run_id=f"run_{i}", run_at=BASE - timedelta(hours=3 - i), source_id="feed_a",
            status="empty", items_fetched=0, items_new=0, newest_published_at=pd.NaT,
        )
        for i in range(3)
    ]
    rows.append(
        _row(
            run_id="run_today", run_at=BASE, source_id="feed_a",
            status="empty", items_fetched=0, items_new=0, newest_published_at=pd.NaT,
        )
    )
    rows.append(_row(run_id="run_today", run_at=BASE, source_id="feed_b", items_new=2))
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"feed_a": 72, "feed_b": 72},
            profile_groups={"macro_general": ["feed_a", "feed_b"]},
        )
    )
    source_row = next(s for s in result["sources"] if s["source_id"] == "feed_a")
    assert source_row["dead"] is True
    assert "source_dead:feed_a" in result["reasons"]


def test_n16_fix_undated_only_source_within_threshold_span_not_dead():
    """eia_energy_rss-shaped: every item is undated, so newest_published_at is
    always NaT and there is no items_new evidence either. With no freshness
    evidence at all, a cold source is judged by how long we have been
    watching it (spec rule 2), not flagged dead outright."""
    rows = [
        _row(
            run_id="run_early", run_at=BASE - timedelta(hours=48), source_id="eia_energy_rss",
            status="ok", items_fetched=12, items_new=0, undated_count=12, newest_published_at=pd.NaT,
        ),
        _row(
            run_id="run_today", run_at=BASE, source_id="eia_energy_rss",
            status="ok", items_fetched=12, items_new=0, undated_count=12, newest_published_at=pd.NaT,
        ),
        _row(run_id="run_today", run_at=BASE, source_id="feed_b", items_new=2),
    ]
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"eia_energy_rss": 336, "feed_b": 72},
            profile_groups={"macro_general": ["eia_energy_rss", "feed_b"]},
        )
    )
    source_row = next(s for s in result["sources"] if s["source_id"] == "eia_energy_rss")
    assert source_row["dead"] is False
    assert "source_dead:eia_energy_rss" not in result["reasons"]


def test_n16_fix_no_evidence_history_beyond_threshold_span_is_dead():
    """Same undated-only shape, but the history now spans more than
    stale_after_hours with no evidence ever seen -- rule 2's own threshold
    bites, independent of the consecutive-bad-runs leg (status stays 'ok')."""
    rows = [
        _row(
            run_id="run_early", run_at=BASE - timedelta(hours=400), source_id="eia_energy_rss",
            status="ok", items_fetched=12, items_new=0, undated_count=12, newest_published_at=pd.NaT,
        ),
        _row(
            run_id="run_today", run_at=BASE, source_id="eia_energy_rss",
            status="ok", items_fetched=12, items_new=0, undated_count=12, newest_published_at=pd.NaT,
        ),
        _row(run_id="run_today", run_at=BASE, source_id="feed_b", items_new=2),
    ]
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            stale_after_hours={"eia_energy_rss": 336, "feed_b": 72},
            profile_groups={"macro_general": ["eia_energy_rss", "feed_b"]},
        )
    )
    source_row = next(s for s in result["sources"] if s["source_id"] == "eia_energy_rss")
    assert source_row["dead"] is True
    assert "source_dead:eia_energy_rss" in result["reasons"]


def test_d2_gdelt_dead_after_seven_consecutive_bad_runs():
    rows = []
    for i in range(7):
        rows.append(
            _row(
                run_id=f"run_{i}",
                run_at=BASE - timedelta(days=7 - i),
                source_id="gdelt_x",
                provider="gdelt",
                status="rate_limited",
                items_new=0,
            )
        )
    rows.append(_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=3))
    history = _history(rows)
    result = compute_news_health(**_base_kwargs(source_runs_history=history))
    assert "gdelt_dead" in result["reasons"]
    assert result["status"] == "degraded"


def test_d2_does_not_fire_with_fewer_than_seven_bad_runs():
    rows = [
        _row(run_id=f"run_{i}", run_at=BASE - timedelta(days=3 - i), source_id="gdelt_x", provider="gdelt", status="rate_limited", items_new=0)
        for i in range(3)
    ]
    rows.append(_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=3))
    history = _history(rows)
    result = compute_news_health(**_base_kwargs(source_runs_history=history))
    assert "gdelt_dead" not in result["reasons"]


def test_d3_group_uncovered_when_every_group_source_is_dead():
    rows = [
        _row(run_id=f"run_{i}", run_at=BASE - timedelta(days=3 - i), source_id="feed_a", status="empty", items_new=0)
        for i in range(3)
    ]
    rows.append(_row(run_id="run_today", run_at=BASE, source_id="feed_a", status="empty", items_new=0))
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(source_runs_history=history, profile_groups={"macro_general": ["feed_a"]})
    )
    assert "group_uncovered:macro_general" in result["reasons"]
    group_row = next(g for g in result["groups"] if g["group"] == "macro_general")
    assert group_row["uncovered"] is True


def test_d4_history_export_and_hydrate_errors_are_forwarded_as_degrade_reasons():
    history = _history([_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=2)])
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            hydrate_result={"errors": ["snapshot_partition_corrupt:items/2026/09/items_2026-09-01.parquet"]},
            export_result={"errors": ["export_refused_shrink:classifications/2026/09/foo.parquet"]},
        )
    )
    assert result["status"] == "degraded"
    assert any(r.startswith("snapshot_partition_corrupt:") for r in result["reasons"])
    assert any(r.startswith("export_refused_shrink:") for r in result["reasons"])


def test_d5_classification_failure_rate_high():
    history = _history([_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=5)])
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            classification_result={
                "selected_count": 20, "completed_count": 20, "failed_count": 3, "deadline_hit": False,
            },
        )
    )
    assert "classification_failure_rate_high" in result["reasons"]
    assert result["status"] == "degraded"


def test_d5_does_not_fire_under_ten_attempts():
    history = _history([_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=5)])
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            classification_result={
                "selected_count": 5, "completed_count": 5, "failed_count": 5, "deadline_hit": False,
            },
        )
    )
    assert "classification_failure_rate_high" not in result["reasons"]


def test_n1_gdelt_rate_limited_note_does_not_change_status():
    history = _history(
        [
            _row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=5),
            _row(run_id="run_today", run_at=BASE, source_id="gdelt_1", provider="gdelt", status="rate_limited", items_new=0),
            _row(run_id="run_today", run_at=BASE, source_id="gdelt_2", provider="gdelt", status="circuit_open", items_new=0),
        ]
    )
    result = compute_news_health(**_base_kwargs(source_runs_history=history))
    assert result["status"] == "ok"
    assert any(n.startswith("gdelt_rate_limited:2/2") for n in result["notes"])


def test_n2_classification_backlog_promoted_to_degrade_after_oa3():
    history = _history([_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=5)])
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            classification_result={
                "selected_count": 60, "completed_count": 15, "failed_count": 1, "deadline_hit": False,
            },
        )
    )
    assert "classification_backlog" in result["reasons"]
    assert result["status"] == "degraded"


def test_n3_group_concentration_note():
    history = _history(
        [
            _row(run_id="run_today", run_at=BASE, source_id="feed_a", source_group="macro_general", items_new=90),
            _row(run_id="run_today", run_at=BASE, source_id="feed_b", source_group="labor", items_new=10),
        ]
    )
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            profile_groups={"macro_general": ["feed_a"], "labor": ["feed_b"]},
        )
    )
    assert any(n.startswith("group_concentration:macro_general") for n in result["notes"])
    assert result["status"] == "ok"


def test_n4_undated_items_note():
    history = _history(
        [_row(run_id="run_today", run_at=BASE, source_id="feed_a", items_new=5, undated_count=18)]
    )
    result = compute_news_health(**_base_kwargs(source_runs_history=history))
    assert "undated_items:feed_a" in result["notes"]


def test_replay_of_35829508938_telemetry_is_degraded():
    """Numbers (§N1.6): 3 consecutive runs of marketwatch_pulse empty, plus 4/6
    GDELT sources rate-limited this run, gives status degraded with reason
    source_dead:marketwatch_pulse_rss and note gdelt_rate_limited:4/6."""
    rows = []
    for i in range(3):
        rows.append(
            _row(
                run_id=f"run_{i}",
                run_at=BASE - timedelta(days=3 - i),
                source_id="marketwatch_pulse_rss",
                status="empty",
                items_new=0,
            )
        )
    rows.append(
        _row(run_id="run_today", run_at=BASE, source_id="marketwatch_pulse_rss", status="empty", items_new=0)
    )
    rows.append(_row(run_id="run_today", run_at=BASE, source_id="cnbc_markets_rss", items_new=12))
    for i in range(4):
        rows.append(
            _row(
                run_id="run_today", run_at=BASE, source_id=f"gdelt_{i}", provider="gdelt",
                status="rate_limited", items_new=0,
            )
        )
    for i in range(2):
        rows.append(
            _row(
                run_id="run_today", run_at=BASE, source_id=f"gdelt_ok_{i}", provider="gdelt",
                status="ok", items_new=1,
            )
        )
    history = _history(rows)
    result = compute_news_health(
        **_base_kwargs(
            source_runs_history=history,
            profile_groups={"macro_general": ["marketwatch_pulse_rss", "cnbc_markets_rss"]},
        )
    )
    assert result["status"] == "degraded"
    assert "source_dead:marketwatch_pulse_rss" in result["reasons"]
    assert any(n.startswith("gdelt_rate_limited:4/6") for n in result["notes"])
