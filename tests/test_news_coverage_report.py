"""E0.2a: rolling classified share and token usage reporter, plus the cap config."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from macro_engine.news.coverage_report import daily_token_usage, rolling_classified_share
from macro_engine.operations_config import load_daily_pipeline_config


def _items(days_to_counts: dict[str, int]) -> pd.DataFrame:
    rows = []
    for day, n in days_to_counts.items():
        for i in range(n):
            rows.append({"news_id": f"{day}-{i}", "first_seen_at": pd.Timestamp(f"{day} 08:00", tz="UTC"),
                         "published_at": pd.Timestamp(f"{day} 07:00", tz="UTC")})
    return pd.DataFrame(rows)


def _cls(news_ids, *, status="success", provider="deepseek", origin="live", usage=None, at="2026-10-16"):
    rows = []
    for nid in news_ids:
        raw = {"response": {"_provider_usage": usage}} if usage else {}
        rows.append({"news_id": nid, "classification_status": status, "ai_provider": provider, "origin": origin,
                     "classified_at": pd.Timestamp(at), "ai_model": "m", "raw_ai_response_json": json.dumps(raw)})
    return pd.DataFrame(rows)


def test_share_counts_only_successful_nonmock_and_not_evaluable_until_window_full():
    items = _items({"2026-10-15": 10, "2026-10-16": 10})
    ids = list(items["news_id"])
    c = pd.concat([_cls(ids[:6]), _cls(ids[6:8], status="error"), _cls(ids[8:10], provider="mock", origin=None)])
    out = rolling_classified_share(items, c, as_of=date(2026, 10, 16), window_days=14)
    assert out["items"] == 20 and out["classified"] == 6
    assert out["share"] == 0.3
    assert out["criterion_evaluable"] is False and out["meets_target"] is None


def test_share_meets_target_only_with_full_window():
    days = {f"2026-10-{d:02d}": 10 for d in range(3, 17)}  # 14 days
    items = _items(days)
    out = rolling_classified_share(items, _cls(list(items["news_id"])[:130]), as_of=date(2026, 10, 16))
    assert out["observed_days"] == 14 and out["criterion_evaluable"] is True
    assert out["share"] == round(130 / 140, 4) and out["meets_target"] is True
    low = rolling_classified_share(items, _cls(list(items["news_id"])[:100]), as_of=date(2026, 10, 16))
    assert low["meets_target"] is False


def test_since_excludes_older_days_and_zero_share_is_a_value():
    items = _items({"2026-10-10": 5, "2026-10-16": 4})
    out = rolling_classified_share(items, _cls([]), as_of=date(2026, 10, 16), since=date(2026, 10, 15))
    assert out["items"] == 4 and out["share"] == 0.0  # 0.0, not None
    assert out["observed_days"] == 1 and out["meets_target"] is None


def test_legacy_rows_without_first_seen_fall_back_to_published():
    items = pd.DataFrame([{"news_id": "a", "first_seen_at": pd.NaT,
                           "published_at": pd.Timestamp("2026-10-16 05:00", tz="UTC")}])
    out = rolling_classified_share(items, _cls(["a"]), as_of=date(2026, 10, 16))
    assert out["items"] == 1 and out["classified"] == 1


def test_token_usage_projection_and_no_price_assumed():
    usage = {"prompt_tokens": 800, "completion_tokens": 500, "total_tokens": 1300,
             "prompt_cache_hit_tokens": 600, "prompt_cache_miss_tokens": 200}
    c = pd.concat([_cls(["a", "b"], usage=usage), _cls(["c"])])  # one row without usage
    out = daily_token_usage(c, cap_items_per_day=150)
    assert out["with_usage"] == 2 and out["missing_usage"] == 1
    assert out["per_item_mean"]["total_tokens"] == 1300.0
    assert out["projected_tokens_per_day_at_cap"]["total_tokens"] == 195000
    assert out["estimated_usd_per_day_at_cap"] is None
    priced = daily_token_usage(c, cap_items_per_day=150,
                               price_per_mtok_usd={"prompt_cache_miss": 1.0, "prompt_cache_hit": 0.0, "completion": 2.0})
    assert priced["estimated_usd_per_day_at_cap"] == round((150 * 200 * 1.0 + 0 + 150 * 500 * 2.0) / 1e6, 4)


def test_empty_inputs_do_not_raise():
    assert rolling_classified_share(pd.DataFrame(), pd.DataFrame(), as_of=date(2026, 10, 16))["share"] is None
    assert daily_token_usage(pd.DataFrame())["per_item_mean"] is None


def test_live_config_cap_is_150():
    assert load_daily_pipeline_config("config/daily_pipeline_github_live.yaml").live_ai_safety.max_items_per_run == 150


def test_only_live_origin_within_14_days_of_publication_counts():
    items = _items({"2026-10-15": 4})
    ids = list(items["news_id"])
    c = pd.concat([
        _cls(ids[:1]),  # live, same day: counts
        _cls(ids[1:2], origin="imported"),  # backfilled: does not count
        _cls(ids[2:3], origin="replay"),
        _cls(ids[3:4], at="2026-11-10"),  # classified 26 days after publication: does not count
    ])
    out = rolling_classified_share(items, c, as_of=date(2026, 10, 16))
    assert out["items"] == 4 and out["classified"] == 1


def test_missing_usage_field_is_zero_not_a_default_but_present_zero_stays_zero():
    usage = {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}
    out = daily_token_usage(_cls(["a"], usage=usage))
    assert out["per_day"]["2026-10-16"]["completion_tokens"] == 0
    assert out["per_day"]["2026-10-16"]["prompt_cache_hit_tokens"] == 0
