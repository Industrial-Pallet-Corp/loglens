"""Locate packaged data files (seed locations, alias map, templates, static)."""

from __future__ import annotations

from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent


def data_dir() -> Path:
    return _PKG_ROOT / "data"


def templates_dir() -> Path:
    return _PKG_ROOT / "templates"


def static_dir() -> Path:
    return _PKG_ROOT / "static"


def default_seed_file() -> Path:
    return data_dir() / "locations.seed.csv"


def default_aliases_file() -> Path:
    return data_dir() / "aliases.yaml"
