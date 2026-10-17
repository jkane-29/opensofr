"""US government-securities business-day calendar (SIFMA-style), used for SOFR fixings.

SOFR is published for each US government securities market business day. Weekends and
holidays carry the previous business day's rate. Holiday rules are rule-based (no
external dependency); getting a holiday wrong moves a monthly forward by well under
0.1bp, so this is precise enough for curve building.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

D = dt.date


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> D:
    """n-th (1-based) weekday (Mon=0) of month; n=-1 means last."""
    if n > 0:
        first = D(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + dt.timedelta(days=offset + 7 * (n - 1))
    nxt = D(year + (month == 12), month % 12 + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> D:
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher)
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return D(year, month, day)


def _observed(day: D, saturday_to_friday: bool = True) -> D | None:
    if day.weekday() == 6:  # Sunday -> Monday
        return day + dt.timedelta(days=1)
    if day.weekday() == 5:  # Saturday -> Friday (or not observed)
        return day - dt.timedelta(days=1) if saturday_to_friday else None
    return day


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[D]:
    h = {
        _observed(D(year, 1, 1), saturday_to_friday=False),  # bond market stays open Dec 31
        _nth_weekday(year, 1, 0, 3),    # MLK Day
        _nth_weekday(year, 2, 0, 3),    # Presidents Day
        _easter(year) - dt.timedelta(days=2),  # Good Friday (SIFMA usually full close)
        _nth_weekday(year, 5, 0, -1),   # Memorial Day
        _observed(D(year, 6, 19)),      # Juneteenth
        _observed(D(year, 7, 4)),       # Independence Day
        _nth_weekday(year, 9, 0, 1),    # Labor Day
        _nth_weekday(year, 10, 0, 2),   # Columbus Day
        _observed(D(year, 11, 11), saturday_to_friday=False),  # Veterans Day
        _nth_weekday(year, 11, 3, 4),   # Thanksgiving
        _observed(D(year, 12, 25)),     # Christmas
    }
    h.discard(None)
    # A Saturday Jan 1 of the *next* year is not observed on Dec 31 (bond market open).
    return frozenset(h)


def is_business_day(day: D) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def prev_business_day(day: D) -> D:
    while not is_business_day(day):
        day -= dt.timedelta(days=1)
    return day


def next_business_day(day: D) -> D:
    day += dt.timedelta(days=1)
    while not is_business_day(day):
        day += dt.timedelta(days=1)
    return day


def third_wednesday(year: int, month: int) -> D:
    return _nth_weekday(year, month, 2, 3)


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + n
    return idx // 12, idx % 12 + 1


def month_start(year: int, month: int) -> D:
    return D(year, month, 1)


def month_end_exclusive(year: int, month: int) -> D:
    y, m = add_months(year, month, 1)
    return D(y, m, 1)
