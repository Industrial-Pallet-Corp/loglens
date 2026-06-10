"""Domain models for trip-log sheets.

A single PDF page maps to one :class:`Sheet`. Each table row is a
:class:`TripRow`. Every processed value is a :class:`FieldValue` carrying the
value itself plus a confidence (0-100), where it came from, and - for values
drawn from a bounded/finite set (e.g. locations) - the ranked next-best
:class:`Alternate` options for one-click swapping in the UI.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

# Bump when the persisted Sheet shape changes; ``load_sheet`` migrates older rows.
SCHEMA_VERSION = 2

# Confidence bands for review shading: below LOW is red ("low confidence"),
# between LOW and MED is yellow ("medium confidence"), above MED is unshaded.
LOW_CONFIDENCE = 65.0
MED_CONFIDENCE = 80.0

# Known short codes for the status column (a small bounded set).
CODE_OPTIONS = ["S", "MT", "CANTS", "CUTSTOCK", "MULCH"]


class Alternate(BaseModel):
    """A ranked candidate value (highest score first)."""

    value: str
    score: float = 0.0
    label: str | None = None
    location_id: str | None = None


class FieldValue(BaseModel):
    """A single processed value plus its confidence and provenance.

    - ``confidence`` is on a 0-100 scale (``None`` when unknown).
    - ``source`` is one of ``ocr`` (model transcription), ``resolver``
      (matched against a bounded set), or ``user`` (human-confirmed).
    - ``ref_id`` is the canonical id of the chosen value when it comes from a
      bounded set (e.g. a location_id).
    - ``raw`` preserves the original OCR reading when reconciliation replaces
      ``value`` with a canonical list entry (used for re-resolve and alias
      learning).
    - ``alternates`` are ranked next-best options for bounded fields.
    """

    value: str | None = None
    confidence: float | None = None
    source: str = "ocr"
    ref_id: str | None = None
    raw: str | None = None
    alternates: list[Alternate] = Field(default_factory=list)

    @classmethod
    def of(
        cls,
        value: str | None,
        confidence: float | None = None,
        *,
        source: str = "ocr",
        ref_id: str | None = None,
        alternates: list[Alternate] | None = None,
    ) -> "FieldValue":
        return cls(
            value=value,
            confidence=confidence,
            source=source,
            ref_id=ref_id,
            alternates=alternates or [],
        )

    def is_low(self, threshold: float = LOW_CONFIDENCE) -> bool:
        return self.confidence is not None and self.confidence < threshold

    def band(self) -> str | None:
        """Confidence band for UI shading: 'low', 'mid', or None."""

        if self.confidence is None:
            return None
        if self.confidence < LOW_CONFIDENCE:
            return "low"
        if self.confidence < MED_CONFIDENCE:
            return "mid"
        return None


class TripRow(BaseModel):
    date: FieldValue = Field(default_factory=FieldValue)
    place_raw: FieldValue = Field(default_factory=FieldValue)  # OCR reading
    place: FieldValue = Field(default_factory=FieldValue)  # resolved location
    start_miles: FieldValue = Field(default_factory=FieldValue)
    end_miles: FieldValue = Field(default_factory=FieldValue)
    trailer_no: FieldValue = Field(default_factory=FieldValue)
    bol_ticket: FieldValue = Field(default_factory=FieldValue)
    code: FieldValue = Field(default_factory=FieldValue)


class Sheet(BaseModel):
    schema_version: int = SCHEMA_VERSION
    page_index: int = 0
    status: str = "pending"  # pending | processing | done | error
    error: str | None = None

    driver: FieldValue = Field(default_factory=FieldValue)
    # A sheet has a single date (the top row); it lives at the sheet level even
    # though the source form repeats a per-row date column.
    date: FieldValue = Field(default_factory=FieldValue)
    truck_no: FieldValue = Field(default_factory=FieldValue)
    rows: list[TripRow] = Field(default_factory=list)
    beg_odometer: FieldValue = Field(default_factory=FieldValue)
    end_odometer: FieldValue = Field(default_factory=FieldValue)
    total_miles: FieldValue = Field(default_factory=FieldValue)

    # Token usage for the extraction call (per sheet).
    input_tokens: int | None = None
    output_tokens: int | None = None


# -- Persistence helpers --------------------------------------------------


def _wrap(value: Any) -> dict[str, Any]:
    """Coerce a legacy scalar field into a FieldValue dict."""

    if isinstance(value, dict):
        return value
    return {"value": value}


def migrate_sheet_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Best-effort migration of a pre-v2 (flat string) sheet payload."""

    if int(data.get("schema_version", 1)) >= 2:
        return data

    for key in ("driver", "truck_no", "beg_odometer", "end_odometer", "total_miles"):
        data[key] = _wrap(data.get(key))

    migrated_rows = []
    for r in data.get("rows", []) or []:
        nr: dict[str, Any] = {}
        for key in (
            "date",
            "place_raw",
            "start_miles",
            "end_miles",
            "trailer_no",
            "bol_ticket",
            "code",
        ):
            nr[key] = _wrap(r.get(key))
        nr["place"] = {
            "value": r.get("place_resolved"),
            "confidence": r.get("place_score"),
            "ref_id": r.get("place_location_id"),
            "source": "resolver",
            "alternates": [
                {
                    "value": a.get("name"),
                    "score": a.get("score", 0.0),
                    "location_id": a.get("location_id"),
                }
                for a in (r.get("place_alternates") or [])
                if a.get("name")
            ],
        }
        migrated_rows.append(nr)

    data["rows"] = migrated_rows
    data["schema_version"] = 2
    return data


def derive_sheet_date(sheet: Sheet) -> None:
    """Populate the sheet-level date from the first dated row, if unset."""

    if sheet.date.value:
        return
    for row in sheet.rows:
        if row.date.value:
            sheet.date = row.date.model_copy(deep=True)
            return


def load_sheet(raw: str) -> Sheet:
    """Deserialize a stored sheet, migrating older payloads as needed."""

    data = json.loads(raw)
    data = migrate_sheet_dict(data)
    sheet = Sheet.model_validate(data)
    derive_sheet_date(sheet)
    return sheet
