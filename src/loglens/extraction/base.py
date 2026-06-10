"""Extractor interface and the shared prompt / structured-output contract.

All providers receive PNG/JPEG bytes of one rendered page and return a
:class:`~loglens.models.Sheet`. Extraction uses a forced tool call so the model
returns a typed object instead of free-form text: every field is an object with
a ``value``, a ``confidence`` (0-100), and optional alternate readings. This
feeds the confidence-first data model directly.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..models import Alternate, FieldValue, Sheet, TripRow

TOOL_NAME = "record_trip_log_sheet"

SYSTEM_PROMPT = """\
You are a meticulous data-entry assistant. You are given a scanned image of a
single trucking "trip log" sheet with handwritten entries. Transcribe it EXACTLY
as written. Do not invent values. The handwriting is often messy and may use
shorthand/abbreviations for the Place column - transcribe what you see; do NOT
expand abbreviations.

The sheet has:
- A header with "Driver" (name) and "Truck #".
- A table with columns, in order: Date, Place (location, often shorthand),
  Start Miles, End Miles, Trailer #, BOL # or Blue Ticket #, and a status Code
  ("S" = Scrap, "MT" = Empty, or blank).
- A footer with "Beg. Odometer", "End Odometer", "Total Miles".

Record the data by calling the `record_trip_log_sheet` tool. For EVERY field:
- Put your best transcription in `value` (use null for truly blank cells).
- Set `confidence` from 0-100 reflecting how sure you are of that specific cell
  (low for messy/ambiguous handwriting, high for clearly printed values).
- When a cell is ambiguous, list other plausible readings in `alternates`.
Only include rows that contain handwriting; skip fully blank rows."""

USER_PROMPT = "Transcribe this trip-log sheet by calling the tool."

# A single field: value + confidence + optional alternate readings.
_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {"type": ["string", "null"]},
        # strict custom tools don't support numeric `minimum`/`maximum`; the
        # 0-100 range is enforced via the system prompt instead.
        "confidence": {
            "type": "number",
            "description": "Confidence from 0 (guess) to 100 (certain).",
        },
        "alternates": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["value", "confidence", "alternates"],
    "additionalProperties": False,
}

_ROW_FIELDS = [
    "date",
    "place",
    "start_miles",
    "end_miles",
    "trailer_no",
    "bol_ticket",
    "code",
]

_HEADER_FIELDS = ["driver", "truck_no", "beg_odometer", "end_odometer", "total_miles"]

TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Record the transcribed contents of one trip-log sheet.",
    # NB: we intentionally do not use strict mode. This schema repeats a
    # {value, confidence, alternates} object across ~12 fields, which compiles
    # to a grammar too large for strict tools ("compiled grammar is too large").
    # tool_choice still forces the call and the schema still guides output; the
    # lenient parser + re-ask handle any deviation.
    "input_schema": {
        "type": "object",
        "properties": {
            **{name: _FIELD_SCHEMA for name in _HEADER_FIELDS},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {name: _FIELD_SCHEMA for name in _ROW_FIELDS},
                    "required": _ROW_FIELDS,
                    "additionalProperties": False,
                },
            },
        },
        "required": [*_HEADER_FIELDS, "rows"],
        "additionalProperties": False,
    },
}

TOOL_CHOICE: dict[str, Any] = {"type": "tool", "name": TOOL_NAME}


class ExtractionParseError(Exception):
    """Raised when a model response can't be parsed into a Sheet."""


class Extractor(Protocol):
    def extract(self, png_bytes: bytes, page_index: int) -> Sheet: ...

    def verify(self) -> tuple[bool, str]: ...


def _fv(raw: Any) -> FieldValue:
    """Build a FieldValue from a structured field object (or bare string)."""

    if raw is None:
        return FieldValue()
    if isinstance(raw, str):
        return FieldValue(value=raw or None, source="ocr")
    value = raw.get("value")
    conf = raw.get("confidence")
    alts = [
        Alternate(value=a)
        for a in (raw.get("alternates") or [])
        if isinstance(a, str) and a.strip()
    ]
    return FieldValue(
        value=(value or None),
        confidence=float(conf) if conf is not None else None,
        source="ocr",
        alternates=alts,
    )


def parse_sheet_payload(data: dict[str, Any], page_index: int) -> Sheet:
    """Build a Sheet from the tool's structured input object."""

    rows = []
    for r in data.get("rows", []) or []:
        rows.append(
            TripRow(
                date=_fv(r.get("date")),
                place_raw=_fv(r.get("place")),
                start_miles=_fv(r.get("start_miles")),
                end_miles=_fv(r.get("end_miles")),
                trailer_no=_fv(r.get("trailer_no")),
                bol_ticket=_fv(r.get("bol_ticket")),
                code=_fv(r.get("code")),
            )
        )
    return Sheet(
        page_index=page_index,
        status="done",
        driver=_fv(data.get("driver")),
        truck_no=_fv(data.get("truck_no")),
        beg_odometer=_fv(data.get("beg_odometer")),
        end_odometer=_fv(data.get("end_odometer")),
        total_miles=_fv(data.get("total_miles")),
        rows=rows,
    )


def parse_sheet_json(payload: str | dict[str, Any], page_index: int) -> Sheet:
    """Fallback: parse a JSON payload returned as text into a Sheet."""

    data = payload if isinstance(payload, dict) else _loads_lenient(payload)
    return parse_sheet_payload(data, page_index)


def _loads_lenient(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)
