import re
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loglens.app import create_app
from loglens.config import Config, ExtractionConfig, ResolverConfig, ServerConfig
from loglens.dates import normalize_date
from loglens.reconcile import normalize


def _make_app(tmp_path, parallel_pages: int = 10):
    cfg = Config(
        state_dir=tmp_path,
        server=ServerConfig(),
        extraction=ExtractionConfig(provider="stub", parallel_pages=parallel_pages),
        resolver=ResolverConfig(),
    )
    app = create_app(cfg)
    return TestClient(app), app


@pytest.fixture
def app_client(tmp_path):
    return _make_app(tmp_path)


def _upload(client) -> str:
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
    return r.url.path.split("/jobs/")[1]


def _wait(client, job_id: str, timeout: float = 15.0) -> dict:
    status = {"finished": False}
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["finished"]:
            return status
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def _upload_and_wait(client) -> str:
    job_id = _upload(client)
    _wait(client, job_id)
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


def _gate_extraction(monkeypatch):
    """Patch _extract_one so pages block until the test releases them."""

    import loglens.app as app_mod

    original = app_mod._extract_one
    release = threading.Event()
    calls: list[tuple[str, int]] = []

    def gated(appx, job_id, pdf, page):
        calls.append((job_id, page))
        release.wait(timeout=10)
        original(appx, job_id, pdf, page)

    monkeypatch.setattr(app_mod, "_extract_one", gated)
    return release, calls


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_queue_serializes_jobs(tmp_path, monkeypatch):
    client, app = _make_app(tmp_path)
    release, calls = _gate_extraction(monkeypatch)

    job_a = _upload(client)
    _wait_for(lambda: calls)  # A's pages are in flight (blocked)

    job_b = _upload(client)
    assert app.state.db.get_job(job_b)["status"] == "queued"

    # Live status: A processing, B waiting; the jobs list shows it.
    active = {j["id"]: j for j in client.get("/status/active").json()["jobs"]}
    assert active[job_a]["status"] == "processing"
    assert active[job_b]["status"] == "queued"
    page = client.get("/").text
    assert "Waiting..." in page

    release.set()
    _wait(client, job_a)
    _wait(client, job_b)

    # Strict FIFO: every page of A was dispatched before any page of B.
    a_idx = [i for i, (j, _) in enumerate(calls) if j == job_a]
    b_idx = [i for i, (j, _) in enumerate(calls) if j == job_b]
    assert a_idx and b_idx and max(a_idx) < min(b_idx)
    assert client.get("/status/active").json()["jobs"] == []

    # Progress counts are exposed on the jobs list query.
    jobs = {j["id"]: j for j in app.state.db.list_jobs()}
    assert jobs[job_a]["done_pages"] == jobs[job_a]["page_count"]


def test_cancel_queued_job(tmp_path, monkeypatch):
    client, app = _make_app(tmp_path)
    release, calls = _gate_extraction(monkeypatch)

    job_a = _upload(client)
    _wait_for(lambda: calls)
    job_b = _upload(client)

    r = client.post(f"/jobs/{job_b}/cancel", follow_redirects=False)
    assert r.status_code == 303
    assert app.state.db.get_job(job_b)["status"] == "cancelled"
    assert client.get(f"/jobs/{job_b}/status").json()["finished"] is True
    statuses = {s["status"] for s in app.state.db.sheet_statuses(job_b)}
    assert statuses == {"cancelled"}

    release.set()
    _wait(client, job_a)
    # The worker must never have touched the cancelled job.
    assert all(j == job_a for (j, _) in calls)


def test_cancel_mid_processing(tmp_path, monkeypatch):
    # parallel_pages=1 so later pages are still undispatched when we cancel.
    client, app = _make_app(tmp_path, parallel_pages=1)
    release, calls = _gate_extraction(monkeypatch)

    job_id = _upload(client)
    _wait_for(lambda: calls)  # page 0 in flight, the rest waiting

    r = client.post(f"/jobs/{job_id}/cancel", follow_redirects=False)
    assert r.status_code == 303
    assert app.state.db.get_job(job_id)["status"] == "cancelling"

    release.set()
    status = _wait(client, job_id)
    assert status["job_status"] == "cancelled"

    sheets = {s["page_index"]: s["status"] for s in app.state.db.sheet_statuses(job_id)}
    assert sheets[0] == "done"  # the in-flight page was allowed to finish
    assert all(v == "cancelled" for k, v in sheets.items() if k != 0)
    assert calls == [(job_id, 0)]  # no further pages were started

    # The page still renders: done sheet editable, the rest marked cancelled.
    page = client.get(f"/jobs/{job_id}").text
    assert "sheet-form" in page
    assert "Cancelled before processing." in page


def test_sheet_fragment_endpoint(app_client):
    client, app = app_client
    job_id = _upload_and_wait(client)

    r = client.get(f"/jobs/{job_id}/sheets/0/html")
    assert r.status_code == 200
    assert 'id="sheet-0"' in r.text
    assert 'data-sheet-status="done"' in r.text
    assert "sheet-form" in r.text
    assert "<html" not in r.text.lower()  # a fragment, not a full page
    assert client.get(f"/jobs/{job_id}/sheets/99/html").status_code == 404


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
