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
