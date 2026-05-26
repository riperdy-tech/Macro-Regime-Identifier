from __future__ import annotations

import pandas as pd

from macro_engine.config.schemas import SourceHealthItem, SourceHealthReport


def calculate_source_health(feature_values: pd.DataFrame) -> SourceHealthReport:
    items: list[SourceHealthItem] = []
    for row in feature_values.to_dict(orient="records"):
        used_in_score = bool(
            row.get("enabled")
            and row.get("available")
            and row.get("freshness_score", 0) > 0
            and row.get("feature_weight", 0) > 0
        )
        items.append(
            SourceHealthItem(
                feature_id=row["feature_id"],
                series_id=_optional_text(row.get("series_id")) or "",
                status=row.get("status") or "unknown",
                last_observation=_optional_text(row.get("last_observation")),
                used_in_score=used_in_score,
                freshness_score=float(row.get("freshness_score") or 0.0),
                reason=_optional_text(row.get("reason")),
            )
        )

    required_missing = [
        row["feature_id"]
        for row in feature_values.to_dict(orient="records")
        if row.get("required") and row.get("status") in {"missing", "insufficient_history", "unusable"}
    ]
    required_stale = [
        row["feature_id"]
        for row in feature_values.to_dict(orient="records")
        if row.get("required") and row.get("status") == "stale"
    ]

    return SourceHealthReport(
        total_series=len(feature_values),
        available_series=int(feature_values["available"].fillna(False).sum()),
        stale_series=int((feature_values["status"] == "stale").sum()),
        missing_series=int(feature_values["status"].isin(["missing", "insufficient_history"]).sum()),
        disabled_series=int((feature_values["status"] == "disabled").sum()),
        required_series_missing=required_missing,
        required_series_stale=required_stale,
        items=items,
    )


def _optional_text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)
