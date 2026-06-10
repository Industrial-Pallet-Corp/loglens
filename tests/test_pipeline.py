import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loglens.app import create_app
from loglens.config import Config, ExtractionConfig, ResolverConfig, ServerConfig
from loglens.reconcile import normalize


@pytest.fixture
def app_client(tmp_path):
    cfg = Config(
        state_dir=tmp_path,
        server=ServerConfig(),
        extraction=ExtractionConfig(provider="stub"),
        resolver=ResolverConfig(),
    )
    app = create_app(cfg)
    return TestClient(app), app


def _upload_and_wait(client) -> str:
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
    job_id = r.url.path.split("/jobs/")[1]
    status = {"finished": False}
    for _ in range(150):
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["finished"]:
            break
        time.sleep(0.1)
    assert status["finished"], "job did not finish in time"
    return job_id


def test_normalize():
    assert normalize("  T+L N ") == "t l n"
    assert normalize("Random Hse!") == "random hse"


def test_lists_start_empty(app_client):
    client, app = app_client
    r = client.get("/")
    assert r.status_code == 200
    counts = app.state.db.ref_counts()
    assert counts == {"location": 0, "driver": 0, "truck": 0, "trailer": 0}


def test_passthrough_then_learn_then_reconcile(app_client):
    client, app = app_client
    job_id = _upload_and_wait(client)

    # With empty lists, raw OCR readings pass straight through to the
    # resolved fields (stub page 0 has place "CH" and driver "Joe Vail").
    sheet = app.state.db.get_sheet(job_id, 0)
    assert sheet.rows[0].place.value == "CH"
    assert sheet.rows[0].place.source == "ocr"
    assert sheet.driver.value == "Joe Vail"

    # Saving a correction locks values in: new canonical entries + learned
    # shorthand for readings that differ from the saved value.
    form = {"driver": "Joseph Vail", "row-0-place": "Central Hub"}
    r = client.post(
        f"/jobs/{job_id}/sheets/0/save", data=form, headers={"X-Inline": "1"}
    )
    assert r.status_code == 200

    db = app.state.db
    locations = [e["value"] for e in db.list_ref_values("location")]
    drivers = [e["value"] for e in db.list_ref_values("driver")]
    trucks = [e["value"] for e in db.list_ref_values("truck")]
    assert "Central Hub" in locations
    assert "Joseph Vail" in drivers
    assert "295-581" in trucks  # unedited OCR value is learned as canonical

    aliases = {a["raw"]: a["value"] for a in db.list_ref_aliases("location")}
    assert aliases.get("CH") == "Central Hub"
    assert {a["raw"]: a["value"] for a in db.list_ref_aliases("driver")}.get(
        "Joe Vail"
    ) == "Joseph Vail"

    # Re-resolving now reconciles the learned shorthand with high confidence.
    r = client.post(f"/jobs/{job_id}/sheets/1/reresolve", follow_redirects=False)
    assert r.status_code == 303
    sheet1 = db.get_sheet(job_id, 1)
    ch_rows = [row for row in sheet1.rows if (row.place_raw.value or "") == "CH"]
    assert ch_rows, "expected a CH row on stub page 1"
    assert ch_rows[0].place.value == "Central Hub"
    assert ch_rows[0].place.source == "resolver"
    assert ch_rows[0].place.confidence and ch_rows[0].place.confidence >= 90


def test_settings_list_crud(app_client):
    client, app = app_client
    db = app.state.db

    # Add via endpoint
    r = client.post(
        "/admin/lists/location", data={"value": "Central Hub"}, follow_redirects=False
    )
    assert r.status_code == 303
    entries = db.list_ref_values("location")
    assert [e["value"] for e in entries] == ["Central Hub"]

    # Settings page renders the entry
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Central Hub" in page.text

    # Delete via endpoint (cascades aliases)
    ref_id = entries[0]["id"]
    db.add_ref_alias("location", "CH", "ch", ref_id)
    r = client.post(f"/admin/lists/location/{ref_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert db.list_ref_values("location") == []
    assert db.list_ref_aliases("location") == []

    # Unknown kinds are rejected
    assert client.post("/admin/lists/banana", data={"value": "x"}).status_code == 404


def test_csv_export_uses_saved_values(app_client):
    client, app = app_client
    job_id = _upload_and_wait(client)

    client.post(
        f"/jobs/{job_id}/sheets/0/save",
        data={"row-0-place": "Central Hub"},
        headers={"X-Inline": "1"},
    )
    csv_resp = client.get(f"/jobs/{job_id}/export.csv")
    assert csv_resp.status_code == 200
    body = csv_resp.text
    assert "place_resolved" in body.splitlines()[0]
    assert "Central Hub" in body
