from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import requests


class FredError(RuntimeError):
    """Raised when FRED returns an error or unusable payload."""


# Retried on transient FRED responses (rate limit + server errors).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FredClient:
    api_key: str
    base_url: str = "https://api.stlouisfed.org/fred"
    timeout: int = 30
    session: requests.Session | None = None
    max_retries: int = 5
    backoff_base_seconds: float = 1.0
    backoff_cap_seconds: float = 30.0
    # ALFRED vintages are served by the SAME FRED API namespace; only the
    # realtime_start/realtime_end request parameters select a vintage. The field
    # exists so a deployment can point at a mirror without touching call sites.
    alfred_base_url: str | None = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise FredError("FRED_API_KEY is required for live ingestion")
        if self.session is None:
            self.session = requests.Session()
        if self.alfred_base_url is None:
            self.alfred_base_url = self.base_url

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
        return _observation_frame(observations, series_id)

    def get_series_observations_vintage(
        self,
        series_id: str,
        as_of: str,
        observation_start: str | None = None,
        observation_end: str | None = None,
    ) -> pd.DataFrame:
        """Observations for `series_id` AS KNOWN on `as_of` (an ALFRED vintage).

        realtime_start == realtime_end == as_of asks FRED for the vintage that was
        current on that single day, which is the point-in-time question: "what was
        published then", not "what do we believe about then now".

        Returns an EMPTY frame (not an error) when the series had no vintage on that
        date: a series that did not yet exist is a fact about the past, not a fault.
        """
        payload = self._get(
            "/series/observations",
            {
                "series_id": series_id,
                "observation_start": observation_start,
                "observation_end": observation_end,
                "realtime_start": as_of,
                "realtime_end": as_of,
            },
        )
        observations = payload.get("observations", [])
        if not observations:
            return _empty_observation_frame()
        return _observation_frame(observations, series_id)

    def get_vintage_dates(
        self,
        series_id: str,
        realtime_start: str | None = None,
        realtime_end: str | None = None,
    ) -> list[str]:
        """Sorted ISO dates on which `series_id` was revised (ALFRED vintagedates)."""
        payload = self._get(
            "/series/vintagedates",
            {
                "series_id": series_id,
                "realtime_start": realtime_start,
                "realtime_end": realtime_end,
            },
        )
        dates = payload.get("vintage_dates", [])
        return sorted({str(value) for value in dates})

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
        url = f"{self._base_for(params)}{endpoint}"
        for attempt in range(self.max_retries + 1):
            response = self.session.get(url, params=request_params, timeout=self.timeout)
            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                time.sleep(self._retry_delay(response, attempt))
                continue
            if response.status_code >= 400:
                raise FredError(
                    f"FRED request failed with HTTP {response.status_code}: {response.text}"
                )
            payload = response.json()
            if "error_message" in payload:
                raise FredError(f"FRED error: {payload['error_message']}")
            return payload
        # Exhausted retries on a retryable status.
        raise FredError(
            f"FRED request failed with HTTP {response.status_code} after "
            f"{self.max_retries} retries: {response.text}"
        )

    def _base_for(self, params: dict[str, Any]) -> str:
        """Vintage-aware requests go to the ALFRED host, everything else to FRED."""
        if params.get("realtime_start") or params.get("realtime_end"):
            return self.alfred_base_url or self.base_url
        return self.base_url

    def _retry_delay(self, response: requests.Response, attempt: int) -> float:
        """Honor Retry-After when present, else exponential backoff (capped)."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_cap_seconds)
            except ValueError:
                pass
        return min(self.backoff_base_seconds * (2**attempt), self.backoff_cap_seconds)


def _observation_frame(observations: list[dict[str, Any]], series_id: str) -> pd.DataFrame:
    frame = pd.DataFrame(observations)
    frame["series_id"] = series_id
    frame["date"] = frame["date"].map(_parse_fred_date)
    frame["realtime_start"] = frame["realtime_start"].map(_parse_fred_date)
    frame["realtime_end"] = frame["realtime_end"].map(_parse_fred_date)
    frame["value"] = pd.to_numeric(frame["value"].replace(".", pd.NA), errors="coerce")
    return frame[["series_id", "date", "value", "realtime_start", "realtime_end"]]


def _empty_observation_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "value": pd.Series(dtype="float64"),
            "realtime_start": pd.Series(dtype="datetime64[ns]"),
            "realtime_end": pd.Series(dtype="datetime64[ns]"),
        }
    )


def _parse_fred_date(value: str) -> date:
    return date.fromisoformat(value)
