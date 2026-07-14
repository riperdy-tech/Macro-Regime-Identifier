from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class DiagnosticSmoothingConfig(BaseModel):
    enabled: bool = False
    min_months_before_switch: int = Field(default=2, ge=1)
    probability_gap_required: float = Field(default=0.10, ge=0, le=1)


class TransitionFilterConfig(BaseModel):
    enabled: bool = False
    min_confidence_to_switch: float = Field(default=0.0, ge=0, le=1)
    # A reported switch must be confirmed by this many consecutive months of
    # the same raw leader. 1 keeps the old immediate-switch behavior.
    confirmation_months: int = Field(default=1, ge=1)
    # If set, confirmation only applies to switches below this confidence;
    # higher-confidence switches (for example crisis flips) stay immediate.
    only_when_confidence_below: float | None = Field(default=None, gt=0, le=1)


class HistoricalDiagnosticConfig(BaseModel):
    start_date: date
    end_date: date | None = None
    mode: Literal["revised_data"] = "revised_data"
    min_valid_regimes: int = Field(default=2, ge=1)
    low_confidence_threshold: float = Field(default=0.05, ge=0, le=1)
    smoothing: DiagnosticSmoothingConfig = Field(default_factory=DiagnosticSmoothingConfig)
    transition_filter: TransitionFilterConfig = Field(default_factory=TransitionFilterConfig)

    @model_validator(mode="after")
    def end_after_start(self) -> HistoricalDiagnosticConfig:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("historical diagnostic end_date must be >= start_date")
        return self


def load_historical_diagnostic_config(path: str | Path) -> HistoricalDiagnosticConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return HistoricalDiagnosticConfig.model_validate(data.get("historical_diagnostic", {}))
