"""FastAPI application factory and HTTP routes."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, ingest
from .config import Config, get_config
from .db import Database
from .export import sheet_to_csv, sheets_to_csv
from .extraction import build_extractor
from .models import Sheet
from .resolver import Resolver
from .resources import static_dir, templates_dir


def _process_job(app: FastAPI, job_id: str, pdf_path: Path) -> None:
    """Render, extract, resolve, and persist every page of a PDF."""

    cfg: Config = app.state.cfg
    db: Database = app.state.db
    extractor = app.state.extractor
    resolver: Resolver = app.state.resolver

    try:
        n = ingest.page_count(pdf_path)
        db.set_job_status(job_id, "processing", page_count=n)
        renders = ingest.render_pdf_to_pngs(
            pdf_path, cfg.renders_dir / job_id, dpi=cfg.extraction.render_dpi
        )
        for i in range(n):
            png = ingest.page_png_bytes(pdf_path, i, dpi=cfg.extraction.render_dpi)
            sheet = extractor.extract(png, i)
            sheet = resolver.resolve_sheet(sheet)
            render_path = renders[i] if i < len(renders) else None
            db.upsert_sheet(job_id, sheet, render_path)
        db.set_job_status(job_id, "done")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        db.set_job_status(job_id, "error", error=f"{type(exc).__name__}: {exc}")
        raise


def _apply_form(sheet: Sheet, form: dict[str, str], locations_by_name: dict[str, str]) -> Sheet:
    """Apply edited form values back onto a sheet."""

    sheet.driver = form.get("driver") or None
    sheet.truck_no = form.get("truck_no") or None
    sheet.beg_odometer = form.get("beg_odometer") or None
    sheet.end_odometer = form.get("end_odometer") or None
    sheet.total_miles = form.get("total_miles") or None

    for i, row in enumerate(sheet.rows):
        p = f"row-{i}-"
        row.date = form.get(p + "date") or None
        row.place_raw = form.get(p + "place_raw") or None
        row.start_miles = form.get(p + "start_miles") or None
        row.end_miles = form.get(p + "end_miles") or None
        row.trailer_no = form.get(p + "trailer_no") or None
        row.bol_ticket = form.get(p + "bol_ticket") or None
        row.code = form.get(p + "code") or None

        resolved = (form.get(p + "place_resolved") or "").strip()
        row.place_resolved = resolved or None
        row.place_location_id = locations_by_name.get(resolved) if resolved else None
        # Manual confirmation marks the place as fully confident.
        row.place_score = 100.0 if resolved else None
        if "place_raw" in row.uncertain_fields and resolved:
            row.uncertain_fields = [f for f in row.uncertain_fields if f != "place_raw"]
    return sheet


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    cfg.ensure_dirs()

    app = FastAPI(title="LogLens", version=__version__)
    app.state.cfg = cfg
    app.state.db = Database(cfg.db_path)
    app.state.extractor = build_extractor(cfg.extraction)
    app.state.resolver = Resolver(app.state.db, cfg.resolver)
    app.state.resolver.ensure_cache()

    templates = Jinja2Templates(directory=str(templates_dir()))
    app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")

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
                "location_count": db().location_count(),
            },
        )

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Please upload a PDF file.")
        job_id = db().create_job(file.filename or "upload.pdf", Path("pending"))
        stored = cfg.uploads_dir / f"{job_id}.pdf"
        with stored.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        db().set_job_status(job_id, "pending")
        db()._conn.execute(
            "UPDATE jobs SET stored_path = ? WHERE id = ?", (str(stored), job_id)
        )
        db()._conn.commit()
        try:
            _process_job(app, job_id, stored)
        except Exception:
            pass  # status already recorded; the job page shows the error
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):
        job = db().get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        sheets = db().get_sheets(job_id)
        locations = [loc["name"] for loc in db().all_locations()]
        return templates.TemplateResponse(
            request,
            "job.html",
            {
                "job": job,
                "sheets": [s for (s, _) in sheets],
                "locations": locations,
                "version": __version__,
            },
        )

    @app.post("/jobs/{job_id}/sheets/{page}/save")
    async def save_sheet(request: Request, job_id: str, page: int):
        sheet = db().get_sheet(job_id, page)
        if not sheet:
            raise HTTPException(404, "Sheet not found")
        form = dict(await request.form())
        locations_by_name = {
            loc["name"]: loc["location_id"] for loc in db().all_locations()
        }
        sheet = _apply_form(sheet, form, locations_by_name)
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

    @app.post("/jobs/{job_id}/delete")
    def delete_job(job_id: str):
        db().delete_job(job_id)
        shutil.rmtree(cfg.renders_dir / job_id, ignore_errors=True)
        (cfg.uploads_dir / f"{job_id}.pdf").unlink(missing_ok=True)
        return RedirectResponse("/", status_code=303)

    @app.post("/admin/refresh-locations")
    def refresh_locations():
        count = app.state.resolver.refresh_cache()
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"

    return app
