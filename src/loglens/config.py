"""Configuration and filesystem layout for LogLens.

Settings come from two TOML files in the config directory (see ``paths.py``):

- ``config.toml`` - server, extraction, and resolver settings.
- ``credentials.toml`` - secrets (the Anthropic API key), kept ``0600`` and
  out of version control.

Environment variables override the most operationally relevant values. Nothing
here requires either file to exist; sane defaults let the app boot with zero
configuration (using the offline ``stub`` provider).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import paths

# Current best general-purpose vision model for this workload. Opus 4.8
# (``claude-opus-4-8``) is the high-accuracy option for very messy handwriting.
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class ExtractionConfig:
    """Vision-LLM extractor settings."""

    provider: str = "anthropic"  # anthropic | stub
    model: str = DEFAULT_MODEL
    api_key: str | None = None  # from config.toml (discouraged; prefer credentials)
    credentials_api_key: str | None = None  # from credentials.toml
    max_tokens: int = 4096
    render_dpi: int = 200
    max_retries: int = 3  # transient API failures

    def resolve_api_key(self) -> str | None:
        return (
            self.api_key
            or self.credentials_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
        )


@dataclass
class ResolverConfig:
    """Reconciliation settings for the curated reference lists."""

    match_threshold: int = 70  # 0-100; below this a raw reading passes through
    max_alternates: int = 5


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class Config:
    config_dir: Path = field(default_factory=paths.config_dir)
    state_dir: Path = field(default_factory=paths.state_dir)
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

    @property
    def config_file(self) -> Path:
        return self.config_dir / paths.CONFIG_FILENAME

    @property
    def credentials_file(self) -> Path:
        return self.config_dir / paths.CREDENTIALS_FILENAME

    def ensure_dirs(self) -> None:
        for p in (self.state_dir, self.uploads_dir, self.renders_dir):
            p.mkdir(parents=True, exist_ok=True)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(config_dir: str | os.PathLike[str] | None = None) -> Config:
    """Build a :class:`Config` from the config + credentials TOML files."""

    cdir = paths.config_dir(config_dir)
    raw = _load_toml(cdir / paths.CONFIG_FILENAME)
    creds = _load_toml(cdir / paths.CREDENTIALS_FILENAME)

    cfg = Config(config_dir=cdir)

    if state := raw.get("state_dir"):
        cfg.state_dir = Path(state).expanduser()

    server = raw.get("server", {})
    cfg.server = ServerConfig(
        host=server.get("host", cfg.server.host),
        port=int(server.get("port", cfg.server.port)),
    )

    ex = raw.get("extraction", {})
    cfg.extraction = ExtractionConfig(
        provider=ex.get("provider", "anthropic"),
        model=ex.get("model", DEFAULT_MODEL),
        api_key=ex.get("api_key"),
        credentials_api_key=(creds.get("anthropic", {}) or {}).get("api_key"),
        max_tokens=int(ex.get("max_tokens", 4096)),
        render_dpi=int(ex.get("render_dpi", 200)),
        max_retries=int(ex.get("max_retries", 3)),
    )

    rs = raw.get("resolver", {})
    cfg.resolver = ResolverConfig(
        match_threshold=int(rs.get("match_threshold", 70)),
        max_alternates=int(rs.get("max_alternates", 5)),
    )

    # Environment overrides for the most operationally relevant settings.
    if host := os.environ.get("LOGLENS_HOST"):
        cfg.server.host = host
    if port := os.environ.get("LOGLENS_PORT"):
        cfg.server.port = int(port)
    if provider := os.environ.get("LOGLENS_EXTRACTION_PROVIDER"):
        cfg.extraction.provider = provider
    if model := os.environ.get("LOGLENS_MODEL"):
        cfg.extraction.model = model

    return cfg


@lru_cache(maxsize=1)
def get_config() -> Config:
    cfg = load_config()
    cfg.ensure_dirs()
    return cfg
