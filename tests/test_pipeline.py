import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loglens.config import Config, ServerConfig, ExtractionConfig, ResolverConfig
from loglens.app import create_app
from loglens.resolver.matcher import normalize


@pytest.fixture
def app_client(tmp_path):
    cfg = Config(
        state_dir=tmp_path,
        server=ServerConfig(),
        extraction=ExtractionConfig(provider="stub"),
        resolver=ResolverConfig(source="seed"),
    )
    app = create_app(cfg)
    return TestClient(app), cfg


def test_normalize():
    assert normalize("  T+L N ") == "t l n"
    assert normalize("Random Hse!") == "random hse"


def test_locations_cache_seeded(app_client):
    client, cfg = app_client
    r = client.get("/")
    assert r.status_code == 200
    # Seed file has 22 locations.
    assert "cached locations" in r.text


def test_full_pipeline_with_sample_pdf(app_client):
    client, cfg = app_client
    pdf = Path(__file__).resolve().parents[1] / "driver.pdf"
    if not pdf.exists():
        pytest.skip("sample driver.pdf not present")
    with pdf.open("rb") as fh:
        r = client.post(
            "/upload",
            files={"file": ("driver.pdf", fh, "application/pdf")},
            follow_redirects=True,
        )
    assert r.status_code == 200
    # Stub fixture page 1 driver should be present and CH should resolve.
    assert "Joe Vail" in r.text
    assert "Central Hub" in r.text  # CH alias -> Central Hub

    # Combined CSV export works and contains resolved location + computed miles.
    job_id = r.url.path.split("/jobs/")[1]
    csv_resp = client.get(f"/jobs/{job_id}/export.csv")
    assert csv_resp.status_code == 200
    body = csv_resp.text
    assert "Central Hub" in body
    assert "place_resolved" in body.splitlines()[0]


def test_resolver_alias_and_fuzzy(app_client):
    client, cfg = app_client
    from loglens.db import Database
    from loglens.resolver import Resolver

    db = Database(cfg.db_path)
    resolver = Resolver(db, cfg.resolver)
    resolver.ensure_cache()

    best, alts = resolver.matcher.match("CH")
    assert best and best.name == "Central Hub"

    best, _ = resolver.matcher.match("Lakeside")  # exact-ish fuzzy
    assert best and best.name == "Lakeside Lumber"

    best, _ = resolver.matcher.match("CSL")  # alias
    assert best and best.name == "Cutstock Loadout"
