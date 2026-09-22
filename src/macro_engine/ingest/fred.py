from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import requests


class FredError(RuntimeError):
    """Raised when FRED returns an error or unusable payload."""


class NoVintageAvailable(FredError):
    """ALFRED holds no vintage for this series/date range.

    A fact about the archive, not a failure: asking for a 1995 vintage of a series ALFRED only
    began archiving in 2005 is a well-formed question with the answer "no". ALFRED reports it as
    HTTP 400 with "The series does not exist in ALFRED", which would otherwise be recorded as a
    failed fetch -- thousands of phantom failures in a full-history backfill, burying the real
    ones. It is a distinct type so callers can count it as an empty vintage.
    """


# ALFRED's wording for "this series has no archive covering the requested realtime range".
_NOT_IN_ALFRED = "does not exist in ALFRED"


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
        date: a series that did not yet exist is a fact about the past, not a fault. ALFRED
        reports that case as HTTP 400 rather than an empty list, so it is caught here and
        converted -- the contract above is what callers actually depend on.
        """
        try:
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
        except NoVintageAvailable:
            return _empty_observation_frame()
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
        """One FRED/ALFRED request, retried on every TRANSIENT failure class.

        Retrying only on retryable HTTP statuses was not enough: a read timeout or a dropped
        connection raises out of `session.get` before any status exists, and the exception is a
        `requests` error rather than a `FredError`. A multi-hour vintage backfill (~6,500
        requests) then died outright on one flaky socket, because the caller's per-series
        `except FredError` could not see it. Transport faults and truncated bodies are now
        retried exactly like 429/5xx, and — once retries are exhausted — surfaced AS a
        `FredError`, so callers that already tolerate a failed fetch keep running.
        """
        assert self.session is not None
        request_params = {
            "api_key": self.api_key,
            "file_type": "json",
            **{key: value for key, value in params.items() if value is not None},
        }
        url = f"{self._base_for(params)}{endpoint}"
        last_error = "no attempt was made"

        def _exhausted(reason: str) -> FredError:
            return FredError(
                f"FRED request to {endpoint} failed after {self.max_retries} retries: {reason}"
            )

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, params=request_params, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.max_retries:
                    time.sleep(self._backoff_seconds(attempt))
                    continue
                raise _exhausted(last_error) from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                time.sleep(self._retry_delay(response, attempt))
                continue
            if response.status_code >= 400:
                if response.status_code == 400 and _NOT_IN_ALFRED in response.text:
                    raise NoVintageAvailable(
                        f"ALFRED has no archive for {params.get('series_id')} over "
                        f"{params.get('realtime_start')}..{params.get('realtime_end')}"
                    )
                raise FredError(
                    f"FRED request failed with HTTP {response.status_code}: {response.text}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                # A truncated/garbled body is a transport fault, not a data verdict.
                last_error = f"invalid JSON payload: {exc}"
                if attempt < self.max_retries:
                    time.sleep(self._backoff_seconds(attempt))
                    continue
                raise _exhausted(last_error) from exc
            if "error_message" in payload:
                raise FredError(f"FRED error: {payload['error_message']}")
            return payload
        # Unreachable in practice: every branch above returns or raises.
        raise _exhausted(last_error)

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
        return self._backoff_seconds(attempt)

    def _backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff for failures that carry no response headers."""
        return min(self.backoff_base_seconds * (2**attempt), self.backoff_cap_seconds)


def _observation_frame(observations: list[dict[str, Any]], series_id: str) -> pd.DataFrame:
    frame = pd.DataFrame(observations)
    frame["series_id"] = series_id
    frame["date"] = frame["date"].map(_parse_fred_date)
    for bound in ("realtime_start", "realtime_end"):
        if bound not in frame.columns:
            frame[bound] = None
        frame[bound] = frame[bound].map(_parse_realtime_bound)
    missing_start = int(frame["realtime_start"].isna().sum())
    if missing_start:
        # NEVER fall back to the fetch date here: the empirical publication index used by
        # point-in-time scoring takes the MIN stored realtime_start per (series, date), so one
        # falsely-recent row (e.g. "today") poisons it and reads as published far later than
        # it actually was. Storing None instead means the row is excluded from PIT evidence
        # rather than silently misdating it.
        print(
            f"fred: {series_id} — {missing_start} observation(s) had no usable realtime_start "
            "in the response; stored as None, never the fetch date",
            flush=True,
        )
    frame["value"] = pd.to_numeric(frame["value"].replace(".", pd.NA), errors="coerce")
    return frame[["series_id", "date", "value", "realtime_start", "realtime_end"]]


def _parse_realtime_bound(value: Any) -> date | None:
    """Parse one realtime_start/realtime_end bound from the FRED/ALFRED response.

    The bound must come from the response itself. A response that omits it, or sends
    something unparsable, becomes None -- never the day the request happened to run.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return _parse_fred_date(value)
    except (TypeError, ValueError):
        return None


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
