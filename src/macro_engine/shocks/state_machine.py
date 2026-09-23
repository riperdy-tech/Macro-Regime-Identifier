"""MRI Layer 2 (MRI-12) — the §3.4 per-shock state machine.

```
inactive --(severity >= 1)--> active --(severity == 0 for R consecutive trading days)--> decaying
decaying --(intensity < retire_level for R consecutive days)--> inactive   [episode closed]
decaying --(severity >= 1)--> active   [same episode; onset_date unchanged]
any active/decaying --(age_days > sunset_days)--> retired_sunset  [closed with flag; Layer 1 owns it now]
```

`R = 10` trading days. One `ShockEpisodeState` is carried day over day per shock (per leg,
for `rates_shock`'s DFII10/DGS2 pair); `step()` applies exactly one trading day.

The diagram gives no arrow leaving `retired_sunset`. Read literally that would strand a
shock in `retired_sunset` forever once its episode outlives `sunset_days`, even after the
transform fully normalises -- which contradicts §3.4's own reason for a sunset ("handed to
Layer 1 with a flag rather than living in the register forever": a *disposition*, not a
life sentence on future episodes). This implementation treats `retired_sunset` as closing
the current episode the same way `decaying -> inactive` does: once closed, the tracked
state resets to `inactive` so a later severity >= 1 crossing opens a fresh episode (fresh
`onset_date`, fresh sunset clock). The `retired_sunset` flag itself is never erased from
the historical row that recorded it. This is a documented interpretation of an
underspecified corner, not a change to any measured number.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date


INACTIVE = "inactive"
ACTIVE = "active"
DECAYING = "decaying"
RETIRED_SUNSET = "retired_sunset"

R_DAYS = 10


@dataclass(frozen=True)
class ShockEpisodeState:
    state: str = INACTIVE
    onset_date: date | None = None
    zero_streak: int = 0  # consecutive trading days with severity == 0, while ACTIVE
    retire_streak: int = 0  # consecutive trading days retired, while DECAYING
    peak_intensity: float | None = None
    peak_date: date | None = None
    sunset_flagged_on: date | None = None  # last date retired_sunset fired, for the artifact


def step(
    prev: ShockEpisodeState,
    *,
    today: date,
    severity: int,
    retired_today: bool,
    intensity: float | None,
    sunset_days: int,
    r_days: int = R_DAYS,
) -> ShockEpisodeState:
    """Advance one trading day. `retired_today` is `severity.is_retired()` on today's raw
    transform value (see `severity.py`); it is independent of `severity` itself -- a
    shock can be simultaneously not-fired (severity 0) and not yet inside the retire band
    (e.g. credit sitting at +30bp: below the +50bp trigger but above the 0bp retire line)."""
    state = prev.state
    onset_date = prev.onset_date
    zero_streak = prev.zero_streak
    retire_streak = prev.retire_streak
    peak_intensity = prev.peak_intensity
    peak_date = prev.peak_date
    sunset_flagged_on = prev.sunset_flagged_on

    if state == INACTIVE:
        if severity >= 1:
            state = ACTIVE
            onset_date = today
            zero_streak = 0
            retire_streak = 0
            peak_intensity = intensity
            peak_date = today
        # else: stays inactive

    elif state in (ACTIVE, DECAYING):
        # "any active/decaying -- age_days > sunset_days --> retired_sunset" is checked
        # first: it can fire the same day severity crosses back to >= 1.
        age_days = (today - onset_date).days if onset_date is not None else 0
        if age_days > sunset_days:
            state = RETIRED_SUNSET
            sunset_flagged_on = today
        elif state == ACTIVE:
            if severity == 0:
                zero_streak += 1
                if zero_streak >= r_days:
                    state = DECAYING
                    retire_streak = 1 if retired_today else 0
            else:
                zero_streak = 0
        else:  # DECAYING
            if severity >= 1:
                state = ACTIVE
                zero_streak = 0
                retire_streak = 0
            elif retired_today:
                retire_streak += 1
                if retire_streak >= r_days:
                    state = INACTIVE
                    onset_date = None
                    zero_streak = 0
                    retire_streak = 0
                    peak_intensity = None
                    peak_date = None
            else:
                retire_streak = 0

        if state in (ACTIVE, DECAYING) and intensity is not None:
            if peak_intensity is None or intensity > peak_intensity:
                peak_intensity = intensity
                peak_date = today

    elif state == RETIRED_SUNSET:
        # See module docstring: close the episode the same way normal retirement does,
        # then let a fresh severity >= 1 crossing (possibly the very same day) open a new
        # episode with its own onset_date and sunset clock.
        if retired_today and severity == 0:
            state = INACTIVE
            onset_date = None
            zero_streak = 0
            retire_streak = 0
            peak_intensity = None
            peak_date = None
        if severity >= 1:
            state = ACTIVE
            onset_date = today
            zero_streak = 0
            retire_streak = 0
            peak_intensity = intensity
            peak_date = today

    return replace(
        prev,
        state=state,
        onset_date=onset_date,
        zero_streak=zero_streak,
        retire_streak=retire_streak,
        peak_intensity=peak_intensity,
        peak_date=peak_date,
        sunset_flagged_on=sunset_flagged_on,
    )


def age_days(current: ShockEpisodeState, today: date) -> int | None:
    if current.onset_date is None:
        return None
    return (today - current.onset_date).days


def is_active(current: ShockEpisodeState) -> bool:
    """`shocks[].active`: severity >= 1 today, or in `decaying` state (§1.3.4)."""
    return current.state in (ACTIVE, DECAYING)
