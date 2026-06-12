"""FastAPI application factory and HTTP routes."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, ingest
from .config import Config, get_config
from .dates import normalize_sheet_dates
from .db import Database
from .export import sheet_to_csv, sheets_to_csv
from .extraction import build_extractor
from .models import CODE_OPTIONS, MED_CONFIDENCE, FieldValue, Sheet
from .reconcile import KIND_LABELS, REF_KINDS, Reconciler
from .resources import static_dir, templates_dir
from .validate import sheet_warnings
from .worker import JobQueue, recover_interrupted_jobs

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _extract_one(app: FastAPI, job_id: str, pdf_path: Path, page_index: int) -> None:
    """Extract + resolve a single page, persisting status and any error."""

    cfg: Config = app.state.cfg
    db: Database = app.state.db
    extractor = app.state.extractor
    reconciler: Reconciler = app.state.reconciler

    db.set_sheet_status(job_id, page_index, "processing")
    try:
        png = ingest.page_png_bytes(pdf_path, page_index, dpi=cfg.extraction.render_dpi)
        sheet = extractor.extract(png, page_index)
        sheet = normalize_sheet_dates(sheet)
        sheet = reconciler.reconcile_sheet(sheet)
        sheet.page_index = page_index
        sheet.status = "done"
        db.upsert_sheet(job_id, sheet, None)
    except Exception as exc:  # noqa: BLE001 - record per-sheet failure, keep going
        existing = db.get_sheet(job_id, page_index) or Sheet(page_index=page_index)
        existing.status = "error"
        existing.error = f"{type(exc).__name__}: {exc}"
        db.upsert_sheet(job_id, existing, None)


def _finalize_job_status(app: FastAPI, job_id: str) -> None:
    """Set the job to done/error once no sheet is pending/processing."""

    db: Database = app.state.db
    statuses = db.sheet_statuses(job_id)
    if any(s["status"] in ("pending", "processing") for s in statuses):
        return
    errored = sum(1 for s in statuses if s["status"] == "error")
    if errored:
        db.set_job_status(job_id, "error", error=f"{errored} sheet(s) failed")
    else:
        db.set_job_status(job_id, "done", error="")


FINISHED_STATUSES = ("done", "error", "cancelled")
ACTIVE_STATUSES = ("pending", "queued", "processing", "cancelling")


def _overlay_live_status(db: Database, job_id: str, sheets: list[Sheet]) -> None:
    """Apply the live status column onto loaded sheets.

    The stored sheet JSON only changes when extraction finishes, while the
    sheets.status column tracks pending/processing/cancelled transitions live;
    rendering should always reflect the column.
    """

    by_page = {s["page_index"]: s for s in db.sheet_statuses(job_id)}
    for sheet in sheets:
        live = by_page.get(sheet.page_index)
        if live:
            sheet.status = live["status"]
            sheet.error = live["error"] or sheet.error


def _apply_field(fv: FieldValue, raw: str | None, confirm: bool = False) -> None:
    """Write an edited value onto a FieldValue, pinning user edits as confident.

    With ``confirm`` (explicit saves, where the client only posts deliberately
    corrected/confirmed fields), an unchanged value is still pinned as
    user-confirmed ("confirm by touch"); its ref_id is kept since the value
    itself did not change.
    """

    new = (raw or "").strip() or None
    if new != fv.value:
        fv.value = new
        fv.source = "user"
        fv.confidence = 100.0 if new else None
        fv.ref_id = None
    elif confirm and new is not None:
        fv.source = "user"
        fv.confidence = 100.0


def _ocr_reading(fv: FieldValue) -> str | None:
    """The original OCR reading behind a field, for alias learning."""

    if fv.raw:
        return fv.raw
    return fv.value if fv.source in ("ocr", "resolver") else None


def _apply_form(sheet: Sheet, form: dict[str, str]) -> Sheet:
    """Apply edited form values back onto a sheet (human edits => source=user).

    Only keys present in the form are applied, so a partial post never wipes
    fields it didn't include. An ``_explicit`` marker (set by the inline-save
    JS, which posts only corrected/confirmed fields) means every submitted
    field is a deliberate user action, so unchanged values are pinned as
    user-confirmed too. Without it (no-JS full-form post), only changed
    values are pinned.
    """

    confirm = form.get("_explicit") == "1"

    def apply(fv: FieldValue, key: str) -> None:
        if key in form:
            _apply_field(fv, form[key], confirm)

    apply(sheet.driver, "driver")
    apply(sheet.date, "date")
    apply(sheet.truck_no, "truck_no")
    apply(sheet.beg_odometer, "beg_odometer")
    apply(sheet.end_odometer, "end_odometer")
    apply(sheet.total_miles, "total_miles")

    for i, row in enumerate(sheet.rows):
        p = f"row-{i}-"
        # place_raw is no longer edited in the UI (the scan preview serves that
        # purpose), but it is kept in the model for re-resolve and CSV export.
        apply(row.start_miles, p + "start_miles")
        apply(row.end_miles, p + "end_miles")
        apply(row.trailer_no, p + "trailer_no")
        apply(row.bol_ticket, p + "bol_ticket")
        apply(row.code, p + "code")
        apply(row.place, p + "place")
    return sheet


def _learnable(fv: FieldValue) -> bool:
    """Whether a field's value is trustworthy enough to enter the known lists.

    Only user-corrected/confirmed values or high-confidence readings qualify;
    mid/low confidence values are never learned automatically.
    """

    if not fv.value:
        return False
    if fv.source == "user":
        return True
    return fv.confidence is not None and fv.confidence >= MED_CONFIDENCE


def _learn_from_sheet(reconciler: Reconciler, sheet: Sheet) -> None:
    """Grow the reference lists from a saved (human-confirmed) sheet.

    Learnable values (see ``_learnable``) in the four curated kinds are ensured
    to exist in their list, and the original OCR reading is recorded as a
    learned alias whenever it differs from the saved canonical value.
    """

    pairs: list[tuple[str, FieldValue, str | None]] = [
        ("driver", sheet.driver, _ocr_reading(sheet.driver)),
        ("truck", sheet.truck_no, _ocr_reading(sheet.truck_no)),
    ]
    for row in sheet.rows:
        pairs.append(("location", row.place, row.place_raw.value or _ocr_reading(row.place)))
        pairs.append(("trailer", row.trailer_no, _ocr_reading(row.trailer_no)))

    for kind, fv, raw in pairs:
        if not _learnable(fv):
            continue
        ref_id = reconciler.learn(kind, fv.value, raw)
        if ref_id is not None:
            fv.ref_id = str(ref_id)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    cfg.ensure_dirs()

    app = FastAPI(title="LogLens", version=__version__)
    app.state.cfg = cfg
    app.state.db = Database(cfg.db_path)
    app.state.extractor = build_extractor(cfg.extraction)
    app.state.reconciler = Reconciler(app.state.db, cfg.resolver)
    app.state.queue = JobQueue(app)
    recover_interrupted_jobs(app)

    templates = Jinja2Templates(directory=str(templates_dir()))
    app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")

    @app.middleware("http")
    async def static_no_cache(request: Request, call_next):
        # Without this, browsers heuristically cache /static assets and can
        # keep serving stale JS/CSS after an upgrade. "no-cache" forces a
        # revalidation (cheap 304) on every load.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def db() -> Database:
        return app.state.db

    # -- Routes ----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "jobs": db().list_jobs(),
                "version": __version__,
                "provider": cfg.extraction.provider,
                "ref_counts": db().ref_counts(),
            },
        )

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        filename = file.filename or "upload.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Please upload a PDF file.")

        job_id = db().create_job(filename, Path("pending"))
        stored = cfg.uploads_dir / f"{job_id}.pdf"
        size = 0
        with stored.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    stored.unlink(missing_ok=True)
                    db().delete_job(job_id)
                    raise HTTPException(413, "PDF exceeds the 50 MB upload limit.")
                out.write(chunk)
        db().set_job_path(job_id, stored)

        # Render pages now (fast, local) so scans appear immediately; the slow
        # OCR happens in a background worker per page.
        try:
            renders = ingest.render_pdf_to_pngs(
                stored, cfg.renders_dir / job_id, dpi=cfg.extraction.render_dpi
            )
        except Exception as exc:  # noqa: BLE001 - bad/corrupt PDF
            db().set_job_status(job_id, "error", error=f"Could not read PDF: {exc}")
            return RedirectResponse(f"/jobs/{job_id}", status_code=303)

        n = len(renders)
        if n == 0:
            db().set_job_status(job_id, "error", error="PDF has no pages.")
            return RedirectResponse(f"/jobs/{job_id}", status_code=303)

        db().set_job_status(job_id, "queued", page_count=n)
        for i in range(n):
            db().upsert_sheet(job_id, Sheet(page_index=i, status="pending"), renders[i])

        app.state.queue.enqueue(job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}/status")
    def job_status(job_id: str):
        job = db().get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        statuses = db().sheet_statuses(job_id)
        done = sum(1 for s in statuses if s["status"] == "done")
        errored = sum(1 for s in statuses if s["status"] == "error")
        return JSONResponse(
            {
                "job_status": job["status"],
                "page_count": job["page_count"],
                "done": done,
                "errored": errored,
                "total": len(statuses),
                "finished": job["status"] in FINISHED_STATUSES,
                "sheets": statuses,
            }
        )

    @app.get("/status/active")
    def active_status():
        """Live progress for all unfinished jobs (jobs-list polling)."""

        return JSONResponse(
            {
                "jobs": [
                    {
                        "id": j["id"],
                        "status": j["status"],
                        "done": j["done_pages"],
                        "total": j["page_count"],
                    }
                    for j in db().list_jobs()
                    if j["status"] in ACTIVE_STATUSES
                ]
            }
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):
        job = db().get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        sheets = [s for (s, _) in db().get_sheets(job_id)]
        _overlay_live_status(db(), job_id, sheets)
        reconciler: Reconciler = app.state.reconciler
        ref_lists = {kind: reconciler.values(kind) for kind in REF_KINDS}
        input_tokens = sum(s.input_tokens or 0 for s in sheets)
        output_tokens = sum(s.output_tokens or 0 for s in sheets)
        warnings = {s.page_index: sheet_warnings(s) for s in sheets}
        return templates.TemplateResponse(
            request,
            "job.html",
            {
                "job": job,
                "sheets": sheets,
                "ref_lists": ref_lists,
                "version": __version__,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "finished": job["status"] in FINISHED_STATUSES,
                "warnings": warnings,
                "code_options": CODE_OPTIONS,
            },
        )

    @app.get("/jobs/{job_id}/sheets/{page}/html", response_class=HTMLResponse)
    def sheet_fragment(request: Request, job_id: str, page: int):
        """A single sheet card, for in-place swaps by the job-page poller."""

        job = db().get_job(job_id)
        sheet = db().get_sheet(job_id, page)
        if not job or not sheet:
            raise HTTPException(404, "Sheet not found")
        _overlay_live_status(db(), job_id, [sheet])
        return templates.TemplateResponse(
            request,
            "_sheet.html",
            {
                "job": job,
                "sheet": sheet,
                "warnings": {sheet.page_index: sheet_warnings(sheet)},
            },
        )

    @app.post("/jobs/{job_id}/sheets/{page}/save")
    async def save_sheet(request: Request, job_id: str, page: int):
        sheet = db().get_sheet(job_id, page)
        if not sheet:
            raise HTTPException(404, "Sheet not found")
        form = dict(await request.form())
        sheet = _apply_form(sheet, form)
        # Saving locks values in: grow the curated lists + learned aliases.
        _learn_from_sheet(app.state.reconciler, sheet)
        db().upsert_sheet(job_id, sheet, None)
        # Inline (fetch) save: return a tiny confirmation instead of reloading.
        if request.headers.get("X-Inline") == "1":
            return HTMLResponse('<span class="saved">Saved \u2713</span>')
        return RedirectResponse(f"/jobs/{job_id}#sheet-{page}", status_code=303)

    @app.post("/jobs/{job_id}/sheets/{page}/reextract")
    def reextract_sheet(job_id: str, page: int):
        if not db().get_job(job_id):
            raise HTTPException(404, "Job not found")
        stored = cfg.uploads_dir / f"{job_id}.pdf"
        if not stored.exists():
            raise HTTPException(404, "Original PDF no longer available")
        db().set_sheet_status(job_id, page, "pending")
        db().set_job_status(job_id, "queued")
        app.state.queue.enqueue(job_id, [page])
        return RedirectResponse(f"/jobs/{job_id}#sheet-{page}", status_code=303)

    @app.post("/jobs/{job_id}/sheets/{page}/reresolve")
    def reresolve_sheet(job_id: str, page: int):
        sheet = db().get_sheet(job_id, page)
        if not sheet:
            raise HTTPException(404, "Sheet not found")
        sheet = app.state.reconciler.reconcile_sheet(sheet)
        db().upsert_sheet(job_id, sheet, None)
        return RedirectResponse(f"/jobs/{job_id}#sheet-{page}", status_code=303)

    @app.get("/jobs/{job_id}/render/{page}.png")
    def render_image(job_id: str, page: int):
        path = cfg.renders_dir / job_id / f"page-{page}.png"
        if not path.exists():
            raise HTTPException(404, "Render not found")
        return Response(path.read_bytes(), media_type="image/png")

    @app.get("/jobs/{job_id}/sheets/{page}/export.csv")
    def export_sheet(job_id: str, page: int):
        sheet = db().get_sheet(job_id, page)
        if not sheet:
            raise HTTPException(404, "Sheet not found")
        return PlainTextResponse(
            sheet_to_csv(sheet),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{job_id}-sheet-{page + 1}.csv"'
            },
        )

    @app.get("/jobs/{job_id}/export.csv")
    def export_job(job_id: str):
        sheets = [s for (s, _) in db().get_sheets(job_id)]
        if not sheets:
            raise HTTPException(404, "No sheets to export")
        return PlainTextResponse(
            sheets_to_csv(sheets),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{job_id}-all.csv"'
            },
        )

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str):
        job = db().get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job["status"] in ("pending", "queued"):
            # Not started: cancel outright; the worker skips it on dequeue.
            db().cancel_pending_sheets(job_id)
            db().set_job_status(job_id, "cancelled", error="")
        elif job["status"] == "processing":
            # The worker stops feeding pages and finalizes the cancel; the
            # in-flight page calls are allowed to finish.
            db().set_job_status(job_id, "cancelling")
        referer = request.headers.get("referer")
        return RedirectResponse(referer or f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/delete")
    def delete_job(job_id: str):
        db().delete_job(job_id)
        shutil.rmtree(cfg.renders_dir / job_id, ignore_errors=True)
        (cfg.uploads_dir / f"{job_id}.pdf").unlink(missing_ok=True)
        return RedirectResponse("/", status_code=303)

    # -- Reference list management ----------------------------------------
    def _check_kind(kind: str) -> None:
        if kind not in REF_KINDS:
            raise HTTPException(404, f"Unknown list kind: {kind}")

    @app.post("/admin/lists/{kind}")
    async def add_list_entry(request: Request, kind: str):
        _check_kind(kind)
        form = dict(await request.form())
        value = (form.get("value") or "").strip()
        if value:
            app.state.reconciler.learn(kind, value)
        return RedirectResponse(f"/settings#list-{kind}", status_code=303)

    @app.post("/admin/lists/{kind}/{ref_id}/delete")
    def delete_list_entry(kind: str, ref_id: int):
        _check_kind(kind)
        db().delete_ref_value(ref_id)
        app.state.reconciler.invalidate()
        return RedirectResponse(f"/settings#list-{kind}", status_code=303)

    @app.post("/admin/lists/{kind}/clear")
    def clear_list(kind: str):
        _check_kind(kind)
        db().clear_ref_kind(kind)
        app.state.reconciler.invalidate()
        return RedirectResponse(f"/settings#list-{kind}", status_code=303)

    @app.post("/admin/aliases/{alias_id}/delete")
    def delete_alias(alias_id: int, kind: str = "location"):
        db().delete_ref_alias(alias_id)
        app.state.reconciler.invalidate()
        return RedirectResponse("/settings", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        ref_lists = {}
        for kind in REF_KINDS:
            aliases_by_ref: dict[int, list[dict]] = {}
            for a in db().list_ref_aliases(kind):
                aliases_by_ref.setdefault(a["ref_value_id"], []).append(a)
            ref_lists[kind] = [
                {**entry, "aliases": aliases_by_ref.get(entry["id"], [])}
                for entry in db().list_ref_values(kind)
            ]
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "version": __version__,
                "config_dir": str(cfg.config_dir),
                "config_file": str(cfg.config_file),
                "config_exists": cfg.config_file.exists(),
                "credentials_file": str(cfg.credentials_file),
                "credentials_exists": cfg.credentials_file.exists(),
                "state_dir": str(cfg.state_dir),
                "provider": cfg.extraction.provider,
                "model": cfg.extraction.model,
                "has_api_key": bool(cfg.extraction.resolve_api_key()),
                "ref_lists": ref_lists,
                "kind_labels": KIND_LABELS,
            },
        )

    @app.post("/admin/check")
    def admin_check():
        try:
            extractor = build_extractor(cfg.extraction)
            ok, detail = extractor.verify()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        return JSONResponse({"ok": ok, "detail": detail})

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"

    return app
