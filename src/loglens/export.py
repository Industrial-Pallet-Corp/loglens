"""CSV export reflecting (possibly corrected) sheet values.

Each processed value carries a confidence; exports include the core values
plus a parallel set of ``*_confidence`` columns so downstream consumers can
see how trustworthy each cell is.
"""

from __future__ import annotations

import csv
import io

from .models import FieldValue, Sheet, TripRow

BASE_FIELDS = [
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

CONFIDENCE_FIELDS = [
    "driver_confidence",
    "truck_no_confidence",
    "date_confidence",
    "place_raw_confidence",
    "start_miles_confidence",
    "end_miles_confidence",
    "trailer_no_confidence",
    "bol_ticket_confidence",
    "code_confidence",
]


def _conf(fv: FieldValue) -> str:
    return "" if fv.confidence is None else str(round(fv.confidence, 1))


def _miles(start: str | None, end: str | None) -> str:
    try:
        return str(int(_digits(end)) - int(_digits(start)))
    except (ValueError, TypeError):
        return ""


def _digits(value: str | None) -> str:
    if not value:
        raise ValueError("empty")
    return "".join(ch for ch in value if ch.isdigit())


def _row_dict(sheet: Sheet, row: TripRow, include_confidence: bool) -> dict[str, object]:
    out: dict[str, object] = {
        "page": sheet.page_index + 1,
        "driver": sheet.driver.value or "",
        "truck_no": sheet.truck_no.value or "",
        "date": sheet.date.value or row.date.value or "",
        "place_raw": row.place_raw.value or "",
        "place_resolved": row.place.value or "",
        "location_id": row.place.ref_id or "",
        "place_score": "" if row.place.confidence is None else row.place.confidence,
        "start_miles": row.start_miles.value or "",
        "end_miles": row.end_miles.value or "",
        "miles": _miles(row.start_miles.value, row.end_miles.value),
        "trailer_no": row.trailer_no.value or "",
        "bol_ticket": row.bol_ticket.value or "",
        "code": row.code.value or "",
    }
    if include_confidence:
        out.update(
            {
                "driver_confidence": _conf(sheet.driver),
                "truck_no_confidence": _conf(sheet.truck_no),
                "date_confidence": _conf(sheet.date if sheet.date.value else row.date),
                "place_raw_confidence": _conf(row.place_raw),
                "start_miles_confidence": _conf(row.start_miles),
                "end_miles_confidence": _conf(row.end_miles),
                "trailer_no_confidence": _conf(row.trailer_no),
                "bol_ticket_confidence": _conf(row.bol_ticket),
                "code_confidence": _conf(row.code),
            }
        )
    return out


def sheet_to_csv(sheet: Sheet, include_confidence: bool = True) -> str:
    return _write([(sheet, row) for row in sheet.rows], include_confidence)


def sheets_to_csv(sheets: list[Sheet], include_confidence: bool = True) -> str:
    pairs = [(sheet, row) for sheet in sheets for row in sheet.rows]
    return _write(pairs, include_confidence)


def _write(pairs: list[tuple[Sheet, TripRow]], include_confidence: bool) -> str:
    fieldnames = BASE_FIELDS + (CONFIDENCE_FIELDS if include_confidence else [])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for sheet, row in pairs:
        writer.writerow(_row_dict(sheet, row, include_confidence))
    return buf.getvalue()
