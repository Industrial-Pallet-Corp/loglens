"""Command-line interface: ``loglens serve`` and helpers."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import default_config_path, get_config, load_config

SAMPLE_CONFIG = """\
# LogLens configuration (TOML). All values are optional; defaults shown.

# state_dir = "~/.local/state/loglens"

[server]
host = "127.0.0.1"
port = 8765

[extraction]
provider = "anthropic"          # anthropic | stub
model = "claude-3-5-sonnet-latest"
# api_key = "sk-ant-..."        # or set ANTHROPIC_API_KEY in the environment
render_dpi = 200

[resolver]
source = "seed"                 # seed (Phase 1) | redshift (Phase 2)
# seed_file = "~/loglens/locations.csv"
# aliases_file = "~/loglens/aliases.yaml"
match_threshold = 70
max_alternates = 5
"""


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = get_config()
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    uvicorn.run("loglens.app:create_app", host=host, port=port, factory=True)
    return 0


def _cmd_init_config(args: argparse.Namespace) -> int:
    path = default_config_path()
    if path.exists() and not args.force:
        print(f"Config already exists at {path} (use --force to overwrite).")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    print(f"Wrote sample config to {path}")
    return 0


def _cmd_refresh_locations(args: argparse.Namespace) -> int:
    from .db import Database
    from .resolver import Resolver

    cfg = get_config()
    db = Database(cfg.db_path)
    resolver = Resolver(db, cfg.resolver)
    count = resolver.refresh_cache()
    print(f"Loaded {count} locations from source '{cfg.resolver.source}'.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loglens", description="LogLens trip-log OCR service")
    parser.add_argument("--version", action="version", version=f"loglens {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the web server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=_cmd_serve)

    init = sub.add_parser("init-config", help="Write a sample config file")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init_config)

    refresh = sub.add_parser("refresh-locations", help="Reload the locations cache")
    refresh.set_defaults(func=_cmd_refresh_locations)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["serve", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
