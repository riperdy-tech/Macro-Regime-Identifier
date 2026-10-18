"""E0.2a measurement: rolling classified share and token cost per day (MRI_EVENTS_PLAN v2 section 9).

Two read-only measurements over the local store; nothing here changes classification behaviour.

* `rolling_classified_share` - of the items first held in the last `window_days` days, the share with a
  successful `origin=live` classification made within 14 days of publication (plan section 3.2; imported and
  replayed classifications do not count). The plan's criterion (>= 85%) is only *evaluable* once the window
  holds `window_days` observed days; until then the report says so rather than claiming a pass.
* `daily_token_usage` - provider token usage per classification day, from the usage the classifier stored
  with each live response (`_provider_usage`). The durable run-history snapshot deliberately omits raw
  responses (OA-5), so usage exists only for classifications made on this machine; the report states how many
  rows carry usage. Prices are never assumed: `estimated_usd` stays null unless the caller supplies them.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from macro_engine.news.provenance import is_forward_evidence
from macro_engine.news.usage_report import _usage_row

TARGET_SHARE = 0.85
DEFAULT_WINDOW_DAYS = 14


def _is_countable(classifications: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """Classifications that count as evidence under plan section 3.2: successful, `origin=live` (a null origin
    is live unless the provider is mock, as in `news.provenance`), and made within 14 days of the item's
    publication. Imported and replayed classifications never count."""
    if classifications.empty:
        return classifications
    df = classifications.reset_index(drop=True)
    if "published_at" not in df.columns:
        pub = items[["news_id", "published_at"]].drop_duplicates("news_id", keep="last") if not items.empty else None
        df = df.merge(pub, on="news_id", how="left") if pub is not None else df.assign(published_at=pd.NaT)
    origin = df["origin"] if "origin" in df.columns else pd.Series([None] * len(df))
    live = origin.fillna(df["ai_provider"].map(lambda p: "mock" if p == "mock" else "live"))
    keep = [
        is_forward_evidence(effective_origin=o, classification_status=s, classified_at=c, published_at=p)
        for o, s, c, p in zip(live, df["classification_status"], df["classified_at"], df["published_at"])
    ]
    return df[pd.Series(keep, index=df.index)]


def rolling_classified_share(
    items: pd.DataFrame,
    classifications: pd.DataFrame,
    *,
    as_of: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    target: float = TARGET_SHARE,
    since: date | None = None,
) -> dict[str, Any]:
    """Per-day and rolling share of items with a counted classification. An item's day is the UTC date of
    `first_seen_at` (the day the engine first held it), falling back to `published_at` for legacy rows.
    `since` (e.g. the day the cap was raised) trims the window so older, differently-capped days cannot
    count towards the criterion; the criterion then needs `window_days` days on or after `since`."""
    start = pd.Timestamp(as_of) - pd.Timedelta(days=window_days - 1)
    if since is not None:
        start = max(start, pd.Timestamp(since))
    result: dict[str, Any] = {
        "as_of": str(as_of),
        "window_days": window_days,
        "window_start": str(start.date()),
        "target": target,
        "since": None if since is None else str(since),
        "per_day": [],
    }
    if items.empty:
        frame = pd.DataFrame(columns=["news_id", "day"])
    else:
        seen = pd.to_datetime(items["first_seen_at"], errors="coerce", utc=True)
        seen = seen.where(seen.notna(), pd.to_datetime(items["published_at"], errors="coerce", utc=True))
        frame = pd.DataFrame({"news_id": items["news_id"], "day": seen.dt.tz_localize(None).dt.normalize()})
        frame = frame.dropna(subset=["day"]).drop_duplicates("news_id")
    frame = frame[(frame["day"] >= start) & (frame["day"] <= pd.Timestamp(as_of))]
    counted = set(_is_countable(classifications, items)["news_id"].astype(str)) if not classifications.empty else set()
    frame = frame.assign(classified=frame["news_id"].astype(str).isin(counted))

    for day, group in frame.groupby("day"):
        result["per_day"].append(
            {
                "day": str(day.date()),
                "items": int(len(group)),
                "classified": int(group["classified"].sum()),
                "share": round(float(group["classified"].mean()), 4),
            }
        )
    total, classified = int(len(frame)), int(frame["classified"].sum())
    observed_days = len(result["per_day"])
    result.update(
        items=total,
        classified=classified,
        share=None if total == 0 else round(classified / total, 4),
        observed_days=observed_days,
        criterion_evaluable=observed_days >= window_days,
        meets_target=None if total == 0 or observed_days < window_days else classified / total >= target,
    )
    return result


def daily_token_usage(
    classifications: pd.DataFrame,
    *,
    cap_items_per_day: int | None = None,
    price_per_mtok_usd: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Token usage per classification day. `price_per_mtok_usd` may carry `prompt_cache_miss`,
    `prompt_cache_hit` and `completion`; with all three, `estimated_usd` is filled, otherwise it is null."""
    rows = [_usage_row(r) for r in classifications.to_dict(orient="records")] if not classifications.empty else []
    with_usage = [r for r in rows if r["has_usage"]]
    by_day: dict[str, dict[str, int]] = {}
    for r in with_usage:
        ts = pd.Timestamp(r["classified_at"]) if r["classified_at"] is not None else None
        if ts is None or pd.isna(ts):
            continue
        d = by_day.setdefault(str(ts.date()), {"items": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                               "total_tokens": 0, "prompt_cache_hit_tokens": 0,
                                               "prompt_cache_miss_tokens": 0})
        d["items"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens"):
            value = r["usage"].get(key)
            d[key] += 0 if value is None else int(value)
    n = len(with_usage)
    totals = {k: sum(v[k] for v in by_day.values()) for k in
              ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
               "prompt_cache_miss_tokens")}
    per_item = None if n == 0 else {k: round(v / n, 1) for k, v in totals.items()}
    out: dict[str, Any] = {
        "classifications": len(rows),
        "with_usage": n,
        "missing_usage": len(rows) - n,
        "usage_note": "usage is stored only on the machine that classified; run-history omits raw responses",
        "per_day": {k: by_day[k] for k in sorted(by_day)},
        "per_item_mean": per_item,
        "cap_items_per_day": cap_items_per_day,
        "projected_tokens_per_day_at_cap": None,
        "estimated_usd_per_day_at_cap": None,
    }
    if per_item is not None and cap_items_per_day is not None:
        projected = {k: round(v * cap_items_per_day) for k, v in per_item.items()}
        out["projected_tokens_per_day_at_cap"] = projected
        p = price_per_mtok_usd or {}
        if all(k in p for k in ("prompt_cache_miss", "prompt_cache_hit", "completion")):
            out["estimated_usd_per_day_at_cap"] = round(
                (projected["prompt_cache_miss_tokens"] * p["prompt_cache_miss"]
                 + projected["prompt_cache_hit_tokens"] * p["prompt_cache_hit"]
                 + projected["completion_tokens"] * p["completion"]) / 1e6, 4)
    return out
