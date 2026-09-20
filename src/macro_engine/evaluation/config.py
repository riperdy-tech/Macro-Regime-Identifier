from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Frequency = Literal["daily", "weekly", "monthly", "quarterly", "annual"]
DateRule = Literal["month_start", "month_end"]
AsOfPolicy = Literal["latest_observation_on_or_before_date"]
# calendar_asof: latest observation on/before the evaluation date, gated by a fixed
#   per-series publication_lag_days approximation.
# point_in_time: the value actually published as of the evaluation date, resolved from
#   stored ALFRED vintages. Opt-in; the default is unchanged so existing historical
#   diagnostics keep their factual basis.
ScoringMode = Literal["same_date", "calendar_asof", "point_in_time"]


class EvaluationCalendarConfig(BaseModel):
    frequency: Literal["monthly"] = "monthly"
    date_rule: DateRule = "month_start"
    start_date: str | None = None
    end_date: str | None = None
    as_of_policy: AsOfPolicy = "latest_observation_on_or_before_date"
    max_lag_by_frequency: dict[Frequency, int] = Field(
        default_factory=lambda: {
            "daily": 10,
            "weekly": 21,
            "monthly": 75,
            "quarterly": 140,
            "annual": 450,
        }
    )

    @model_validator(mode="after")
    def has_all_lag_thresholds(self) -> EvaluationCalendarConfig:
        missing = {"daily", "weekly", "monthly", "quarterly", "annual"} - set(
            self.max_lag_by_frequency
        )
        if missing:
            raise ValueError(f"missing max_lag_by_frequency values: {sorted(missing)}")
        return self


class EvaluationConfig(BaseModel):
    scoring_mode: ScoringMode = "calendar_asof"
    evaluation_calendar: EvaluationCalendarConfig = Field(
        default_factory=EvaluationCalendarConfig
    )


def load_evaluation_config(path: str | Path) -> EvaluationConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return EvaluationConfig.model_validate(
        {
            "scoring_mode": data.get("scoring_mode", "calendar_asof"),
            "evaluation_calendar": data.get("evaluation_calendar", {}),
        }
    )

