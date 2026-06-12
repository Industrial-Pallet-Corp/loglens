"""Background OCR work queue.

All extraction work - whole uploads and single-page re-extracts - flows
through one global FIFO queue with a single consumer thread, so jobs from any
number of users never run concurrently. Pages *within* the active job fan out
to a bounded thread pool.

Parallelism budget: Anthropic enforces per-model requests/min and input/output
tokens/min per usage tier, and for this workload the output side binds first
(a trip-log page averages ~3.8k input / ~1.9k output tokens). For Sonnet-class
models that means roughly 4 pages/min on Tier 1 (8k OTPM) and ~45 pages/min on
Tier 2 (90k OTPM). With ~30-60s per page call, 25 concurrent pages saturates
Tier 2, so ``extraction.parallel_pages`` is clamped to that ceiling; the
default of 10 leaves headroom for retries and re-asks. Tier 1 accounts should
configure 2-3.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

PARALLEL_CEILING = 25

# Job statuses that mean "do not start (more) work for this job".
_HALTED = ("cancelling", "cancelled")


class JobQueue:
    """FIFO queue of extraction work with a single consumer thread."""

    def __init__(self, app: "FastAPI") -> None:
        self.app = app
        requested = int(app.state.cfg.extraction.parallel_pages)
        self.parallel = max(1, min(requested, PARALLEL_CEILING))
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="loglens-jobqueue"
        )
        self._thread.start()

    def enqueue(self, job_id: str, pages: list[int] | None = None) -> None:
        """Queue a whole job (pages=None) or specific pages (re-extract)."""

        self._q.put((job_id, pages))

    # -- Consumer ----------------------------------------------------------
    def _run(self) -> None:
        while True:
            job_id, pages = self._q.get()
            try:
                self._process(job_id, pages)
            except Exception as exc:  # noqa: BLE001 - never kill the worker
                try:
                    self.app.state.db.set_job_status(
                        job_id, "error", error=f"{type(exc).__name__}: {exc}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._q.task_done()

    def _status(self, job_id: str) -> str | None:
        job = self.app.state.db.get_job(job_id)
        return job["status"] if job else None

    def _process(self, job_id: str, pages: list[int] | None) -> None:
        # Imported lazily: app.py owns the per-page pipeline and imports us.
        from .app import _extract_one, _finalize_job_status

        db = self.app.state.db
        cfg = self.app.state.cfg

        status = self._status(job_id)
        if status is None:
            return  # deleted while waiting in the queue
        if status in _HALTED:
            db.cancel_pending_sheets(job_id)
            db.set_job_status(job_id, "cancelled", error="")
            return

        pdf = cfg.uploads_dir / f"{job_id}.pdf"
        if not pdf.exists():
            db.set_job_status(job_id, "error", error="Original PDF no longer available")
            return

        if pages is None:
            pages = [
                s["page_index"]
                for s in db.sheet_statuses(job_id)
                if s["status"] == "pending"
            ]
        db.set_job_status(job_id, "processing")

        def run_page(page_index: int) -> None:
            # A cancel stops new pages from starting; in-flight API calls
            # are allowed to finish their page (no mid-call abort).
            if self._status(job_id) in (None, *_HALTED):
                return
            _extract_one(self.app, job_id, pdf, page_index)

        with ThreadPoolExecutor(max_workers=self.parallel) as pool:
            list(pool.map(run_page, pages))

        status = self._status(job_id)
        if status is None:
            return  # deleted mid-run
        if status in _HALTED:
            db.cancel_pending_sheets(job_id)
            db.set_job_status(job_id, "cancelled", error="")
        else:
            _finalize_job_status(self.app, job_id)


def recover_interrupted_jobs(app: "FastAPI") -> None:
    """Re-enqueue jobs that a previous server process left unfinished."""

    db = app.state.db
    for job in db.list_jobs():
        if job["status"] == "cancelling":
            db.cancel_pending_sheets(job["id"])
            db.set_job_status(job["id"], "cancelled", error="")
        elif job["status"] in ("queued", "processing"):
            db.reset_processing_sheets(job["id"])
            db.set_job_status(job["id"], "queued")
            app.state.queue.enqueue(job["id"])


__all__ = ["JobQueue", "PARALLEL_CEILING", "recover_interrupted_jobs"]
