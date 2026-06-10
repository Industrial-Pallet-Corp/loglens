"""Domain models for trip-log sheets.

A single PDF page maps to one :class:`Sheet`. Each table row is a
:class:`TripRow`. Fields the OCR step was unsure about are listed in
``uncertain_fields`` so the review UI can highlight them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LocationMatch(BaseModel):
    """A candidate location from the cache for a raw place string."""

    name: str
    location_id: str | None = None
    score: float = 0.0


class TripRow(BaseModel):
    date: str | None = None
    place_raw: str | None = None
    start_miles: str | None = None
    end_miles: str | None = None
    trailer_no: str | None = None
    bol_ticket: str | None = None
    code: str | None = None  # S | MT | (blank)

    # Resolution output (filled by the resolver, editable in the UI).
    place_resolved: str | None = None
    place_location_id: str | None = None
    place_score: float | None = None
    place_alternates: list[LocationMatch] = Field(default_factory=list)

    # Names of fields the extractor flagged as low-confidence.
    uncertain_fields: list[str] = Field(default_factory=list)


class Sheet(BaseModel):
    page_index: int = 0
    driver: str | None = None
    truck_no: str | None = None
    rows: list[TripRow] = Field(default_factory=list)
    beg_odometer: str | None = None
    end_odometer: str | None = None
    total_miles: str | None = None
    uncertain_fields: list[str] = Field(default_factory=list)
