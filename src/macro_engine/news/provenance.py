"""Provenance and forward-evidence qualification for news classifications."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

DEFAULT_MAX_AGE_DAYS = 14


def is_forward_evidence(
    record: Any = None,
    *,
    effective_origin: str | None = None,
    classification_status: str | None = None,
    classified_at: datetime | pd.Timestamp | str | None = None,
    published_at: datetime | pd.Timestamp | str | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> bool:
    """Predicate for whether a news classification qualifies as forward evidence.

    A row counts only if:
    - effective_origin == 'live'
    - classification_status == 'success'
    - classified_at - published_at <= max_age_days (14 days)
    """
    if record is not None:
        if isinstance(record, dict):
            effective_origin = record.get("effective_origin") or record.get("origin")
            classification_status = record.get("classification_status")
            classified_at = record.get("classified_at")
            published_at = record.get("published_at")
        elif hasattr(record, "get"):
            effective_origin = record.get("effective_origin") or record.get("origin")
            classification_status = record.get("classification_status")
            classified_at = record.get("classified_at")
            published_at = record.get("published_at")
        else:
            effective_origin = getattr(record, "effective_origin", getattr(record, "origin", None))
            classification_status = getattr(record, "classification_status", None)
            classified_at = getattr(record, "classified_at", None)
            published_at = getattr(record, "published_at", None)

    if effective_origin != "live":
        return False
    if classification_status != "success":
        return False
    if classified_at is None or published_at is None:
        return False
    if pd.isna(classified_at) or pd.isna(published_at):
        return False

    c_ts = pd.to_datetime(classified_at, utc=True)
    p_ts = pd.to_datetime(published_at, utc=True)
    diff = c_ts - p_ts
    if diff > pd.Timedelta(days=max_age_days):
        return False
    # Clock skew tolerance: reject if classified more than 1 day before publication
    if diff < -pd.Timedelta(days=1):
        return False
    return True


def filter_forward_evidence(
    classifications: pd.DataFrame,
    news_items: pd.DataFrame,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> pd.DataFrame:
    """Filter classifications DataFrame to rows qualifying as forward evidence."""
    if classifications.empty:
        return classifications.copy()

    df = classifications.copy()
    if "effective_origin" not in df.columns:
        if "origin" in df.columns:
            df["effective_origin"] = df["origin"].fillna(
                df["ai_provider"].map(lambda p: "mock" if p == "mock" else "live")
            )
        else:
            df["effective_origin"] = df["ai_provider"].map(lambda p: "mock" if p == "mock" else "live")

    if "published_at" not in df.columns:
        if not news_items.empty and "published_at" in news_items.columns:
            pub_map = news_items[["news_id", "published_at"]].drop_duplicates(subset=["news_id"], keep="last")
            df = df.merge(pub_map, on="news_id", how="left")
        else:
            df["published_at"] = pd.NaT

    mask = df.apply(
        lambda r: is_forward_evidence(
            effective_origin=r.get("effective_origin"),
            classification_status=r.get("classification_status"),
            classified_at=r.get("classified_at"),
            published_at=r.get("published_at"),
            max_age_days=max_age_days,
        ),
        axis=1,
    )
    return classifications.loc[mask[mask].index].copy()
