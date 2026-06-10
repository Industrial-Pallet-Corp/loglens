"""Image preparation for the vision model.

Rendered pages can be large; we downscale to the model's maximum supported long
edge and re-encode as JPEG to keep the base64 payload small (and under the API's
per-image size limit) without hurting legibility of handwriting.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

# Anthropic vision long-edge limits: 2576px on Opus 4.7+ (and Fable/Mythos),
# 1568px on earlier models. We pick conservatively from the model id.
_HIGH_RES_MARKERS = ("opus-4-8", "opus-4-7", "fable", "mythos")
HIGH_RES_MAX_EDGE = 2576
STANDARD_MAX_EDGE = 1568

# Keep individual images well under the 5MB API limit.
MAX_BYTES = 4_500_000


def max_edge_for_model(model: str) -> int:
    m = (model or "").lower()
    return HIGH_RES_MAX_EDGE if any(k in m for k in _HIGH_RES_MARKERS) else STANDARD_MAX_EDGE


def prepare_image(
    png_bytes: bytes, model: str, *, quality: int = 90
) -> tuple[bytes, str]:
    """Return (jpeg_bytes, media_type) downscaled for the given model."""

    max_edge = max_edge_for_model(model)
    img = Image.open(BytesIO(png_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    long_edge = max(img.size)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    # Re-encode, stepping quality down if we exceed the size budget.
    for q in (quality, 80, 70, 60):
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_BYTES:
            return data, "image/jpeg"
    return data, "image/jpeg"
