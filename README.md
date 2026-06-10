# LogLens

Self-hosted web app that turns scanned **trip-log PDFs** into reviewable,
correctable summaries and **CSV exports**. Each PDF page is treated as one
trip-log sheet.

- **OCR / extraction:** a cloud vision LLM (Anthropic Claude by default) reads
  the handwritten sheet and returns structured data. An offline `stub` provider
  lets you run the whole app with no API key.
- **Location resolution:** the messy/abbreviated *Place* column is matched
  against a cached list of canonical locations using fuzzy matching plus a
  shorthand alias map. In **Phase 1** that list comes from a seed file; **Phase
  2** swaps in a live AWS Redshift source behind the same interface.
- **Review UI:** a side-by-side view of the scanned page and an editable table,
  with low-confidence fields highlighted and one-click location suggestions.
- **Export:** per-sheet and combined CSV.

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

To use real OCR, set an Anthropic key and use the default provider:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
loglens serve
```

## Configuration

Create a config file (TOML):

```bash
loglens init-config        # writes ~/.config/loglens/config.toml
```

Key settings: `extraction.provider` (`anthropic` | `stub`), `extraction.model`,
`resolver.source` (`seed` for Phase 1), `resolver.seed_file`,
`resolver.aliases_file`, `resolver.match_threshold`, and `server.host`/`port`.
State (SQLite DB, uploads, page renders) lives under `~/.local/state/loglens`.

## CLI

```
loglens serve [--host H] [--port P]   # run the web server (default command)
loglens init-config [--force]          # write a sample config
loglens refresh-locations              # reload the locations cache from the source
```

## Locations seed file

Phase 1 reads canonical locations from a CSV (`location_id,name` or a single
`name` column) or YAML list, plus a shorthand `aliases.yaml`
(`SHORTHAND: Canonical Name`). Defaults are bundled; override with
`resolver.seed_file` / `resolver.aliases_file`.

## Roadmap

- **Phase 1 (this release):** web UI, PDF processing, vision-LLM extraction,
  seed-based location resolution, review UI, CSV export, Homebrew packaging.
- **Phase 2:** AWS Redshift read (locations sync) and write-back of processed
  miles via the Data API.
