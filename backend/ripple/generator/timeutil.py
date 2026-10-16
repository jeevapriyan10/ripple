"""Time and calendar utilities for Ripple synthetic transaction generation.

Provides:
- Timezone conversions (ZoneInfo, pinned to UTC).
- Canonical ISO-8601 formatting with trailing 'Z'.
- Business day calculations (Mon-Fri, non-holiday).
- Diurnal and weekday activity curves.
- Naive timestamp generation for feed quirks.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# 24-hour diurnal activity distribution (local hour 0 to 23)
# Realistic assumption: low activity overnight (02:00-05:00), peaks midday and evening
DEFAULT_DIURNAL_HOURLY: list[float] = [
    0.15,  # 00:00
    0.08,  # 01:00
    0.04,  # 02:00
    0.03,  # 03:00
    0.05,  # 04:00
    0.12,  # 05:00
    0.30,  # 06:00
    0.60,  # 07:00
    0.95,  # 08:00
    1.25,  # 09:00
    1.40,  # 10:00
    1.45,  # 11:00
    1.35,  # 12:00
    1.30,  # 13:00
    1.40,  # 14:00
    1.50,  # 15:00
    1.45,  # 16:00
    1.35,  # 17:00
    1.25,  # 18:00
    1.15,  # 19:00
    1.00,  # 20:00
    0.75,  # 21:00
    0.45,  # 22:00
    0.25,  # 23:00
]

# Weekday activity factors (0=Monday ... 6=Sunday)
DEFAULT_WEEKDAY_FACTORS: list[float] = [
    1.05,  # Mon
    1.10,  # Tue
    1.12,  # Wed
    1.15,  # Thu
    1.20,  # Fri
    0.85,  # Sat
    0.65,  # Sun
]


def to_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and converted to UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_z(dt: datetime) -> str:
    """Format datetime as ISO-8601 with trailing 'Z'."""
    utc_dt = to_utc(dt)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_seconds(dt: datetime) -> int:
    """Return integer Unix epoch timestamp for datetime."""
    return int(to_utc(dt).timestamp())


def to_local(dt: datetime, tz_name: str) -> datetime:
    """Convert UTC datetime to local datetime in the specified IANA timezone."""
    utc_dt = to_utc(dt)
    return utc_dt.astimezone(ZoneInfo(tz_name))


def is_business_day(dt: datetime) -> bool:
    """Return True if Monday through Friday (no holiday calendar)."""
    return to_utc(dt).weekday() < 5


def next_business_day(dt: datetime) -> datetime:
    """Return the start of the next business day (preserving time of day)."""
    current = to_utc(dt)
    while True:
        current += timedelta(days=1)
        if current.weekday() < 5:
            return current


def add_business_days(dt: datetime, days: int) -> datetime:
    """Add a given number of business days, skipping weekends."""
    if days < 0:
        raise ValueError(f"add_business_days days must be non-negative, got {days}")
    current = to_utc(dt)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def diurnal_weight(
    local_hour: int,
    weekday: int,
    hourly_curve: list[float] | None = None,
    weekday_factors: list[float] | None = None,
) -> float:
    """Compute relative activity multiplier for a given local hour (0-23) and weekday (0-6)."""
    h_curve = hourly_curve or DEFAULT_DIURNAL_HOURLY
    w_factors = weekday_factors or DEFAULT_WEEKDAY_FACTORS

    h_idx = max(0, min(23, int(local_hour)))
    w_idx = max(0, min(6, int(weekday)))

    return float(h_curve[h_idx] * w_factors[w_idx])


def render_naive_zone(dt_utc: datetime, tz_name: str) -> str:
    """Render a UTC instant as a naive timestamp string in a target IANA timezone.

    Used by feed quirks to simulate adapters receiving naive timestamps (e.g. IST for UPI).
    Example output: '2026-01-05T14:30:00'
    """
    local_dt = to_local(dt_utc, tz_name)
    return local_dt.strftime("%Y-%m-%dT%H:%M:%S")
