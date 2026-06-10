"""Fuzzy location matching with shorthand alias expansion."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from ..db import Database
from ..models import LocationMatch
from ..resources import default_aliases_file

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""

    return _PUNCT.sub(" ", text.lower()).strip()


def load_aliases(path: str | Path | None = None) -> dict[str, str]:
    """Load a shorthand->canonical map, keyed by normalized shorthand."""

    p = Path(path).expanduser() if path else default_aliases_file()
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {normalize(str(k)): str(v) for k, v in raw.items()}


class LocationMatcher:
    def __init__(
        self,
        db: Database,
        aliases: dict[str, str],
        threshold: int = 70,
        max_alternates: int = 5,
    ):
        self.db = db
        self.aliases = aliases
        self.threshold = threshold
        self.max_alternates = max_alternates
        self._cache: list[dict] | None = None

    # The location list rarely changes during a request; cache it in memory.
    def invalidate(self) -> None:
        self._cache = None

    def _locations(self) -> list[dict]:
        if self._cache is None:
            self._cache = self.db.all_locations()
        return self._cache

    def normalize(self, text: str) -> str:
        return normalize(text)

    def match(self, raw: str) -> tuple[LocationMatch | None, list[LocationMatch]]:
        locations = self._locations()
        if not locations:
            return None, []

        norm = normalize(raw)
        alias_target = self.aliases.get(norm)
        query = normalize(alias_target) if alias_target else norm

        choices = {i: loc["normalized"] for i, loc in enumerate(locations)}
        results = process.extract(
            query,
            choices,
            scorer=fuzz.WRatio,
            limit=self.max_alternates + 1,
        )

        matches: list[LocationMatch] = []
        for _matched_text, score, idx in results:
            loc = locations[idx]
            # An exact alias hit is authoritative; pin its confidence high.
            if alias_target and loc["normalized"] == query:
                score = max(score, 97.0)
            matches.append(
                LocationMatch(
                    name=loc["name"],
                    location_id=loc["location_id"],
                    score=round(float(score), 1),
                )
            )

        matches.sort(key=lambda m: m.score, reverse=True)
        best = matches[0] if matches and matches[0].score >= self.threshold else None
        alternates = matches[: self.max_alternates]
        return best, alternates
