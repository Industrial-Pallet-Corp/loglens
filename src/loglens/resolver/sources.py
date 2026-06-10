"""Locations sources.

A source yields ``(location_id, name)`` pairs. The seed source reads a bundled
or user-supplied file (CSV or YAML). Phase 2 will add a RedshiftSource here.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

import yaml

from ..resources import default_seed_file


class LocationsSource(Protocol):
    def load(self) -> list[tuple[str | None, str]]: ...


class SeedSource:
    """Reads locations from a CSV (``location_id,name`` or ``name``) or YAML file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else default_seed_file()

    def load(self) -> list[tuple[str | None, str]]:
        if not self.path.exists():
            raise FileNotFoundError(f"Seed locations file not found: {self.path}")
        if self.path.suffix.lower() in {".yaml", ".yml"}:
            return self._load_yaml()
        return self._load_csv()

    def _load_csv(self) -> list[tuple[str | None, str]]:
        out: list[tuple[str | None, str]] = []
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = {f.lower(): f for f in (reader.fieldnames or [])}
            name_key = fields.get("name")
            id_key = fields.get("location_id") or fields.get("id")
            if name_key is None:
                # Headerless single-column file: re-read as plain rows.
                fh.seek(0)
                for line in csv.reader(fh):
                    if line and line[0].strip():
                        out.append((None, line[0].strip()))
                return out
            for record in reader:
                name = (record.get(name_key) or "").strip()
                if not name:
                    continue
                loc_id = (record.get(id_key) or "").strip() if id_key else None
                out.append((loc_id or None, name))
        return out

    def _load_yaml(self) -> list[tuple[str | None, str]]:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
        out: list[tuple[str | None, str]] = []
        for item in data:
            if isinstance(item, str):
                out.append((None, item.strip()))
            elif isinstance(item, dict) and item.get("name"):
                out.append(
                    (
                        str(item["location_id"]) if item.get("location_id") else None,
                        str(item["name"]).strip(),
                    )
                )
        return out
