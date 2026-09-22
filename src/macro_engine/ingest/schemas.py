from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Frequency = Literal["daily", "weekly", "monthly", "quarterly", "annual"]


class IngestionSource(BaseModel):
    series_id: str
    name: str
    provider: Literal["FRED"] = "FRED"
    dimension: str
    frequency: Frequency
    required: bool = True
    enabled: bool = True
    reason_disabled: str | None = None
    stale_after_days: int = Field(gt=0)
    unusable_after_days: int = Field(gt=0)
    # Approximate days between a FRED observation date and its first public
    # release. Used by calendar as-of alignment so an evaluation date cannot
    # see observations that had not been published yet. 0 keeps the old
    # observation-date-only behavior. This is a fixed approximation, not
    # ALFRED vintage data.
    publication_lag_days: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_staleness(self) -> IngestionSource:
        if self.unusable_after_days < self.stale_after_days:
            raise ValueError("unusable_after_days must be >= stale_after_days")
        if not self.enabled and self.required:
            raise ValueError("disabled ingestion source cannot be required")
        return self


class IngestionRunSummary(BaseModel):
    run_id: str
    series_requested: int
    series_succeeded: int
    series_failed: int
    stale_series: list[str]
    storage_path: str


class VintageIngestionSummary(BaseModel):
    """Outcome of an ALFRED vintage backfill. Counts are aggregated by design:
    one log line per (series, vintage) would be thousands of lines per backfill."""

    run_id: str
    series_requested: int
    as_of_dates: list[str]
    vintage_rows: int
    vintage_series: int
    # (series, as_of) pairs already stored and therefore not re-fetched. A full-history backfill
    # is thousands of rate-limited requests, so resumption has to be visible, not implicit.
    skipped_pairs: int = 0
    # (series, as_of) pairs where the series had no vintage yet — a fact about the
    # past (the series did not exist), reported rather than treated as a failure.
    empty_vintage_count: int
    # Of the skipped pairs, how many were skipped because a PREVIOUS run already established
    # that ALFRED has no vintage there. Reported separately because "nothing new to do" and
    # "nothing was ever there" are different answers to the same question.
    skipped_absent_pairs: int = 0
    failed_count: int
    storage_path: str
    # Which series actually landed rows in THIS run, so a partial run is legible.
    series_stored: list[str] = Field(default_factory=list)
    # Which series had at least one failed vintage fetch in THIS run, so a caller can name
    # the failure in a warning instead of only counting it.
    failed_series: list[str] = Field(default_factory=list)
