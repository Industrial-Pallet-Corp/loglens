"""SQLite persistence layer.

Two concerns live here:
- Job/sheet records for uploaded PDFs (sheet payloads stored as JSON so the
  nested, user-editable structure round-trips without a rigid relational schema).
- The locations cache the resolver matches against. In Phase 1 it is filled
  from a seed file; in Phase 2 the same table is filled from Redshift.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .models import Sheet

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    error        TEXT,
    page_count   INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sheets (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page_index  INTEGER NOT NULL,
    render_path TEXT,
    data        TEXT NOT NULL,
    UNIQUE (job_id, page_index)
);

CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id TEXT,
    name        TEXT NOT NULL,
    normalized  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'seed'
);

CREATE INDEX IF NOT EXISTS idx_locations_normalized ON locations(normalized);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- Jobs ------------------------------------------------------------
    def create_job(self, original_name: str, stored_path: Path) -> str:
        job_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO jobs (id, original_name, stored_path, status, created_at)"
            " VALUES (?, ?, ?, 'pending', ?)",
            (job_id, original_name, str(stored_path), time.time()),
        )
        self._conn.commit()
        return job_id

    def set_job_status(
        self,
        job_id: str,
        status: str,
        *,
        error: str | None = None,
        page_count: int | None = None,
    ) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if page_count is not None:
            sets.append("page_count = ?")
            params.append(page_count)
        params.append(job_id)
        self._conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params
        )
        self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    # -- Sheets ----------------------------------------------------------
    def upsert_sheet(
        self, job_id: str, sheet: Sheet, render_path: Path | None
    ) -> str:
        sheet_id = f"{job_id}-{sheet.page_index}"
        self._conn.execute(
            "INSERT INTO sheets (id, job_id, page_index, render_path, data)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(job_id, page_index) DO UPDATE SET"
            " render_path = excluded.render_path, data = excluded.data",
            (
                sheet_id,
                job_id,
                sheet.page_index,
                str(render_path) if render_path else None,
                sheet.model_dump_json(),
            ),
        )
        self._conn.commit()
        return sheet_id

    def get_sheets(self, job_id: str) -> list[tuple[Sheet, str | None]]:
        rows = self._conn.execute(
            "SELECT data, render_path FROM sheets WHERE job_id = ?"
            " ORDER BY page_index",
            (job_id,),
        ).fetchall()
        return [
            (Sheet.model_validate_json(r["data"]), r["render_path"]) for r in rows
        ]

    def get_sheet(self, job_id: str, page_index: int) -> Sheet | None:
        row = self._conn.execute(
            "SELECT data FROM sheets WHERE job_id = ? AND page_index = ?",
            (job_id, page_index),
        ).fetchone()
        return Sheet.model_validate_json(row["data"]) if row else None

    # -- Locations cache -------------------------------------------------
    def replace_locations(
        self, rows: Iterable[tuple[str | None, str, str]], source: str
    ) -> int:
        """Replace all locations for ``source`` with ``(location_id, name, normalized)``."""

        cur = self._conn.cursor()
        cur.execute("DELETE FROM locations WHERE source = ?", (source,))
        cur.executemany(
            "INSERT INTO locations (location_id, name, normalized, source)"
            " VALUES (?, ?, ?, ?)",
            [(lid, name, norm, source) for (lid, name, norm) in rows],
        )
        self._conn.commit()
        return cur.rowcount

    def all_locations(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT location_id, name, normalized FROM locations"
        ).fetchall()
        return [dict(r) for r in rows]

    def location_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS c FROM locations").fetchone()[
            "c"
        ]


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
