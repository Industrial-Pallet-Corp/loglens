import re
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loglens.app import create_app
from loglens.config import Config, ExtractionConfig, ResolverConfig, ServerConfig
from loglens.dates import normalize_date
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


def test_normalize_date():
    midyear = date(2026, 6, 11)

    # Common handwritten conventions, all coerced to mm/dd/yy.
    assert normalize_date("6-26", midyear) == "06/26/26"
    assert normalize_date("6/26", midyear) == "06/26/26"
    assert normalize_date("06.26.26", midyear) == "06/26/26"
    assert normalize_date("2026-06-26", midyear) == "06/26/26"
    assert normalize_date("26/6", midyear) == "06/26/26"  # d/m swapped

    # Off-year readings are forced to the current year.
    assert normalize_date("6/26/25", midyear) == "06/26/26"
    assert normalize_date("6/26/2031", midyear) == "06/26/26"

    # Within a week of the year boundary, the adjacent year is allowed.
    assert normalize_date("1/2/27", date(2026, 12, 28)) == "01/02/27"
    assert normalize_date("12/30/26", date(2027, 1, 3)) == "12/30/26"
    # ...but not outside that window.
    assert normalize_date("1/2/27", midyear) == "01/02/26"

    # Missing year near the boundary: best guess is the closest allowed year.
    assert normalize_date("12-30", date(2027, 1, 3)) == "12/30/26"
    assert normalize_date("1-2", date(2026, 12, 28)) == "01/02/27"

    # Hopeless readings stay untouched (caller keeps the original value).
    assert normalize_date("scribble", midyear) is None
    assert normalize_date("2/30", midyear) is None
    assert normalize_date(None, midyear) is None


def test_extraction_normalizes_dates(app_client):
    client, app = app_client
    job_id = _upload_and_wait(client)

    # Stub page 0 row 0 is dated "6-26"; the sheet-level date derives from it.
    sheet = app.state.db.get_sheet(job_id, 0)
    assert re.fullmatch(r"06/26/\d{2}", sheet.rows[0].date.value)
    assert sheet.rows[0].date.raw == "6-26"
    assert re.fullmatch(r"06/26/\d{2}", sheet.date.value)


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


def test_explicit_save_pins_only_submitted_fields(app_client):
    client, app = app_client
    job_id = _upload_and_wait(client)

    # Confirm-by-touch: the driver is posted unchanged with the explicit
    # marker, so it is pinned as user-confirmed; nothing else is touched.
    r = client.post(
        f"/jobs/{job_id}/sheets/0/save",
        data={"_explicit": "1", "driver": "Joe Vail"},
        headers={"X-Inline": "1"},
    )
    assert r.status_code == 200

    sheet = app.state.db.get_sheet(job_id, 0)
    assert sheet.driver.source == "user"
    assert sheet.driver.confidence == 100.0
    # Unsubmitted fields keep their OCR provenance and confidence.
    assert sheet.truck_no.source == "ocr"
    assert sheet.truck_no.confidence == 96.0
    assert sheet.rows[1].place.value == "Emerson Inno"
    assert sheet.rows[1].place.source == "ocr"


def test_learning_gate(app_client):
    client, app = app_client
    db = app.state.db
    job_id = _upload_and_wait(client)

    # Stub page 0: driver/truck and most places are high confidence (96), but
    # row 1's place "Emerson Inno" is low confidence (42).
    r = client.post(
        f"/jobs/{job_id}/sheets/0/save",
        data={"_explicit": "1", "driver": "Joseph Vail"},
        headers={"X-Inline": "1"},
    )
    assert r.status_code == 200

    drivers = [e["value"] for e in db.list_ref_values("driver")]
    trucks = [e["value"] for e in db.list_ref_values("truck")]
    locations = [e["value"] for e in db.list_ref_values("location")]
    assert "Joseph Vail" in drivers  # user-corrected
    assert "295-581" in trucks  # high confidence (96)
    assert "CH" in locations  # high confidence passthrough
    assert "Emerson Inno" not in locations  # low confidence: never learned

    # A deliberate correction of the low-confidence field is learned, along
    # with the raw OCR reading as an alias.
    r = client.post(
        f"/jobs/{job_id}/sheets/0/save",
        data={"_explicit": "1", "row-1-place": "Emerson Innovations"},
        headers={"X-Inline": "1"},
    )
    assert r.status_code == 200
    locations = [e["value"] for e in db.list_ref_values("location")]
    assert "Emerson Innovations" in locations
    aliases = {a["raw"]: a["value"] for a in db.list_ref_aliases("location")}
    assert aliases.get("Emerson Inno") == "Emerson Innovations"


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

    # Delete-all wipes one kind's values and aliases, leaving others intact.
    for v in ("Central Hub", "Lakeside"):
        client.post("/admin/lists/location", data={"value": v})
    client.post("/admin/lists/driver", data={"value": "Joseph Vail"})
    loc_id = db.list_ref_values("location")[0]["id"]
    db.add_ref_alias("location", "CH", "ch", loc_id)

    r = client.post("/admin/lists/location/clear", follow_redirects=False)
    assert r.status_code == 303
    assert db.list_ref_values("location") == []
    assert db.list_ref_aliases("location") == []
    assert [e["value"] for e in db.list_ref_values("driver")] == ["Joseph Vail"]
    assert client.post("/admin/lists/banana/clear").status_code == 404


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
