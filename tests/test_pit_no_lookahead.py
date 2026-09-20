"""Point-in-time (as-known) integrity.

The claim under test: an evaluation date D must never see a value that was first
published after D. It is proven on the two revision patterns that actually break
calendar as-of — a benchmark revision (PAYEMS-scale, where an entire history is
rewritten) and a re-seasonalisation (CPI-scale, where the base is rewritten).
"""

from __future__ import annotations

import pandas as pd
import pytest

from macro_engine.evaluation.asof import (
    build_publication_index,
    latest_observation_on_or_before_date,
    point_in_time_observation,
    point_in_time_series,
    resolve_asof_observation,
)

PAYEMS_PRE_REVISION = pd.DataFrame(
    {
        "series_id": ["PAYEMS", "PAYEMS", "PAYEMS"],
        "date": pd.to_datetime(["2024-03-01", "2024-04-01", "2024-05-01"]),
        "value": [156_000.0, 156_200.0, 156_400.0],
        "realtime_start": pd.to_datetime(["2024-06-07"] * 3),
        "realtime_end": pd.to_datetime(["2024-12-31"] * 3),
    }
)
# The benchmark revision lands in Feb 2025 and rewrites the SAME observation dates.
PAYEMS_POST_REVISION = pd.DataFrame(
    {
        "series_id": ["PAYEMS", "PAYEMS", "PAYEMS"],
        "date": pd.to_datetime(["2024-03-01", "2024-04-01", "2024-05-01"]),
        "value": [155_000.0, 155_150.0, 155_300.0],
        "realtime_start": pd.to_datetime(["2025-02-07"] * 3),
        "realtime_end": pd.to_datetime(["9999-12-31"] * 3),
    }
)


def _vintages() -> pd.DataFrame:
    return pd.concat([PAYEMS_PRE_REVISION, PAYEMS_POST_REVISION], ignore_index=True)


def test_multi_series_vintages_without_a_series_id_refuse_to_answer():
    """Regression: the resolver used to answer from whichever series sorted last.

    The anchor builders handed point-in-time mode the WHOLE `raw_observation_vintages` table
    (~20 series sharing one monthly as-of calendar) and never named a series, while every
    calendar-mode caller filtered its own first. Measured consequence: point-in-time mode
    published **294.43 as the 10-year nominal Treasury yield**, and an ERP of -294.33 derived
    from it. Every value was well-formed, so nothing downstream could tell it was wrong.

    Naming the series must give the named series; not naming it must stop, not guess.
    """
    frame = pd.concat(
        [
            PAYEMS_PRE_REVISION,
            pd.DataFrame(
                {
                    "series_id": ["DGS10"] * 3,
                    "date": pd.to_datetime(["2024-03-01", "2024-04-01", "2024-05-01"]),
                    "value": [4.20, 4.50, 4.80],
                    "realtime_start": pd.to_datetime(["2024-06-07"] * 3),
                    "realtime_end": pd.to_datetime(["9999-12-31"] * 3),
                }
            ),
        ],
        ignore_index=True,
    )

    with pytest.raises(ValueError, match="no series_id"):
        point_in_time_observation(frame, "2024-06-15")
    with pytest.raises(ValueError, match="no series_id"):
        point_in_time_series(frame, "2024-06-15")
    with pytest.raises(ValueError, match="no series_id"):
        resolve_asof_observation(
            mode="point_in_time", observations=frame, as_of="2024-06-15", vintages=frame
        )

    named = point_in_time_observation(frame, "2024-06-15", series_id="DGS10")
    assert named is not None
    assert float(named["value"]) == pytest.approx(4.80)
    payems = point_in_time_series(frame, "2024-06-15", series_id="PAYEMS")
    assert set(payems["value"]) == {156_000.0, 156_200.0, 156_400.0}


def test_a_single_series_frame_still_answers_without_a_series_id():
    """The guard must not break the ordinary one-series call (it did not need naming before)."""
    row = point_in_time_observation(_vintages(), "2024-12-15")
    assert row is not None
    assert float(row["value"]) == pytest.approx(156_400.0)


