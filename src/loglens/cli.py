"""Command-line interface: ``loglens serve`` and helpers."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, paths
from .config import Config, get_config, load_config

SAMPLE_CONFIG = """\
# LogLens configuration (TOML). All values are optional; defaults shown.
# Secrets (the Anthropic API key) live in credentials.toml, not here.

# state_dir = "~/.local/state/loglens"

[server]
host = "127.0.0.1"
port = 8765

[extraction]
provider = "anthropic"          # anthropic | stub
model = "claude-sonnet-4-6"     # or "claude-opus-4-8" for very messy handwriting
render_dpi = 200
max_tokens = 4096
# Pages of the active job OCR'd concurrently (hard ceiling 25). The default
# of 10 suits Anthropic Tier 2+ accounts; on Tier 1 set this to 2-3 to stay
# under the 8k output-tokens/min limit.
parallel_pages = 10

[resolver]
match_threshold = 70            # 0-100; below this a raw reading passes through
max_alternates = 5
"""

SAMPLE_CREDENTIALS = """\
# LogLens credentials (TOML). Keep this file private (chmod 0600) and out of
# version control. Alternatively set the ANTHROPIC_API_KEY environment variable.

[anthropic]
api_key = "sk-ant-..."
"""


def _config_for(args: argparse.Namespace) -> Config:
    """Load config honoring an optional --config-dir, ensuring dirs exist."""

    cdir = getattr(args, "config_dir", None)
    cfg = load_config(cdir) if cdir else get_config()
    cfg.ensure_dirs()
    return cfg


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = _config_for(args)
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    # Pass the resolved config dir to the worker process via env.
    os.environ.setdefault("LOGLENS_CONFIG_DIR", str(cfg.config_dir))
    uvicorn.run("loglens.app:create_app", host=host, port=port, factory=True)
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    cdir = paths.config_dir(getattr(args, "config_dir", None))
    cdir.mkdir(parents=True, exist_ok=True)

    targets = [
        (cdir / paths.CONFIG_FILENAME, SAMPLE_CONFIG, None),
        (cdir / paths.CREDENTIALS_FILENAME, SAMPLE_CREDENTIALS, 0o600),
    ]
    for path, content, mode in targets:
        if path.exists() and not args.force:
            print(f"  exists, skipping: {path}")
            continue
        path.write_text(content, encoding="utf-8")
        if mode is not None:
            os.chmod(path, mode)
        print(f"  wrote: {path}")

    print()
    print(f"Config directory: {cdir}")
    print("Next steps:")
    print(f"  1. Put your Anthropic API key in {cdir / paths.CREDENTIALS_FILENAME}")
    print("     (or export ANTHROPIC_API_KEY)")
    print("  2. Verify the connection:  loglens check")
    print("  3. Start the server:       loglens serve")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from .extraction import build_extractor

    cfg = _config_for(args)
    print(f"Provider: {cfg.extraction.provider}")
    print(f"Model:    {cfg.extraction.model}")
    print(f"Config:   {cfg.config_file}")

    try:
        extractor = build_extractor(cfg.extraction)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to initialize extractor: {exc}")
        return 1

    ok, detail = extractor.verify()
    print(("OK: " if ok else "FAILED: ") + detail)
    return 0 if ok else 1


def _add_config_dir(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config-dir",
        default=None,
        help="Directory holding config.toml + credentials.toml (overrides discovery).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loglens", description="LogLens trip-log OCR service")
    parser.add_argument("--version", action="version", version=f"loglens {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the web server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    _add_config_dir(serve)
    serve.set_defaults(func=_cmd_serve)

    init = sub.add_parser("init", help="Seed config + credentials files")
    init.add_argument("--force", action="store_true", help="Overwrite existing files")
    _add_config_dir(init)
    init.set_defaults(func=_cmd_init)

    check = sub.add_parser("check", help="Verify the extraction provider connection")
    _add_config_dir(check)
    check.set_defaults(func=_cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["serve", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
