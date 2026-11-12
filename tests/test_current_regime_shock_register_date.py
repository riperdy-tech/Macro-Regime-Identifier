"""B6: `active_shocks_on_date` used to be null whenever the regime's date (a month start, often a weekend)
had no shock-register row of its own. It now reads the last register row on or before the date and says
which date it used."""

from __future__ import annotations

import pandas as pd

from macro_engine.reports.config import ReportConfig
from macro_engine.reports.writer import _active_shocks_for_date, build_current_regime_report

from test_phase_g_reports import (
    _dimension_contributions,
    _dimension_scores,
    _feature_health,
    _regime_contributions,
    _regime_health,
    _regime_scores,
    _source_health,
)


def _register(rows: list[tuple[str, str, bool]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": date, "shock_id": shock_id, "active": active} for date, shock_id, active in rows]
    )


def _report(register: pd.DataFrame | None):
    return build_current_regime_report(
        regime_scores=_regime_scores(),
        regime_health=_regime_health(),
        regime_contributions=_regime_contributions(),
        dimension_scores=_dimension_scores(),
        dimension_contributions=_dimension_contributions(),
        feature_health=_feature_health(),
        source_health=_source_health(),
        config=ReportConfig(max_contributors=3),
        shock_register=register,
    )


def test_a_regime_date_without_a_register_row_uses_the_last_earlier_row_and_names_its_date():
    # the regime date is 2026-01-01; the register's last row is 2025-12-31
    register = _register(
        [
            ("2025-12-30", "volatility_shock", False),
            ("2025-12-31", "volatility_shock", True),
            ("2025-12-31", "credit_shock", False),
        ]
    )
    payload = _report(register)

    assert payload["active_shocks_on_date"] == ["volatility_shock"]
    assert payload["active_shocks_register_date"] == "2025-12-31"
    assert any("2025-12-31" in reason for reason in payload["reasons"])


def test_an_exact_date_row_is_used_as_before_and_needs_no_reason():
    register = _register([("2025-12-31", "volatility_shock", True), ("2026-01-01", "credit_shock", True)])
    payload = _report(register)

    assert payload["active_shocks_on_date"] == ["credit_shock"]
    assert payload["active_shocks_register_date"] == "2026-01-01"
    assert not any("shock_register" in reason for reason in payload["reasons"])


def test_an_empty_list_is_still_a_value_when_the_earlier_row_shows_nothing_active():
    ids, reason = _active_shocks_for_date(_register([("2025-12-31", "volatility_shock", False)]), "2026-01-01")
    assert ids == []
    assert reason is None


def test_a_register_with_only_later_rows_is_still_null_with_a_reason():
    ids, reason = _active_shocks_for_date(_register([("2026-02-02", "volatility_shock", True)]), "2026-01-01")
    assert ids is None
    assert reason == "shock_register_no_row_for_date"


def test_no_register_is_still_null_and_has_no_register_date():
    payload = _report(None)
    assert payload["active_shocks_on_date"] is None
    assert payload["active_shocks_register_date"] is None