def test_as_of_before_the_revision_sees_the_as_published_value():
    """The whole point: a vintage read must not import the future revision."""
    row = point_in_time_observation(_vintages(), pd.Timestamp("2024-12-15"))
    assert row is not None
    assert pd.Timestamp(row["date"]) == pd.Timestamp("2024-05-01")
    assert float(row["value"]) == pytest.approx(156_400.0)


def test_as_of_after_the_revision_sees_the_revised_value():
    row = point_in_time_observation(_vintages(), pd.Timestamp("2025-06-30"))
    assert row is not None
    assert pd.Timestamp(row["date"]) == pd.Timestamp("2024-05-01")
    assert float(row["value"]) == pytest.approx(155_300.0)


def test_revision_is_invisible_before_it_happened_and_visible_after():
    """The two reads differ by exactly the revision, which is what makes it provable."""
    before = float(point_in_time_observation(_vintages(), "2024-12-15")["value"])
    after = float(point_in_time_observation(_vintages(), "2025-06-30")["value"])
    assert before - after == pytest.approx(1_100.0)


def test_no_lookahead_when_the_series_did_not_yet_exist():
    """Before the first vintage there is no value — not the earliest known one."""
    assert point_in_time_observation(_vintages(), pd.Timestamp("2024-01-01")) is None


def test_calendar_rule_would_have_leaked_the_revision():
    """Contrast case: the calendar rule cannot distinguish the two vintages at all.

    This is the defect the PIT mode exists to remove, so the test asserts the leak
    rather than pretending the old rule was safe.
    """
    observations = pd.DataFrame(
        {
            "series_id": ["PAYEMS"] * 3,
            "date": pd.to_datetime(["2024-03-01", "2024-04-01", "2024-05-01"]),
            "value": [155_000.0, 155_150.0, 155_300.0],  # only the revised history is stored
        }
    )
    leaked = latest_observation_on_or_before_date(observations, pd.Timestamp("2024-12-15"))
    assert float(leaked["value"]) == pytest.approx(155_300.0)  # a value not yet published


def test_reseasonalisation_rewrites_the_base_and_the_prior_year_leg():
    """CPI-style: as-published yoy must use the as-published base, not a revised one."""
    vintages = pd.DataFrame(
        {
            "series_id": ["CPIAUCSL"] * 4,
            "date": pd.to_datetime(["2024-04-01", "2025-04-01", "2024-04-01", "2025-04-01"]),
            "value": [300.0, 312.0, 298.0, 309.9],
            "realtime_start": pd.to_datetime(
                ["2025-05-13", "2025-05-13", "2026-02-11", "2026-02-11"]
            ),
            "realtime_end": pd.to_datetime(["2026-02-10", "2026-02-10", "9999-12-31", "9999-12-31"]),
        }
    )
    as_published = point_in_time_series(vintages, pd.Timestamp("2025-06-01"))
    revised = point_in_time_series(vintages, pd.Timestamp("2026-06-01"))
    published_yoy = float(as_published.iloc[-1]["value"]) / float(as_published.iloc[0]["value"]) - 1
    revised_yoy = float(revised.iloc[-1]["value"]) / float(revised.iloc[0]["value"]) - 1
    assert published_yoy == pytest.approx(0.04)
    assert revised_yoy == pytest.approx(0.0399, abs=5e-5)


