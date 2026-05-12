from __future__ import annotations

import numpy as np
import pandas as pd


def build_toy_observations() -> pd.DataFrame:
    rows: list[dict] = []
    monthly = pd.date_range("2018-01-01", "2026-04-01", freq="MS")
    weekly = pd.date_range("2018-01-05", "2026-05-08", freq="W-FRI")
    daily = pd.bdate_range("2018-01-02", "2026-05-08")
    annual = pd.date_range("2018-01-01", "2026-01-01", freq="YS")

    for i, dt in enumerate(monthly):
        rows.append(_row("CPIAUCSL", dt, 250 + i * 0.55 + max(0, i - 55) * 0.18))
        rows.append(_row("PCEPILFE", dt, 100 + i * 0.20 + max(0, i - 58) * 0.08))
        rows.append(_row("INDPRO", dt, 100 + np.sin(i / 6) * 2 - max(0, i - 82) * 0.20))
        rows.append(_row("UNRATE", dt, 4.0 + max(0, i - 82) * 0.035))
        rows.append(_row("FEDFUNDS", dt, 0.5 + min(max(i - 48, 0), 36) * 0.12))

    for i, dt in enumerate(weekly):
        rows.append(_row("NFCI", dt, -0.45 + max(0, i - 365) * 0.01))

    for i, dt in enumerate(daily):
        rows.append(_row("DGS2", dt, 0.7 + min(max(i - 900, 0), 700) * 0.003))
        rows.append(_row("SP500", dt, 2800 + i * 1.1 - max(0, i - 1900) * 3.0))
        rows.append(_row("VIXCLS", dt, 16 + max(0, i - 1900) * 0.025))
        rows.append(_row("DCOILWTICO", dt, 55 + np.sin(i / 80) * 8))
        rows.append(_row("GOLDAMGBD228NLBM", dt, 1300 + i * 0.25))

    for i, dt in enumerate(annual):
        rows.append(_row("FYFSGDA188S", dt, -3.0 - i * 0.1))

    return pd.DataFrame(rows)


def _row(series_id: str, date: pd.Timestamp, value: float) -> dict:
    return {"series_id": series_id, "date": date.date().isoformat(), "value": float(value)}
