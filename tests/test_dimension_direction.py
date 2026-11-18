"""WO_MD1 Part 2: `dimension_direction` on the current regime report (direction beside level)."""

from __future__ import annotations

import math

import pandas as pd

from macro_engine.reports.config import ReportConfig
from macro_engine.reports.writer import (
    DIRECTION_MIN_HISTORY,
    build_current_regime_report,
    compute_dimension_direction,
)

from test_phase_g_reports import (
    _dimension_contributions,
    _dimension_scores,
    _feature_health,
    _regime_contributions,
    _regime_health,
    _regime_scores,
    _source_health,
)


def _history(dimension_id: str, scores: list, start: str = "2015-01-01", valid=None) -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(scores), freq="MS")
    return pd.DataFrame(
        {
            "dimension_id": dimension_id,
            "date": dates,
            "score": scores,
            "valid": [True] * len(scores) if valid is None else valid,
        }
    )


def test_direction_is_the_change_over_three_and_six_months_and_is_standardised_without_look_ahead():
    # 80 months; the score rises by 1 each month except that the last month jumps by 4
    scores = [float(i) for i in range(79)] + [82.0]
    frame = _history("growth_momentum", scores)
    last = frame["date"].iloc[-1]

    result = compute_dimension_direction(frame, last)["growth_momentum"]

    assert result["d3"] == 82.0 - 76.0
    assert result["d6"] == 82.0 - 73.0
    assert result["as_of"] == last.strftime("%Y-%m-%d")
    # the expanding standard deviation of the 3-month change, over months up to and including the last
    d3 = pd.Series(scores) - pd.Series(scores).shift(3)
    assert math.isclose(result["z3"], 6.0 / d3.dropna().std(), rel_tol=1e-12)
    d6 = pd.Series(scores) - pd.Series(scores).shift(6)
    assert math.isclose(result["z6"], 9.0 / d6.dropna().std(), rel_tol=1e-12)
    assert result["reasons"] == {}


def test_a_later_month_does_not_change_the_value_at_an_earlier_report_month():
    scores = [float(i % 7) for i in range(90)]
    frame = _history("policy_stance", scores)
    cut = frame["date"].iloc[79]
    early = compute_dimension_direction(frame[frame["date"] <= cut], cut)["policy_stance"]
    late = compute_dimension_direction(frame, cut)["policy_stance"]
    assert early == late


def test_short_history_gives_the_change_but_no_standardised_value_and_says_why():
    frame = _history("growth_momentum", [float(i) for i in range(20)])
    result = compute_dimension_direction(frame, frame["date"].iloc[-1])["growth_momentum"]

    assert result["d3"] == 3.0 and result["d6"] == 6.0
    assert result["z3"] is None and result["z6"] is None
    assert result["reasons"]["z3"].startswith("history_short:")
    assert str(DIRECTION_MIN_HISTORY) in result["reasons"]["z3"]


def test_history_shorter_than_the_horizon_gives_nothing_and_says_why():
    frame = _history("growth_momentum", [1.0, 2.0])
    result = compute_dimension_direction(frame, frame["date"].iloc[-1])["growth_momentum"]
    assert result["d3"] is None and result["d6"] is None and result["z3"] is None and result["z6"] is None
    assert result["reasons"]["d3"] == "score_missing_3_months_before_as_of"


def test_an_invalid_score_at_the_report_month_is_null_with_a_reason():
    valid = [True] * 79 + [False]
    frame = _history("credit_liquidity", [float(i) for i in range(80)], valid=valid)
    result = compute_dimension_direction(frame, frame["date"].iloc[-1])["credit_liquidity"]
    assert all(result[key] is None for key in ("d3", "d6", "z3", "z6"))
    assert result["reasons"]["d6"] == "score_invalid_or_missing_at_as_of"


def test_an_invalid_month_in_the_past_leaves_that_change_null_not_zero():
    valid = [True] * 80
    valid[76] = False  # three months before the last month
    frame = _history("yield_curve", [float(i) for i in range(80)], valid=valid)
    result = compute_dimension_direction(frame, frame["date"].iloc[-1])["yield_curve"]
    assert result["d3"] is None
    assert result["d6"] == 6.0


def test_zero_is_a_value():
    # a flat score: the change is exactly 0.0 (a value) and the standardised value is undefined, with a reason
    frame = _history("dollar", [0.0] * 80)
    result = compute_dimension_direction(frame, frame["date"].iloc[-1])["dollar"]
    assert result["d3"] == 0.0 and result["d6"] == 0.0
    assert result["z3"] is None and result["reasons"]["z3"] == "zero_dispersion"


def test_every_dimension_in_the_table_is_reported():
    frame = pd.concat([_history("growth_momentum", [0.0, 1.0, 2.0, 3.0]), _history("dollar", [1.0, 1.5, 2.0, 2.5])])
    result = compute_dimension_direction(frame, "2015-04-01")
    assert set(result) == {"growth_momentum", "dollar"}
    assert result["dollar"]["d3"] == 1.5 and result["growth_momentum"]["d3"] == 3.0


def test_the_current_report_carries_dimension_direction_and_keeps_schema_version_2():
    payload = build_current_regime_report(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        regime_contributions=_regime_contributions(),
        dimension_scores=_dimension_scores(),
        dimension_contributions=_dimension_contributions(),
        feature_health=_feature_health(),
        source_health=_source_health(),
        config=ReportConfig(max_contributors=3),
    )
    assert payload["schema_version"] == 2
    direction = payload["dimension_direction"]
    assert set(direction) == set(_dimension_scores()["dimension_id"])
    for entry in direction.values():
        assert set(entry) == {"d3", "d6", "z3", "z6", "as_of", "reasons"}
