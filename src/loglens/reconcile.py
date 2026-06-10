"""Reconciliation against the curated reference lists.

Four internally curated "source of truth" sets (locations, drivers, trucks,
trailers) live in SQLite, start empty, and grow from user corrections. When a
sheet is processed, each raw OCR reading is reconciled in this order:

1. Learned alias exact hit (a raw->canonical pairing recorded from an earlier
   correction) -> canonical value, high confidence.
2. Fuzzy match against the kind's list at/above the configured threshold ->
   canonical value with the match score.
3. No match -> the raw reading passes through unchanged, keeping the OCR
   confidence, with any below-threshold candidates offered as alternates.

User-confirmed fields (``source == "user"``) are never clobbered.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process

from .config import ResolverConfig
from .db import REF_KINDS, Database
from .models import CODE_OPTIONS, Alternate, FieldValue, Sheet

KIND_LABELS = {
    "location": "Locations",
    "driver": "Drivers",
    "truck": "Trucks",
    "trailer": "Trailers",
}

# Confidence assigned to a learned-alias exact hit (authoritative).
ALIAS_CONFIDENCE = 98.0

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""

    return _PUNCT.sub(" ", text.lower()).strip()


class Reconciler:
    def __init__(self, db: Database, cfg: ResolverConfig):
        self.db = db
        self.threshold = cfg.match_threshold
        self.max_alternates = cfg.max_alternates
        # Lists rarely change within a request; cache per kind in memory.
        self._entries: dict[str, list[dict]] = {}
        self._aliases: dict[str, dict[str, dict]] = {}

    def invalidate(self) -> None:
        self._entries.clear()
        self._aliases.clear()

    def entries(self, kind: str) -> list[dict]:
        if kind not in self._entries:
            self._entries[kind] = self.db.list_ref_values(kind)
        return self._entries[kind]

    def values(self, kind: str) -> list[str]:
        return [e["value"] for e in self.entries(kind)]

    def aliases(self, kind: str) -> dict[str, dict]:
        if kind not in self._aliases:
            self._aliases[kind] = self.db.alias_map(kind)
        return self._aliases[kind]

    # -- Matching ----------------------------------------------------------
    def candidates(self, kind: str, raw: str) -> list[Alternate]:
        """Ranked fuzzy candidates from the kind's list (highest score first)."""

        entries = self.entries(kind)
        if not entries:
            return []
        choices = {i: e["normalized"] for i, e in enumerate(entries)}
        results = process.extract(
            normalize(raw),
            choices,
            scorer=fuzz.WRatio,
            limit=self.max_alternates,
        )
        out = [
            Alternate(
                value=entries[idx]["value"],
                location_id=str(entries[idx]["id"]),
                score=round(float(score), 1),
            )
            for _text, score, idx in results
        ]
        out.sort(key=lambda a: a.score, reverse=True)
        return out

    # -- Reconciliation ------------------------------------------------------
    def reconcile_field(self, kind: str, fv: FieldValue, raw: str | None) -> None:
        """Apply alias > fuzzy > passthrough resolution onto ``fv``."""

        if fv.source == "user":
            return  # never clobber a human-confirmed value
        raw = (raw or "").strip()
        if not raw:
            return

        fv.raw = raw
        cands = self.candidates(kind, raw)

        alias = self.aliases(kind).get(normalize(raw))
        if alias:
            fv.value = alias["value"]
            fv.ref_id = str(alias["ref_value_id"])
            fv.confidence = ALIAS_CONFIDENCE
            fv.source = "resolver"
            fv.alternates = [a for a in cands if a.value != alias["value"]][:3]
            return

        if cands and cands[0].score >= self.threshold:
            best = cands[0]
            fv.value = best.value
            fv.ref_id = best.location_id
            fv.confidence = best.score
            fv.source = "resolver"
            fv.alternates = cands[1 : 1 + 3]
            return

        # Passthrough: the raw reading stands, with its OCR confidence.
        if not fv.value:
            fv.value = raw
        fv.ref_id = None
        fv.source = "ocr"
        fv.alternates = cands[:3]

    def reconcile_sheet(self, sheet: Sheet) -> Sheet:
        self.reconcile_field("driver", sheet.driver, sheet.driver.raw or sheet.driver.value)
        self.reconcile_field("truck", sheet.truck_no, sheet.truck_no.raw or sheet.truck_no.value)

        for row in sheet.rows:
            if row.place_raw.value:
                # The place field starts empty; seed its OCR confidence from
                # the raw reading before reconciling.
                if row.place.source != "user" and not row.place.value:
                    row.place.confidence = row.place_raw.confidence
                self.reconcile_field("location", row.place, row.place_raw.value)
            self.reconcile_field(
                "trailer", row.trailer_no, row.trailer_no.raw or row.trailer_no.value
            )

            # The status code is a small fixed set: offer alternates.
            if row.code.source != "user":
                current = (row.code.value or "").strip().upper()
                row.code.alternates = [
                    Alternate(value=opt, score=0.0)
                    for opt in CODE_OPTIONS
                    if opt != current
                ]
        return sheet

    # -- Learning ------------------------------------------------------------
    def learn(self, kind: str, value: str | None, raw: str | None = None) -> int | None:
        """Ensure ``value`` is in the kind's list; record ``raw`` as an alias.

        Returns the canonical entry's id, or None when value is empty.
        """

        value = (value or "").strip()
        norm_value = normalize(value)
        if not norm_value:
            return None
        ref_id = self.db.add_ref_value(kind, value, norm_value)

        raw = (raw or "").strip()
        norm_raw = normalize(raw)
        if norm_raw and norm_raw != norm_value:
            self.db.add_ref_alias(kind, raw, norm_raw, ref_id)

        self.invalidate()
        return ref_id


__all__ = ["Reconciler", "normalize", "REF_KINDS", "KIND_LABELS"]
