"""Time helpers.

All SLA arithmetic in this system runs through here rather than being done by the
model. Business-hours maths is the reason: the dataset snapshot is a Sunday, so a
naive "hours elapsed" calculation silently breaches every business-hours target.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .config import BUSINESS_DAYS, BUSINESS_HOURS_END, BUSINESS_HOURS_START, IST


def parse_dt(value) -> datetime | None:
    """Parse the workbook's 'YYYY-MM-DD HH:MM' strings (and real datetimes) as IST."""
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    except ValueError:
        return None


def fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M IST") if dt else None


def is_business_day(dt: datetime) -> bool:
    return dt.weekday() in BUSINESS_DAYS


def _day_window(dt: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(dt.date(), time(BUSINESS_HOURS_START), tzinfo=IST)
    end = datetime.combine(dt.date(), time(BUSINESS_HOURS_END), tzinfo=IST)
    return start, end


def business_minutes_between(start: datetime, end: datetime) -> int:
    """Minutes of support coverage between two instants (Mon-Fri, 09:00-18:00 IST)."""
    if end <= start:
        return 0
    total = 0
    cursor = start
    # Guard against runaway loops on absurd inputs (~2 years of days).
    for _ in range(800):
        if cursor >= end:
            break
        day_start, day_end = _day_window(cursor)
        if is_business_day(cursor):
            span_start = max(cursor, day_start)
            span_end = min(end, day_end)
            if span_end > span_start:
                total += int((span_end - span_start).total_seconds() // 60)
        cursor = datetime.combine(
            (cursor + timedelta(days=1)).date(), time(0, 0), tzinfo=IST
        )
    return total


def add_business_minutes(start: datetime, minutes: int) -> datetime:
    """The instant `minutes` of support coverage after `start`."""
    remaining = minutes
    cursor = start
    for _ in range(800):
        day_start, day_end = _day_window(cursor)
        if not is_business_day(cursor) or cursor >= day_end:
            cursor = datetime.combine(
                (cursor + timedelta(days=1)).date(), time(0, 0), tzinfo=IST
            )
            continue
        span_start = max(cursor, day_start)
        available = int((day_end - span_start).total_seconds() // 60)
        if remaining <= available:
            return span_start + timedelta(minutes=remaining)
        remaining -= available
        cursor = datetime.combine(
            (cursor + timedelta(days=1)).date(), time(0, 0), tzinfo=IST
        )
    return cursor


def add_business_days(start: datetime, days: int) -> datetime:
    return add_business_minutes(start, days * (BUSINESS_HOURS_END - BUSINESS_HOURS_START) * 60)


@dataclass(frozen=True)
class Duration:
    """An SLA target expressed in the units the source documents actually use."""

    amount: float
    unit: str  # "minutes" | "hours" | "business_hours" | "business_days"
    clock: str  # "24x7" | "business"

    @property
    def label(self) -> str:
        amount = int(self.amount) if float(self.amount).is_integer() else self.amount
        noun = {
            "minutes": "minute",
            "hours": "hour",
            "business_hours": "business hour",
            "business_days": "business day",
        }[self.unit]
        plural = "" if amount == 1 else "s"
        suffix = ", 24x7" if self.clock == "24x7" and self.unit in {"minutes", "hours"} else ""
        return f"{amount} {noun}{plural}{suffix}"

    def deadline_from(self, start: datetime) -> datetime:
        if self.clock == "24x7":
            factor = 1 if self.unit == "minutes" else 60
            return start + timedelta(minutes=self.amount * factor)
        if self.unit == "business_days":
            return add_business_days(start, int(self.amount))
        minutes = self.amount * (1 if self.unit == "minutes" else 60)
        return add_business_minutes(start, int(minutes))

    def elapsed_from(self, start: datetime, now: datetime) -> int:
        """Elapsed time in minutes, measured on this target's own clock."""
        if self.clock == "24x7":
            return max(0, int((now - start).total_seconds() // 60))
        return business_minutes_between(start, now)

    def budget_minutes(self) -> int:
        if self.unit == "minutes":
            return int(self.amount)
        if self.unit == "hours":
            return int(self.amount * 60)
        if self.unit == "business_hours":
            return int(self.amount * 60)
        return int(self.amount) * (BUSINESS_HOURS_END - BUSINESS_HOURS_START) * 60


def humanise_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"
