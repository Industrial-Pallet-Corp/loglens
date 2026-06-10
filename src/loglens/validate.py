"""Numeric sanity checks surfaced as review cues (not hard errors).

These catch the most common transcription slips: a row whose end odometer is
below its start, a row whose start doesn't continue the previous row's end, and
a sheet whose stated total miles disagrees with the odometer span or the summed
per-row miles.
"""

from __future__ import annotations

from .models import Sheet, TripRow


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def row_miles(row: TripRow) -> int | None:
    start = _to_int(row.start_miles.value)
    end = _to_int(row.end_miles.value)
    if start is None or end is None:
        return None
    return end - start


def sheet_warnings(sheet: Sheet) -> dict:
    """Return ``{"rows": [[msg, ...], ...], "sheet": [msg, ...]}``."""

    row_msgs: list[list[str]] = []
    prev_end: int | None = None
    summed = 0
    have_any = False

    for row in sheet.rows:
        msgs: list[str] = []
        start = _to_int(row.start_miles.value)
        end = _to_int(row.end_miles.value)
        if start is not None and end is not None:
            if end < start:
                msgs.append("End miles is below start miles.")
            else:
                summed += end - start
                have_any = True
        if prev_end is not None and start is not None and start != prev_end:
            msgs.append(f"Start ({start}) doesn't match previous end ({prev_end}).")
        if end is not None:
            prev_end = end
        row_msgs.append(msgs)

    sheet_msgs: list[str] = []
    beg = _to_int(sheet.beg_odometer.value)
    end_odo = _to_int(sheet.end_odometer.value)
    total = _to_int(sheet.total_miles.value)

    if beg is not None and end_odo is not None:
        span = end_odo - beg
        if span < 0:
            sheet_msgs.append("End odometer is below beginning odometer.")
        elif total is not None and abs(span - total) > 1:
            sheet_msgs.append(
                f"Total miles ({total}) doesn't match odometer span ({span})."
            )
    if total is not None and have_any and abs(summed - total) > 1:
        sheet_msgs.append(
            f"Total miles ({total}) doesn't match the sum of row miles ({summed})."
        )

    return {"rows": row_msgs, "sheet": sheet_msgs}


def has_warnings(warnings: dict) -> bool:
    return bool(warnings["sheet"]) or any(warnings["rows"])
