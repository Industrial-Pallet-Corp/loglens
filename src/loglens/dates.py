"""Best-guess normalization of handwritten dates to mm/dd/yy.

Trip logs are filled in by hand with whatever convention the driver favors
("6-26", "06.26.25", "2025-06-26", "26/6", ...). Once OCR has produced a
reading, we coerce it into a single mm/dd/yy rendering and pin the year to the
current one - except within a week of a year boundary, where the adjacent year
is also accepted (sheets dated late December get processed in early January,
and vice versa).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .models import Sheet

_YEAR_WINDOW = timedelta(days=7)

# Two or three numeric groups split by common separators; the first group may
# be a 4-digit year (ISO-style yyyy-mm-dd).
_DATE_RE = re.compile(r"(\d{1,4})\s*[-/. ]\s*(\d{1,2})(?:\s*[-/. ]\s*(\d{2,4}))?")


def _allowed_years(today: date) -> list[int]:
    """Years a sheet may legitimately be dated, current year first."""

    years = [today.year]
    if date(today.year + 1, 1, 1) - today <= _YEAR_WINDOW:
        years.append(today.year + 1)
    if today - date(today.year, 1, 1) <= _YEAR_WINDOW:
        years.append(today.year - 1)
    return years


def normalize_date(text: str | None, today: date | None = None) -> str | None:
    """Coerce a raw date reading into "mm/dd/yy", or None if hopeless."""

    today = today or date.today()
    m = _DATE_RE.search(text or "")
    if not m:
        return None

    g1, g2, g3 = m.groups()
    if len(g1) >= 3:  # ISO-style: yyyy-mm-dd
        if not g3:
            return None
        year: int | None = int(g1)
        month, day = int(g2), int(g3)
    else:
        month, day = int(g1), int(g2)
        year = int(g3) if g3 else None
        if year is not None and year < 100:
            year += 2000

    # US m/d convention; swap when the reading only works the other way round.
    if month > 12 and day <= 12:
        month, day = day, month

    allowed = _allowed_years(today)
    if year in allowed:
        candidates = [year]
    elif year is None:
        # No year written: best guess is the allowed year closest to today.
        def distance(y: int) -> int:
            try:
                return abs((date(y, month, day) - today).days)
            except ValueError:
                return 10**9

        candidates = sorted(allowed, key=distance)
    else:
        # Out-of-window year: force the current year, other allowed years as
        # fallback (e.g. Feb 29).
        candidates = sorted(allowed, key=lambda y: y != today.year)

    for y in candidates:
        try:
            dt = date(y, month, day)
        except ValueError:
            continue
        return f"{dt.month:02d}/{dt.day:02d}/{dt.year % 100:02d}"
    return None


def normalize_sheet_dates(sheet: Sheet, today: date | None = None) -> Sheet:
    """Normalize all OCR-sourced date fields on a sheet in place."""

    for fv in (sheet.date, *(row.date for row in sheet.rows)):
        if fv.source == "user" or not fv.value:
            continue
        normalized = normalize_date(fv.value, today)
        if normalized and normalized != fv.value:
            fv.raw = fv.raw or fv.value
            fv.value = normalized
    return sheet


__all__ = ["normalize_date", "normalize_sheet_dates"]
