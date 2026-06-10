"""Configuration and filesystem layout for LogLens.

Settings are read from a TOML file (default ``~/.config/loglens/config.toml``)
and may be overridden by environment variables. Nothing here requires the file
to exist; sane defaults let the app boot with zero configuration.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def default_config_path() -> Path:
    return _env_path(
        "LOGLENS_CONFIG",
        Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
        / "loglens"
        / "config.toml",
    )


def default_state_dir() -> Path:
    return _env_path(
        "LOGLENS_STATE_DIR",
        Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
        / "loglens",
    )


@dataclass
class ExtractionConfig:
    """Vision-LLM extractor settings."""

    provider: str = "anthropic"  # anthropic | stub
    model: str = "claude-3-5-sonnet-latest"
    api_key: str | None = None
    max_tokens: int = 4096
    render_dpi: int = 200

    def resolve_api_key(self) -> str | None:
        return self.api_key or os.environ.get("ANTHROPIC_API_KEY")


@dataclass
class ResolverConfig:
    """Location-resolution settings (Phase 1 uses a seed file)."""

    source: str = "seed"  # seed | redshift (Phase 2)
    seed_file: str | None = None
    aliases_file: str | None = None
    match_threshold: int = 70  # 0-100; below this we flag as unresolved
    max_alternates: int = 5


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class Config:
    state_dir: Path = field(default_factory=default_state_dir)
    server: ServerConfig = field(default_factory=ServerConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    resolver: ResolverConfig = field(default_factory=ResolverConfig)

    # Derived paths -------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.state_dir / "loglens.db"

    @property
    def uploads_dir(self) -> Path:
        return self.state_dir / "uploads"

    @property
    def renders_dir(self) -> Path:
        return self.state_dir / "renders"

    def ensure_dirs(self) -> None:
        for p in (self.state_dir, self.uploads_dir, self.renders_dir):
            p.mkdir(parents=True, exist_ok=True)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(path: Path | None = None) -> Config:
    """Build a :class:`Config` from a TOML file plus environment overrides."""

    path = path or default_config_path()
    raw = _load_toml(path)

    cfg = Config()

    if state := raw.get("state_dir"):
        cfg.state_dir = Path(state).expanduser()

    server = raw.get("server", {})
    cfg.server = ServerConfig(
        host=server.get("host", cfg.server.host),
        port=int(server.get("port", cfg.server.port)),
    )

    ex = raw.get("extraction", {})
    cfg.extraction = ExtractionConfig(
        provider=ex.get("provider", cfg.extraction.provider),
        model=ex.get("model", cfg.extraction.model),
        api_key=ex.get("api_key"),
        max_tokens=int(ex.get("max_tokens", cfg.extraction.max_tokens)),
        render_dpi=int(ex.get("render_dpi", cfg.extraction.render_dpi)),
    )

    rs = raw.get("resolver", {})
    cfg.resolver = ResolverConfig(
        source=rs.get("source", cfg.resolver.source),
        seed_file=rs.get("seed_file"),
        aliases_file=rs.get("aliases_file"),
        match_threshold=int(rs.get("match_threshold", cfg.resolver.match_threshold)),
        max_alternates=int(rs.get("max_alternates", cfg.resolver.max_alternates)),
    )

    # Environment overrides for the most operationally relevant settings.
    if host := os.environ.get("LOGLENS_HOST"):
        cfg.server.host = host
    if port := os.environ.get("LOGLENS_PORT"):
        cfg.server.port = int(port)
    if provider := os.environ.get("LOGLENS_EXTRACTION_PROVIDER"):
        cfg.extraction.provider = provider

    return cfg


@lru_cache(maxsize=1)
def get_config() -> Config:
    cfg = load_config()
    cfg.ensure_dirs()
    return cfg
