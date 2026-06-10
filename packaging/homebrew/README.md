# Distributing LogLens via Homebrew

LogLens is shipped as a single Homebrew formula that installs into an isolated
Python virtualenv and runs as a `brew services` daemon on macOS and Linux.

## One-time: create a tap

A "tap" is just a Git repo named `homebrew-<something>`:

```bash
# e.g. github.com/YOURORG/homebrew-loglens
mkdir -p homebrew-loglens/Formula
cp loglens.rb homebrew-loglens/Formula/loglens.rb
git -C homebrew-loglens init && git -C homebrew-loglens add . && git -C homebrew-loglens commit -m "loglens 0.1.0"
# push to GitHub as YOURORG/homebrew-loglens
```

## Install / run as a service

```bash
brew tap YOURORG/loglens
brew install loglens

# configure: seed config.toml + credentials.toml (0600) under $(brew --prefix)/etc/loglens
loglens init
$EDITOR "$(brew --prefix)/etc/loglens/credentials.toml"   # [anthropic] api_key = "sk-ant-..."
# (or set extraction.provider = "stub" in config.toml to run with no API key)
loglens check                              # verify the key + model

brew services start loglens                # persistent daemon (launchd/systemd)
open http://127.0.0.1:8765
```

When installed via Homebrew, LogLens stores config under
`$(brew --prefix)/etc/loglens` and state under `$(brew --prefix)/var/loglens`,
both of which survive upgrades. Logs: `$(brew --prefix)/var/log/loglens.log`.
Stop with `brew services stop loglens`.

## Cutting a release / pushing updates

1. Tag and create a GitHub release for the app repo (e.g. `v0.1.1`).
2. Get the tarball checksum:

   ```bash
   curl -L https://github.com/YOURORG/loglens/archive/refs/tags/v0.1.1.tar.gz | shasum -a 256
   ```

3. In the tap, update `url`, `version`, and `sha256` in `Formula/loglens.rb`; commit and push.
4. Users update with:

   ```bash
   brew update && brew upgrade loglens && brew services restart loglens
   ```

## Reproducible dependency pinning (optional)

The bundled formula uses a pragmatic `pip install` (deps resolved from PyPI at
install time). For fully pinned, offline-reproducible builds, generate
`resource` stanzas and switch to `virtualenv_install_with_resources`:

```bash
brew update-python-resources loglens
```
