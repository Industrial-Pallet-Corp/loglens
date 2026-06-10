# LogLens Homebrew formula.
#
# This formula installs LogLens into a self-contained Python virtualenv under
# libexec and registers it as a `brew services` daemon (launchd on macOS,
# systemd on Linuxbrew).
#
# Distribution / update workflow:
#   1. Cut a GitHub release; set `url` to the release tarball and `sha256` below.
#   2. Bump `version` (or let Homebrew infer it from the tag).
#   3. Users run:  brew update && brew upgrade loglens
#
# Two dependency strategies (pick one):
#   (A) Pinned resources (most reproducible, Homebrew-core style):
#       run `brew update-python-resources loglens` to auto-generate `resource`
#       stanzas, then use `virtualenv_install_with_resources`.
#   (B) Pragmatic pip install (simplest for a personal tap; used below): pip
#       resolves dependencies from PyPI at install time.
class Loglens < Formula
  include Language::Python::Virtualenv

  desc "Self-hosted OCR + review tool for trucking trip-log PDFs"
  homepage "https://github.com/YOURORG/loglens"
  url "https://github.com/YOURORG/loglens/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_TARBALL_SHA256"
  license "MIT"
  version "0.1.0"

  depends_on "python@3.12"
  # PyMuPDF and others ship wheels, so no extra system build deps are needed.

  def install
    # Strategy (B): create an isolated venv and install the package + its PyPI
    # dependencies into it. For strategy (A), replace this with
    # `virtualenv_install_with_resources`.
    venv = virtualenv_create(libexec, "python3.12")
    system libexec/"bin/pip", "install", "--upgrade", "pip"
    system libexec/"bin/pip", "install", buildpath
    bin.install_symlink libexec/"bin/loglens"
  end

  service do
    run [opt_bin/"loglens", "serve"]
    keep_alive true
    log_path var/"log/loglens.log"
    error_log_path var/"log/loglens.log"
    working_dir var
  end

  test do
    assert_match "loglens", shell_output("#{bin}/loglens --version")
  end
end