def test_point_in_time_works_between_backfilled_dates():
    """An evaluation date that is NOT an exact backfilled vintage date must still resolve.

    ALFRED clips realtime_end to the query's realtime_end, so a vintage fetched for one day
    comes back with realtime_start == realtime_end == that day. A bracket test
    (`realtime_start <= D <= realtime_end`) therefore matched only the exact backfilled dates
    and silently returned nothing for every other day. The rule is "newest vintage at or
    before D".
    """
    vintages = pd.DataFrame(
        {
            "series_id": ["DGS10"] * 2,
            "date": pd.to_datetime(["2024-05-01", "2024-05-02"]),
            "value": [4.50, 4.55],
            "realtime_start": pd.to_datetime(["2024-05-01", "2024-05-01"]),
            "realtime_end": pd.to_datetime(["2024-05-01", "2024-05-01"]),
        }
    )
    # A date strictly between two backfilled vintages (2024-05-01 and 2024-06-01).
    row = point_in_time_observation(vintages, pd.Timestamp("2024-05-15"))
    assert row is not None
    assert float(row["value"]) == pytest.approx(4.55)
    assert pd.Timestamp(row["date"]) == pd.Timestamp("2024-05-02")


def test_the_newer_vintage_supersedes_for_a_later_as_of():
    vintages = pd.DataFrame(
        {
            "series_id": ["DGS10"] * 2,
            "date": pd.to_datetime(["2024-05-01", "2024-05-01"]),
            "value": [4.50, 4.80],  # same period, revised
            "realtime_start": pd.to_datetime(["2024-05-01", "2024-06-01"]),
            "realtime_end": pd.to_datetime(["2024-05-01", "2024-06-01"]),
        }
    )
    assert float(point_in_time_observation(vintages, "2024-05-15")["value"]) == pytest.approx(4.50)
    assert float(point_in_time_observation(vintages, "2024-06-15")["value"]) == pytest.approx(4.80)


def test_publication_index_records_the_first_date_each_observation_was_known():
    vintages = pd.concat(
        [
            pd.DataFrame(
                {
                    "series_id": ["PAYEMS"] * 2,
                    "date": pd.to_datetime(["2024-03-01", "2024-04-01"]),
                    "value": [1.0, 2.0],
                    "realtime_start": pd.to_datetime(["2024-06-07"] * 2),
                    "realtime_end": pd.to_datetime(["2024-07-04", "9999-12-31"]),
                }
            ),
            pd.DataFrame(
                {
                    "series_id": ["PAYEMS"],
                    "date": pd.to_datetime(["2024-04-01"]),
                    "value": [2.5],
                    "realtime_start": pd.to_datetime(["2025-02-07"]),
                    "realtime_end": pd.to_datetime(["9999-12-31"]),
                }
            ),
        ],
        ignore_index=True,
    )
    index = build_publication_index(vintages)
    mapping = {
        pd.Timestamp(row.date).date().isoformat(): pd.Timestamp(row.first_known_date)
        for row in index.itertuples(index=False)
    }
    # March 2024 was FIRST known in June 2024, even though it stayed current into July.
    assert mapping["2024-03-01"] == pd.Timestamp("2024-06-07")
    # April 2024 was first known in June 2024, not at its February 2025 revision.
    assert mapping["2024-04-01"] == pd.Timestamp("2024-06-07")


def test_point_in_time_mode_without_vintages_fails_loudly():
    """An opted-in PIT read that cannot be evidenced must not silently approximate."""
    row, reason = resolve_asof_observation(
        mode="point_in_time",
        observations=pd.DataFrame(
            {"series_id": ["X"], "date": pd.to_datetime(["2024-01-01"]), "value": [1.0]}
        ),
        as_of=pd.Timestamp("2024-06-01"),
        vintages=None,
    )
    assert row is None
    assert reason == "pit_vintage_missing"


def test_calendar_mode_is_unchanged_by_the_new_module():
    observations = pd.DataFrame(
        {
            "series_id": ["X"] * 3,
            "date": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
            "value": [1.0, 2.0, 3.0],
        }
    )
    row = latest_observation_on_or_before_date(
        observations, pd.Timestamp("2024-02-15"), publication_lag_days=0
    )
    assert pd.Timestamp(row["date"]) == pd.Timestamp("2024-02-01")
    # The lag still pushes visibility back, exactly as before.
    lagged = latest_observation_on_or_before_date(
        observations, pd.Timestamp("2024-02-15"), publication_lag_days=20
    )
    assert pd.Timestamp(lagged["date"]) == pd.Timestamp("2024-01-01")


