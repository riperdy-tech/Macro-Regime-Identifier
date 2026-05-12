from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import requests


class FredError(RuntimeError):
    """Raised when FRED returns an error or unusable payload."""


@dataclass
class FredClient:
    api_key: str
    base_url: str = "https://api.stlouisfed.org/fred"
    timeout: int = 30
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise FredError("FRED_API_KEY is required for live ingestion")
        if self.session is None:
            self.session = requests.Session()

    def get_series_observations(
        self,
        series_id: str,
        observation_start: str | None = None,
        observation_end: str | None = None,
    ) -> pd.DataFrame:
        payload = self._get(
            "/series/observations",
            {
                "series_id": series_id,
                "observation_start": observation_start,
                "observation_end": observation_end,
            },
        )
        observations = payload.get("observations", [])
        if not observations:
            raise FredError(f"FRED returned no observations for {series_id}")
        frame = pd.DataFrame(observations)
        frame["series_id"] = series_id
        frame["date"] = frame["date"].map(_parse_fred_date)
        frame["realtime_start"] = frame["realtime_start"].map(_parse_fred_date)
        frame["realtime_end"] = frame["realtime_end"].map(_parse_fred_date)
        frame["value"] = pd.to_numeric(frame["value"].replace(".", pd.NA), errors="coerce")
        return frame[["series_id", "date", "value", "realtime_start", "realtime_end"]]

    def get_series_metadata(self, series_id: str) -> dict[str, Any]:
        payload = self._get("/series", {"series_id": series_id})
        seriess = payload.get("seriess", [])
        if not seriess:
            raise FredError(f"FRED returned no metadata for {series_id}")
        metadata = seriess[0]
        return {
            "series_id": series_id,
            "title": metadata.get("title"),
            "frequency": metadata.get("frequency_short") or metadata.get("frequency"),
            "units": metadata.get("units_short") or metadata.get("units"),
            "seasonal_adjustment": metadata.get("seasonal_adjustment_short")
            or metadata.get("seasonal_adjustment"),
            "last_updated": metadata.get("last_updated"),
            "notes": metadata.get("notes") or "",
        }

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.session is not None
        request_params = {
            "api_key": self.api_key,
            "file_type": "json",
            **{key: value for key, value in params.items() if value is not None},
        }
        response = self.session.get(
            f"{self.base_url}{endpoint}",
            params=request_params,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise FredError(f"FRED request failed with HTTP {response.status_code}: {response.text}")
        payload = response.json()
        if "error_message" in payload:
            raise FredError(f"FRED error: {payload['error_message']}")
        return payload


def _parse_fred_date(value: str) -> date:
    return date.fromisoformat(value)
