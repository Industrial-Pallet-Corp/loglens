"""Locate packaged data files (templates, static assets)."""

from __future__ import annotations

from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent


def templates_dir() -> Path:
    return _PKG_ROOT / "templates"


def static_dir() -> Path:
    return _PKG_ROOT / "static"
