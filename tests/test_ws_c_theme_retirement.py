"""WS-C theme retirement: a secular theme with fewer than 8 articles in 180 days is dormant and proposed for removal,
the mirror of promotion's evidence bar; a history shorter than 90 days judges nothing."""
from datetime import UTC, datetime, timedelta

import pandas as pd
import yaml

from macro_engine.news.theme_retirement import (
    RetirementThresholds,
    evaluate_retirement,
    load_classification_history,
    retire_themes,
)

AS_OF = datetime(2026, 10, 4, tzinfo=UTC)


def rows(theme, n, *, days_ago=10, status="success", start_id=0):
    return [{"news_id": f"{theme}-{start_id + i}", "classified_at": AS_OF - timedelta(days=days_ago),
             "secular_theme": theme, "classification_status": status} for i in range(n)]


def history(*parts, oldest_days_ago=120):
    anchor = [{"news_id": "anchor", "classified_at": AS_OF - timedelta(days=oldest_days_ago), "secular_theme": None,
               "classification_status": "success"}]
    return pd.DataFrame(anchor + [r for part in parts for r in part])


def test_a_theme_under_eight_articles_in_180_days_is_dormant_and_one_at_eight_is_not():
    out = evaluate_retirement(history(rows("ai_compute", 8), rows("quantum_computing", 7)),
                              {"ai_compute", "quantum_computing"}, as_of=AS_OF)
    assert out["evaluated"] is True
    assert out["themes"] == {"ai_compute": {"articles": 8, "dormant": False},
                             "quantum_computing": {"articles": 7, "dormant": True}}
    assert out["dormant"] == ["quantum_computing"]


def test_articles_older_than_the_window_failed_classifications_and_repeats_do_not_count():
    old = rows("space_economy", 20, days_ago=200)
    failed = rows("space_economy", 20, status="failed", start_id=100)
    repeated = rows("space_economy", 3, start_id=200) * 3              # the same 3 news items classified 3 times
    out = evaluate_retirement(history(old, failed, repeated, oldest_days_ago=250), {"space_economy"}, as_of=AS_OF)
    assert out["themes"]["space_economy"] == {"articles": 3, "dormant": True}


def test_a_history_shorter_than_90_days_judges_nothing():
    out = evaluate_retirement(history(rows("ai_compute", 1), oldest_days_ago=60), {"ai_compute"}, as_of=AS_OF)
    assert out["evaluated"] is False and out["dormant"] == [] and out["themes"] == {}
    assert "60 days < 90" in out["reason"]
    empty = evaluate_retirement(pd.DataFrame(columns=["news_id", "classified_at", "secular_theme",
                                                      "classification_status"]), {"ai_compute"}, as_of=AS_OF)
    assert empty["evaluated"] is False and empty["history_days"] == 0


def test_the_thresholds_are_promotions_bar_and_stated_in_the_report():
    out = evaluate_retirement(history(), {"ai_compute"}, as_of=AS_OF)
    assert out["thresholds"] == {"window_days": 180, "min_articles": 8, "min_history_days": 90}
    assert RetirementThresholds().min_articles == 8


def test_retire_themes_removes_only_the_named_secular_themes(tmp_path):
    path = tmp_path / "news_themes.yaml"
    path.write_text(yaml.safe_dump({"macro_themes": [{"theme_id": "inflation_pressure"}],
                                    "secular_themes": {"ai_compute": {"label": "AI"}, "space_economy": {"label": "S"}}},
                                   sort_keys=False), encoding="utf-8")
    assert retire_themes(path, ["space_economy", "not_a_theme"]) == ["space_economy"]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["secular_themes"] == {"ai_compute": {"label": "AI"}}
    assert data["macro_themes"] == [{"theme_id": "inflation_pressure"}]
    before = path.read_text(encoding="utf-8")
    assert retire_themes(path, ["not_a_theme"]) == [] and path.read_text(encoding="utf-8") == before


def test_the_history_loader_reads_every_daily_parquet(tmp_path):
    day = tmp_path / "classifications" / "2026" / "09"
    day.mkdir(parents=True)
    pd.DataFrame(rows("ai_compute", 2)).to_parquet(day / "classifications_2026-09-20.parquet")
    pd.DataFrame(rows("ai_compute", 1, start_id=5)).to_parquet(day / "classifications_2026-09-21.parquet")
    assert len(load_classification_history(tmp_path)) == 3
    assert load_classification_history(tmp_path / "missing").empty