# ── Backfill resumption ──────────────────────────────────────────────────────────────────────
# A full-history ALFRED backfill is thousands of rate-limited requests. The first version held
# every series in memory and wrote once at the end, so being killed -- which rate limiting makes
# likely -- discarded hours of work. These pin the two properties that fix it.


class _CountingClient:
    """FredClient stand-in that records every vintage request it is asked to make."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_series_observations_vintage(self, series_id, as_of, observation_start=None, observation_end=None):
        self.calls.append((series_id, as_of))
        return pd.DataFrame(
            {
                "series_id": [series_id],
                "date": [pd.Timestamp(as_of)],
                "value": [1.0],
                "realtime_start": [pd.Timestamp(as_of)],
                "realtime_end": [pd.Timestamp(as_of)],
            }
        )


def _sources_file(tmp_path, series_ids):
    path = tmp_path / "sources.yaml"
    body = "sources:\n" + "".join(
        f"  - series_id: {sid}\n    name: {sid}\n    provider: FRED\n    dimension: test\n"
        "    frequency: monthly\n    required: false\n    enabled: true\n"
        "    stale_after_days: 45\n    unusable_after_days: 120\n    publication_lag_days: 0\n"
        for sid in series_ids
    )
    path.write_text(body, encoding="utf-8")
    return path


def test_backfill_writes_each_series_before_moving_on(tmp_path):
    """Progress must persist per series, not only at the end of the whole run."""
    from macro_engine.ingest.service import run_fred_vintage_ingestion
    from macro_engine.storage.duckdb_store import DuckDBStore

    db = tmp_path / "macro.duckdb"
    config = _sources_file(tmp_path, ["AAA", "BBB"])
    client = _CountingClient()

    summary = run_fred_vintage_ingestion(
        as_of_dates=["2020-01-01", "2020-02-01"],
        config_path=config,
        db_path=db,
        parquet_dir=tmp_path / "parquet",
        client=client,
    )
    assert summary.vintage_rows == 4
    assert set(summary.series_stored) == {"AAA", "BBB"}
    stored = DuckDBStore(db).read_raw_observation_vintages()
    assert set(stored["series_id"]) == {"AAA", "BBB"}


def test_resume_skips_pairs_already_stored(tmp_path):
    """A restart must cost only the work that had not completed."""
    from macro_engine.ingest.service import run_fred_vintage_ingestion
    from macro_engine.storage.duckdb_store import DuckDBStore

    db = tmp_path / "macro.duckdb"
    config = _sources_file(tmp_path, ["AAA"])
    dates = ["2020-01-01", "2020-02-01", "2020-03-01"]

    first = _CountingClient()
    run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=first,
    )
    assert len(first.calls) == 3

    second = _CountingClient()
    summary = run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=second,
    )
    assert second.calls == [], "resume re-fetched work that was already stored"
    assert summary.skipped_pairs == 3
    assert summary.vintage_rows == 0
    # The store still holds exactly one row per (series, date) -- resumption is not lossy.
    assert len(DuckDBStore(db).read_raw_observation_vintages()) == 3


def test_resume_can_be_disabled(tmp_path):
    from macro_engine.ingest.service import run_fred_vintage_ingestion

    db = tmp_path / "macro.duckdb"
    config = _sources_file(tmp_path, ["AAA"])
    dates = ["2020-01-01"]

    run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=_CountingClient(),
    )
    forced = _CountingClient()
    run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=forced, resume=False,
    )
    assert len(forced.calls) == 1


def test_a_partially_fetched_series_resumes_mid_way(tmp_path):
    """The unit of work is (series, as-of), so a series interrupted halfway resumes halfway."""
    from macro_engine.ingest.service import run_fred_vintage_ingestion
    from macro_engine.storage.duckdb_store import DuckDBStore

    db = tmp_path / "macro.duckdb"
    config = _sources_file(tmp_path, ["AAA"])
    run_fred_vintage_ingestion(
        as_of_dates=["2020-01-01"], config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=_CountingClient(),
    )
    top_up = _CountingClient()
    summary = run_fred_vintage_ingestion(
        as_of_dates=["2020-01-01", "2020-02-01", "2020-03-01"],
        config_path=config, db_path=db, parquet_dir=tmp_path / "p", client=top_up,
    )
    assert [call[1] for call in top_up.calls] == ["2020-02-01", "2020-03-01"]
    assert summary.skipped_pairs == 1
    assert len(DuckDBStore(db).read_raw_observation_vintages()) == 3


def test_a_failed_pair_does_not_abort_the_run(tmp_path):
    """One dead request must cost one pair, never the hours already spent.

    Regression: a transport fault escaped both the client's retry loop and this loop's
    `except FredError`, so a ~6,500-request backfill died outright mid-run. A failure is now
    recorded against its own (series, as-of), the remaining pairs still run, and because the
    failed pair was never stored, `resume` picks it up on the next attempt.
    """
    from macro_engine.ingest.fred import FredError
    from macro_engine.ingest.service import run_fred_vintage_ingestion
    from macro_engine.storage.duckdb_store import DuckDBStore

    class _FlakyClient(_CountingClient):
        def __init__(self, dead):
            super().__init__()
            self.dead = set(dead)

        def get_series_observations_vintage(self, series_id, as_of, **kwargs):
            if (series_id, as_of) in self.dead:
                self.calls.append((series_id, as_of))
                raise FredError("FRED request failed after 5 retries: ReadTimeout")
            return super().get_series_observations_vintage(series_id, as_of, **kwargs)

    db = tmp_path / "macro.duckdb"
    config = _sources_file(tmp_path, ["AAA", "BBB"])
    dates = ["2020-01-01", "2020-02-01"]

    flaky = _FlakyClient({("AAA", "2020-01-01")})
    summary = run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=flaky,
    )
    assert summary.failed_count == 1
    # Everything else still landed: the other pair of AAA and all of BBB.
    assert summary.vintage_rows == 3
    stored = DuckDBStore(db).read_raw_observation_vintages()
    assert set(zip(stored["series_id"], stored["date"].astype(str))) == {
        ("AAA", "2020-02-01"),
        ("BBB", "2020-01-01"),
        ("BBB", "2020-02-01"),
    }

    # The failure was not swallowed: resume asks for exactly the pair that is missing.
    retry = _CountingClient()
    resumed = run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=retry,
    )
    assert retry.calls == [("AAA", "2020-01-01")]
    assert resumed.skipped_pairs == 3
    assert resumed.vintage_rows == 1


def test_concurrent_fetch_stores_the_same_rows_in_date_order(tmp_path):
    """Concurrency must change only the speed, never the contents or the bookkeeping.

    ALFRED latency is uneven per request (0.3 s for one vintage, 17-19 s for another in the
    same series), so the fetch is pooled. The write stays serial and the per-pair reporting
    stays date-ordered, so summaries and `resume` behave identically at any worker count.
    """
    from macro_engine.ingest.service import run_fred_vintage_ingestion
    from macro_engine.storage.duckdb_store import DuckDBStore

    db = tmp_path / "macro.duckdb"
    config = _sources_file(tmp_path, ["AAA"])
    dates = [f"2020-{month:02d}-01" for month in range(1, 9)]

    client = _CountingClient()
    summary = run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=client, max_workers=4,
    )
    assert summary.vintage_rows == len(dates)
    assert sorted(call[1] for call in client.calls) == dates
    stored = DuckDBStore(db).read_raw_observation_vintages()
    assert sorted(stored["date"].astype(str)) == dates

    # And the concurrent run is as resumable as the serial one.
    second = _CountingClient()
    again = run_fred_vintage_ingestion(
        as_of_dates=dates, config_path=config, db_path=db,
        parquet_dir=tmp_path / "p", client=second, max_workers=4,
    )
    assert second.calls == []
    assert again.skipped_pairs == len(dates)
