"""Runtime filesystem locations (config, credentials, state).

Resolution is cwd-independent so LogLens behaves the same from any directory,
and works whether installed via Homebrew or from source. Precedence for the
config directory (first match wins):

1. An explicit directory passed in (e.g. a ``--config-dir`` CLI flag).
2. ``LOGLENS_CONFIG_DIR`` environment variable.
3. Homebrew ``etc/loglens`` when running from inside a brew prefix (survives upgrades).
4. XDG ``~/.config/loglens`` (the from-source / default location).

State (the SQLite DB, uploads, page renders) follows a parallel chain via
``LOGLENS_STATE_DIR`` then the Homebrew ``var`` dir then XDG state.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_FILENAME = "config.toml"
CREDENTIALS_FILENAME = "credentials.toml"


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def _brew_prefix() -> Path | None:
    """Detect a Homebrew prefix only when *this package* is installed under it.

    We deliberately do not trust a bare ``HOMEBREW_PREFIX`` env var: it is set
    on any machine with Homebrew, including when LogLens is run from a source
    checkout or a plain venv. Brew locations are used only when the package
    actually lives inside the prefix (a real ``brew install``).
    """

    here = Path(__file__).resolve()

    # Formula installs live under ``<prefix>/Cellar/loglens/<ver>/...``.
    for parent in here.parents:
        if parent.name == "Cellar":
            return parent.parent

    # Otherwise honor HOMEBREW_PREFIX only if the package is inside it
    # (covers ``<prefix>/opt/loglens/libexec/...`` layouts).
    if env := os.environ.get("HOMEBREW_PREFIX"):
        prefix = _expand(env)
        try:
            here.relative_to(prefix)
        except ValueError:
            return None
        return prefix

    return None


def config_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return _expand(str(explicit))
    if env := os.environ.get("LOGLENS_CONFIG_DIR"):
        return _expand(env)
    if (prefix := _brew_prefix()) is not None:
        return prefix / "etc" / "loglens"
    base = os.environ.get("XDG_CONFIG_HOME", "~/.config")
    return _expand(base) / "loglens"


def config_file(explicit_dir: str | os.PathLike[str] | None = None) -> Path:
    return config_dir(explicit_dir) / CONFIG_FILENAME


def credentials_file(explicit_dir: str | os.PathLike[str] | None = None) -> Path:
    return config_dir(explicit_dir) / CREDENTIALS_FILENAME


def state_dir() -> Path:
    if env := os.environ.get("LOGLENS_STATE_DIR"):
        return _expand(env)
    if (prefix := _brew_prefix()) is not None:
        return prefix / "var" / "loglens"
    base = os.environ.get("XDG_STATE_HOME", "~/.local/state")
    return _expand(base) / "loglens"
