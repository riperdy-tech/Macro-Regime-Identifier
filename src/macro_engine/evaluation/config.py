from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
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
    # Where point-in-time evidence begins. ALFRED's archive is not uniform: the risk-free curve
    # does not exist before 2005-07 (DGS10), the breakeven leg before 2014-02 (T10YIE), and the
    # observed ACM term premium before 2016-06. A blanket `point_in_time` therefore does not
    # upgrade old dates -- it deletes their inputs.
    #
    # The measured boundary is 2014-02-01: from there every anchor leg is resolvable and nothing
    # degrades (docs/ANCHOR_METHODOLOGY.md §6.2b). Before it, the documented, disclosed rule is
    # `calendar_asof`, which is an approximation but a populated one.
    point_in_time_start: str | None = None
    # How far the newest stored vintage may trail the as-of before the point-in-time basis is
    # declared STALE. Daily series publish with a lag of a day or two, so a few days is normal
    # operation; a month means the pipeline is not refreshing vintages and the "point-in-time"
    # number is really an old one wearing a precise label (measured: a 4.5-month lag moved the
    # 10-year nominal by 54 bp -- docs/ANCHOR_METHODOLOGY.md §6.2b).
    point_in_time_max_lag_days: int = 7
    evaluation_calendar: EvaluationCalendarConfig = Field(
        default_factory=EvaluationCalendarConfig
    )

    def effective_scoring_mode(self, as_of: pd.Timestamp | str | None) -> ScoringMode:
        """The rule that applies to ONE evaluation date.

        A hybrid is only honest if the boundary is a declared property of the config rather than a
        silent fallback buried in a resolver, so this is the single place the switch happens and
        `scoring_mode_applied` reports which way it went for any given date.
        """
        if self.scoring_mode != "point_in_time":
            return self.scoring_mode
        if self.point_in_time_start is None or as_of is None:
            return "point_in_time"
        return (
            "point_in_time"
            if pd.Timestamp(as_of) >= pd.Timestamp(self.point_in_time_start)
            else "calendar_asof"
        )

    def scoring_mode_applied(self, as_of: pd.Timestamp | str | None) -> str:
        """`effective_scoring_mode` plus the reason, for provenance."""
        applied = self.effective_scoring_mode(as_of)
        if applied == self.scoring_mode:
            return applied
        return f"{applied} (before point_in_time_start={self.point_in_time_start})"


def load_evaluation_config(path: str | Path) -> EvaluationConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return EvaluationConfig.model_validate(
        {
            "scoring_mode": data.get("scoring_mode", "calendar_asof"),
            "point_in_time_start": data.get("point_in_time_start"),
            "point_in_time_max_lag_days": data.get("point_in_time_max_lag_days", 7),
            "evaluation_calendar": data.get("evaluation_calendar", {}),
        }
    )

