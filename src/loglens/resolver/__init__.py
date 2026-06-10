"""Location resolution.

The resolver matches a raw (often shorthand) place string to a canonical
location from the cache. The cache is filled by a :class:`LocationsSource`:
Phase 1 ships a seed-file source; Phase 2 will add a Redshift source with the
same interface, so nothing else changes.
"""

from __future__ import annotations

from ..config import ResolverConfig
from ..db import Database
from .matcher import LocationMatcher, load_aliases
from .sources import LocationsSource, SeedSource


def build_source(cfg: ResolverConfig) -> LocationsSource:
    source = cfg.source.lower()
    if source == "seed":
        return SeedSource(cfg.seed_file)
    raise ValueError(
        f"Unknown resolver source: {cfg.source!r} (Phase 1 supports 'seed')"
    )


class Resolver:
    def __init__(self, db: Database, cfg: ResolverConfig):
        self.db = db
        self.cfg = cfg
        self.source = build_source(cfg)
        self.aliases = load_aliases(cfg.aliases_file)
        self.matcher = LocationMatcher(
            db,
            aliases=self.aliases,
            threshold=cfg.match_threshold,
            max_alternates=cfg.max_alternates,
        )

    def refresh_cache(self) -> int:
        """Load locations from the configured source into the SQLite cache."""

        records = self.source.load()
        rows = [
            (loc_id, name, self.matcher.normalize(name)) for (loc_id, name) in records
        ]
        count = self.db.replace_locations(rows, source=self.cfg.source)
        self.matcher.invalidate()
        return count

    def ensure_cache(self) -> None:
        if self.db.location_count() == 0:
            self.refresh_cache()

    def resolve_sheet(self, sheet):
        for row in sheet.rows:
            if not row.place_raw:
                continue
            best, alternates = self.matcher.match(row.place_raw)
            row.place_alternates = alternates
            if best:
                row.place_resolved = best.name
                row.place_location_id = best.location_id
                row.place_score = best.score
        return sheet


__all__ = ["Resolver", "LocationsSource", "SeedSource", "build_source"]
