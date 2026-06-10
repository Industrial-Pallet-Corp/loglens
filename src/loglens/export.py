"""CSV export reflecting (possibly corrected) sheet values."""

from __future__ import annotations

import csv
import io

from .models import Sheet

FIELDNAMES = [
    "page",
    "driver",
    "truck_no",
    "date",
    "place_raw",
    "place_resolved",
    "location_id",
    "place_score",
    "start_miles",
    "end_miles",
    "miles",
    "trailer_no",
    "bol_ticket",
    "code",
]


def _miles(start: str | None, end: str | None) -> str:
    try:
        return str(int(_digits(end)) - int(_digits(start)))
    except (ValueError, TypeError):
        return ""


def _digits(value: str | None) -> str:
    if not value:
        raise ValueError("empty")
    return "".join(ch for ch in value if ch.isdigit())


def _row_dict(sheet: Sheet, row) -> dict[str, object]:
    return {
        "page": sheet.page_index + 1,
        "driver": sheet.driver or "",
        "truck_no": sheet.truck_no or "",
        "date": row.date or "",
        "place_raw": row.place_raw or "",
        "place_resolved": row.place_resolved or "",
        "location_id": row.place_location_id or "",
        "place_score": row.place_score if row.place_score is not None else "",
        "start_miles": row.start_miles or "",
        "end_miles": row.end_miles or "",
        "miles": _miles(row.start_miles, row.end_miles),
        "trailer_no": row.trailer_no or "",
        "bol_ticket": row.bol_ticket or "",
        "code": row.code or "",
    }


def sheet_to_csv(sheet: Sheet) -> str:
    return _write([(sheet, row) for row in sheet.rows])


def sheets_to_csv(sheets: list[Sheet]) -> str:
    pairs = [(sheet, row) for sheet in sheets for row in sheet.rows]
    return _write(pairs)


def _write(pairs: list[tuple[Sheet, object]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    writer.writeheader()
    for sheet, row in pairs:
        writer.writerow(_row_dict(sheet, row))
    return buf.getvalue()
