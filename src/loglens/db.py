"""SQLite persistence layer.

Two concerns live here:
- Job/sheet records for uploaded PDFs (sheet payloads stored as JSON so the
  nested, user-editable structure round-trips without a rigid relational schema).
- The curated reference lists (locations, drivers, trucks, trailers) the
  reconciler matches against, plus the learned raw->canonical alias pairings
  that accumulate from user corrections.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .models import Sheet, load_sheet

# The four curated "source of truth" sets.
REF_KINDS = ("location", "driver", "truck", "trailer")

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
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    data        TEXT NOT NULL,
    UNIQUE (job_id, page_index)
);

CREATE TABLE IF NOT EXISTS ref_values (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    normalized TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (kind, normalized)
);

CREATE TABLE IF NOT EXISTS ref_aliases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    raw            TEXT NOT NULL,
    raw_normalized TEXT NOT NULL,
    ref_value_id   INTEGER NOT NULL REFERENCES ref_values(id) ON DELETE CASCADE,
    UNIQUE (kind, raw_normalized)
);

CREATE INDEX IF NOT EXISTS idx_ref_values_kind ON ref_values(kind, normalized);
CREATE INDEX IF NOT EXISTS idx_ref_aliases_kind ON ref_aliases(kind, raw_normalized);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        # Writes may come from the background worker thread; serialize them.
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """Add columns that older databases may be missing."""

        cols = {
            r["name"] for r in self._conn.execute("PRAGMA table_info(sheets)").fetchall()
        }
        if "status" not in cols:
            self._conn.execute(
                "ALTER TABLE sheets ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
        if "error" not in cols:
            self._conn.execute("ALTER TABLE sheets ADD COLUMN error TEXT")
        # The seed-based locations cache is replaced by the curated ref lists.
        self._conn.execute("DROP TABLE IF EXISTS locations")

    # -- Jobs ------------------------------------------------------------
    def create_job(self, original_name: str, stored_path: Path) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, original_name, stored_path, status, created_at)"
                " VALUES (?, ?, ?, 'pending', ?)",
                (job_id, original_name, str(stored_path), time.time()),
            )
            self._conn.commit()
        return job_id

    def set_job_path(self, job_id: str, stored_path: Path) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET stored_path = ? WHERE id = ?",
                (str(stored_path), job_id),
            )
            self._conn.commit()

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
        with self._lock:
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
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()

    # -- Sheets ----------------------------------------------------------
    def upsert_sheet(
        self, job_id: str, sheet: Sheet, render_path: Path | None
    ) -> str:
        sheet_id = f"{job_id}-{sheet.page_index}"
        with self._lock:
            # Preserve an existing render_path when the caller passes None.
            self._conn.execute(
                "INSERT INTO sheets (id, job_id, page_index, render_path, status, error, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(job_id, page_index) DO UPDATE SET"
                " render_path = COALESCE(excluded.render_path, sheets.render_path),"
                " status = excluded.status, error = excluded.error, data = excluded.data",
                (
                    sheet_id,
                    job_id,
                    sheet.page_index,
                    str(render_path) if render_path else None,
                    sheet.status,
                    sheet.error,
                    sheet.model_dump_json(),
                ),
            )
            self._conn.commit()
        return sheet_id

    def set_sheet_status(
        self, job_id: str, page_index: int, status: str, *, error: str | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sheets SET status = ?, error = ? WHERE job_id = ? AND page_index = ?",
                (status, error, job_id, page_index),
            )
            self._conn.commit()

    def sheet_statuses(self, job_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT page_index, status, error FROM sheets WHERE job_id = ?"
            " ORDER BY page_index",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sheets(self, job_id: str) -> list[tuple[Sheet, str | None]]:
        rows = self._conn.execute(
            "SELECT data, render_path FROM sheets WHERE job_id = ?"
            " ORDER BY page_index",
            (job_id,),
        ).fetchall()
        return [(load_sheet(r["data"]), r["render_path"]) for r in rows]

    def get_sheet(self, job_id: str, page_index: int) -> Sheet | None:
        row = self._conn.execute(
            "SELECT data FROM sheets WHERE job_id = ? AND page_index = ?",
            (job_id, page_index),
        ).fetchone()
        return load_sheet(row["data"]) if row else None

    # -- Reference lists (curated source-of-truth sets) -------------------
    def list_ref_values(self, kind: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, value, normalized FROM ref_values WHERE kind = ?"
            " ORDER BY value COLLATE NOCASE",
            (kind,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_ref_value(self, kind: str, normalized: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, value, normalized FROM ref_values"
            " WHERE kind = ? AND normalized = ?",
            (kind, normalized),
        ).fetchone()
        return dict(row) if row else None

    def add_ref_value(self, kind: str, value: str, normalized: str) -> int:
        """Insert a canonical value; returns its id (existing id if present)."""

        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM ref_values WHERE kind = ? AND normalized = ?",
                (kind, normalized),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = self._conn.execute(
                "INSERT INTO ref_values (kind, value, normalized, created_at)"
                " VALUES (?, ?, ?, ?)",
                (kind, value, normalized, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def delete_ref_value(self, ref_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ref_values WHERE id = ?", (ref_id,))
            self._conn.commit()

    def ref_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT kind, COUNT(*) AS c FROM ref_values GROUP BY kind"
        ).fetchall()
        counts = {kind: 0 for kind in REF_KINDS}
        counts.update({r["kind"]: r["c"] for r in rows})
        return counts

    # -- Learned aliases (raw reading -> canonical value) -----------------
    def list_ref_aliases(self, kind: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT a.id, a.raw, a.raw_normalized, a.ref_value_id, v.value"
            " FROM ref_aliases a JOIN ref_values v ON v.id = a.ref_value_id"
            " WHERE a.kind = ? ORDER BY v.value COLLATE NOCASE, a.raw COLLATE NOCASE",
            (kind,),
        ).fetchall()
        return [dict(r) for r in rows]

    def alias_map(self, kind: str) -> dict[str, dict[str, Any]]:
        """Map of raw_normalized -> {ref_value_id, value} for fast lookup."""

        return {
            r["raw_normalized"]: {"ref_value_id": r["ref_value_id"], "value": r["value"]}
            for r in self.list_ref_aliases(kind)
        }

    def add_ref_alias(
        self, kind: str, raw: str, raw_normalized: str, ref_value_id: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO ref_aliases (kind, raw, raw_normalized, ref_value_id)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(kind, raw_normalized) DO UPDATE SET"
                " raw = excluded.raw, ref_value_id = excluded.ref_value_id",
                (kind, raw, raw_normalized, ref_value_id),
            )
            self._conn.commit()

    def delete_ref_alias(self, alias_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ref_aliases WHERE id = ?", (alias_id,))
            self._conn.commit()


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
