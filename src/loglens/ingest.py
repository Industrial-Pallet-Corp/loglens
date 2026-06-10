"""PDF ingest: render each page to a PNG with PyMuPDF.

We render whole pages (one page == one trip-log sheet) at a configurable DPI.
A page is also exposed to the extractor as raw PNG bytes. A generic crop helper
is provided so the review UI / extractor can zoom into a region later.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def _zoom_matrix(dpi: int) -> "fitz.Matrix":
    scale = dpi / 72.0
    return fitz.Matrix(scale, scale)


def render_pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int = 200) -> list[Path]:
    """Render every page of ``pdf_path`` to ``out_dir`` as ``page-N.png``."""

    out_dir.mkdir(parents=True, exist_ok=True)
    matrix = _zoom_matrix(dpi)
    paths: list[Path] = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out_path = out_dir / f"page-{i}.png"
            pix.save(out_path)
            paths.append(out_path)
    return paths


def page_png_bytes(pdf_path: Path, page_index: int, dpi: int = 200) -> bytes:
    """Return PNG bytes for a single page (used to feed the vision model)."""

    matrix = _zoom_matrix(dpi)
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        return page.get_pixmap(matrix=matrix, alpha=False).tobytes("png")


def crop_region_png(
    pdf_path: Path,
    page_index: int,
    bbox: tuple[float, float, float, float],
    dpi: int = 200,
) -> bytes:
    """Crop a normalized (0-1) bbox of a page and return PNG bytes."""

    matrix = _zoom_matrix(dpi)
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        rect = page.rect
        clip = fitz.Rect(
            rect.x0 + bbox[0] * rect.width,
            rect.y0 + bbox[1] * rect.height,
            rect.x0 + bbox[2] * rect.width,
            rect.y0 + bbox[3] * rect.height,
        )
        return page.get_pixmap(matrix=matrix, clip=clip, alpha=False).tobytes("png")


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count
