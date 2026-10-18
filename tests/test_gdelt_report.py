"""E0.2b: rolling GDELT success-rate reporter."""
from __future__ import annotations

from datetime import date

import pandas as pd

from macro_engine.news.gdelt_report import rolling_gdelt_success_rate


def _runs(per_day: dict[str, list[str]], provider: str = "gdelt") -> pd.DataFrame:
    rows = []
    for day, statuses in per_day.items():
        for i, st in enumerate(statuses):
            rows.append({"run_at": pd.Timestamp(f"{day} 00:2{i}"), "provider": provider, "status": st})
    return pd.DataFrame(rows)


def test_circuit_open_counts_as_a_failed_scheduled_pull():
    runs = _runs({"2026-10-16": ["rate_limited", "circuit_open", "circuit_open", "ok", "empty", "error"]})
    out = rolling_gdelt_success_rate(runs, as_of=date(2026, 10, 16))
    assert out["scheduled"] == 6 and out["succeeded"] == 2
    assert out["success_rate"] == round(2 / 6, 4)
    assert out["status_counts"]["circuit_open"] == 2


def test_not_evaluable_until_window_is_full_and_never_claims_a_pass_early():
    runs = _runs({"2026-10-16": ["ok"] * 6})
    out = rolling_gdelt_success_rate(runs, as_of=date(2026, 10, 16))
    assert out["success_rate"] == 1.0
    assert out["criterion_evaluable"] is False and out["meets_target"] is None


def test_full_window_meets_or_misses_target():
    days = {f"2026-10-{d:02d}": ["ok"] * 6 for d in range(3, 17)}  # 14 days, 84 pulls
    out = rolling_gdelt_success_rate(_runs(days), as_of=date(2026, 10, 16))
    assert out["criterion_evaluable"] and out["meets_target"] is True
    days["2026-10-16"] = ["circuit_open"] * 6
    days["2026-10-15"] = ["circuit_open"] * 6
    out = rolling_gdelt_success_rate(_runs(days), as_of=date(2026, 10, 16))
    assert out["success_rate"] == round(72 / 84, 4) and out["meets_target"] is False


def test_since_excludes_pre_repair_days_and_zero_is_a_value():
    runs = _runs({"2026-10-10": ["ok"] * 6, "2026-10-16": ["circuit_open"] * 6})
    out = rolling_gdelt_success_rate(runs, as_of=date(2026, 10, 16), since=date(2026, 10, 15))
    assert out["scheduled"] == 6 and out["success_rate"] == 0.0


def test_other_providers_and_empty_inputs_are_ignored():
    runs = _runs({"2026-10-16": ["ok"] * 3}, provider="rss")
    assert rolling_gdelt_success_rate(runs, as_of=date(2026, 10, 16))["success_rate"] is None
    assert rolling_gdelt_success_rate(pd.DataFrame(), as_of=date(2026, 10, 16))["scheduled"] == 0
