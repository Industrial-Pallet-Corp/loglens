"""Extractor interface and the shared prompt/JSON contract.

All providers receive PNG bytes of one rendered page and return a
:class:`~loglens.models.Sheet`. The JSON schema below is the contract we ask
the model to follow; it mirrors the trip-log sheet layout.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..models import Sheet, TripRow

# Column semantics described to the model. Kept here so every provider shares
# the exact same instructions.
SYSTEM_PROMPT = """\
You are a meticulous data-entry assistant. You are given a scanned image of a
single trucking "trip log" sheet with handwritten entries. Transcribe it EXACTLY
as written. Do not invent values. The handwriting is often messy and may use
shorthand/abbreviations for the Place column - transcribe what you see; do not
expand abbreviations.

The sheet has:
- A header with "Driver" (name) and "Truck #".
- A table with columns, in order:
    1. Date
    2. Place (location, often shorthand/abbreviated)
    3. Start Miles
    4. End Miles
    5. Trailer #
    6. BOL # or Blue Ticket # (Mulch/Cutstock/Cants Transfer)
    7. Code: "S" (Scrap), "MT" (Empty), or blank
- A footer with "Beg. Odometer", "End Odometer", "Total Miles".

Return ONLY a JSON object (no prose, no markdown fences) matching this schema:
{
  "driver": string|null,
  "truck_no": string|null,
  "beg_odometer": string|null,
  "end_odometer": string|null,
  "total_miles": string|null,
  "rows": [
    {
      "date": string|null,
      "place_raw": string|null,
      "start_miles": string|null,
      "end_miles": string|null,
      "trailer_no": string|null,
      "bol_ticket": string|null,
      "code": string|null,
      "uncertain_fields": [string]
    }
  ],
  "uncertain_fields": [string]
}

Only include rows that contain handwriting; skip fully blank rows. For any field
you cannot read confidently, still provide your best guess but add the field's
name to that row's "uncertain_fields" list (use "driver"/"truck_no" etc. in the
top-level "uncertain_fields" for header fields).
"""

USER_PROMPT = "Transcribe this trip-log sheet into the JSON schema."


class Extractor(Protocol):
    def extract(self, png_bytes: bytes, page_index: int) -> Sheet: ...


def parse_sheet_json(payload: str | dict[str, Any], page_index: int) -> Sheet:
    """Validate a model's JSON payload into a :class:`Sheet`.

    Tolerant of stray markdown fences or leading/trailing prose.
    """

    data = payload if isinstance(payload, dict) else _loads_lenient(payload)
    rows = [TripRow.model_validate(r) for r in data.get("rows", []) or []]
    return Sheet(
        page_index=page_index,
        driver=data.get("driver"),
        truck_no=data.get("truck_no"),
        beg_odometer=data.get("beg_odometer"),
        end_odometer=data.get("end_odometer"),
        total_miles=data.get("total_miles"),
        rows=rows,
        uncertain_fields=list(data.get("uncertain_fields", []) or []),
    )


def _loads_lenient(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences if the model added them.
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)
