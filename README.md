# LogLens

Self-hosted web app that turns scanned **trip-log PDFs** into reviewable,
correctable summaries and **CSV exports**. Each PDF page is treated as one
trip-log sheet.

- **OCR / extraction:** a cloud vision LLM (Anthropic Claude by default) reads
  the handwritten sheet and returns *structured* data via a forced tool call,
  with a per-field confidence score. An offline `stub` provider lets you run the
  whole app with no API key.
- **Confidence everywhere:** every extracted value carries a confidence (0-100);
  fields are shaded yellow (medium confidence) or red (low confidence) for
  review, and curated fields carry ranked next-best alternatives with one-click
  swap.
- **Curated reference lists:** four internally curated "source of truth" sets -
  **locations, drivers, trucks, trailers** - live in SQLite, start empty, and
  grow from your corrections. Raw OCR readings are reconciled against them
  (learned shorthand first, then fuzzy match); unmatched readings pass through
  as-is until you correct them. Saving a correction adds new values to the
  lists and records the raw reading as learned shorthand for future sheets.
  Manage all four lists on the Settings page.
- **Review UI:** a side-by-side view of the scanned page and an editable table,
  with live per-page progress, confidence/validation shading, top-3 swap chips,
  per-sheet *Re-run OCR* / *Re-resolve*, and inline saves.
- **Export:** per-sheet and combined CSV (with parallel `*_confidence` columns).

## Quick start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"

# Run fully offline with the sample fixtures (no API key needed):
LOGLENS_EXTRACTION_PROVIDER=stub loglens serve
# open http://127.0.0.1:8765 and upload driver.pdf
```

> Note: use a regular install (`pip install .`), not an editable install
> (`pip install -e .`). Editable installs write a bare-path `.pth` into
> site-packages that some environments (notably project trees synced by iCloud
> Drive) fail to honor, which makes `import loglens` fail intermittently. After
> changing source code, re-run `pip install --no-deps .`. Tests don't need an
> install (pytest is configured with `pythonpath = ["src"]`). For the most
> reliable local runtime, create the venv outside any cloud-synced folder, e.g.
> `python3.12 -m venv ~/.venvs/loglens`.

To use real OCR, seed the config files, add your Anthropic key, and verify:

```bash
loglens init               # writes config.toml + credentials.toml (0600)
# put your key in credentials.toml ([anthropic] api_key = "sk-ant-...")
loglens check              # validates the key + model with a tiny request
loglens serve
```

`ANTHROPIC_API_KEY` is also honored if you'd rather not use the credentials file.

## Configuration

Settings live in two TOML files in the config directory:

- `config.toml` — `[server]`, `[extraction]` (provider/model/render_dpi/max_tokens),
  `[resolver]`.
- `credentials.toml` — `[anthropic] api_key = "..."`, written `0600` and kept out
  of version control.

```bash
loglens init               # seeds both files and prints their paths
```

**Config directory** discovery (first match wins): `--config-dir` flag >
`LOGLENS_CONFIG_DIR` > Homebrew `etc/loglens` (only when installed via brew) >
`~/.config/loglens`. **State** (SQLite DB, uploads, page renders) lives under
`~/.local/state/loglens` (or the brew `var` dir / `LOGLENS_STATE_DIR`).

Key settings: `extraction.provider` (`anthropic` | `stub`), `extraction.model`
(`claude-sonnet-4-6` default; `claude-opus-4-8` for very messy handwriting),
`resolver.match_threshold` (below this score a raw reading passes through
unreconciled), `resolver.max_alternates`, and `server.host`/`port`.

## CLI

```
loglens serve [--host H] [--port P]    # run the web server (default command)
loglens init [--force]                 # seed config.toml + credentials.toml
loglens check                          # verify the extraction provider connection
```

All commands accept `--config-dir DIR` to point at a specific config directory.

## Reference lists

The four curated sets (locations, drivers, trucks, trailers) are stored in the
SQLite database and managed entirely through the app:

- They start empty. When a processed value can't be reconciled, the raw OCR
  reading shows in the field; fix it (type-ahead suggests existing entries) and
  hit *Save corrections* - the value joins its list, and the raw reading is
  remembered as shorthand for that value (one canonical value can accumulate
  any number of learned shorthands).
- Edit, add, or remove entries and learned shorthand any time on the
  **Settings** page.
