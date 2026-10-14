from __future__ import annotations

import io
import json
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

from print_appliance import db as store
from print_appliance.auth import password_hash
from print_appliance.service import hash_api_key
from tests.conftest import Harness

PDF = b"%PDF-1.7\nsmall test content\n"


def post_pdf(harness: Harness, key: str, idem: str, *, content: bytes = PDF, options: str = "{}"):
    return harness.api.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": idem},
        data={
            "printer_id": harness.printer["id"],
            "format": "pdf",
            "title": "Test invoice",
            "copies": "1",
            **({"options": options} if json.loads(options) else {}),
        },
        files={"file": ("invoice.pdf", content, "application/pdf")},
    )


def test_scoped_auth_jobs_and_client_privacy(harness: Harness) -> None:
    assert harness.api.get("/api/v1/printers").status_code == 401
    first_printers = harness.api.get(
        "/api/v1/printers", headers={"Authorization": f"Bearer {harness.key}"}
    )
    assert first_printers.status_code == 200
    assert [printer["id"] for printer in first_printers.json()] == [harness.printer["id"]]
    assert "device_uri" not in first_printers.json()[0]

    other_key = "pa_other_client_secret_value_0123456789"
    other_id = str(uuid.uuid4())
    db = harness.appliance.db()
    try:
        db.execute(
            "INSERT INTO clients(id,name,key_hash,created_at) VALUES(?,?,?,datetime('now'))",
            (other_id, "Other", hash_api_key(other_key)),
        )
        db.execute(
            "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)",
            (other_id, harness.printer["id"]),
        )
    finally:
        db.close()
    other = harness.api.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {other_key}", "Idempotency-Key": "other-1"},
        data={
            "printer_id": harness.printer["id"],
            "format": "pdf",
            "title": "Other",
            "copies": "1",
        },
        files={"file": ("x.pdf", PDF, "application/pdf")},
    )
    assert other.status_code == 202
    hidden = harness.api.get(
        f"/api/v1/jobs/{other.json()['job_id']}", headers={"Authorization": f"Bearer {harness.key}"}
    )
    assert hidden.status_code == 404
    own = post_pdf(harness, harness.key, "own-1")
    assert own.status_code == 202
    listed = harness.api.get("/api/v1/jobs", headers={"Authorization": f"Bearer {harness.key}"})
    assert [job["job_id"] for job in listed.json()] == [own.json()["job_id"]]


def test_capabilities_scoped_schema_and_option_constraints(harness: Harness) -> None:
    auth = {"Authorization": f"Bearer {harness.key}"}
    url = "/api/v1/printers"
    assert harness.api.get(url).status_code == 401
    response = harness.api.get(url, headers=auth)
    assert response.status_code == 200
    schema = response.json()[0]["capabilities"]
    assert schema["availability"] == "available"
    assert "A4" in schema["supported"]["paper_size"]
    assert harness.api.get(f"/api/v1/printers/{harness.printer['id']}/capabilities", headers=auth).status_code == 404
    harness.backend.available = False
    unavailable = harness.api.get(url, headers=auth)
    assert unavailable.status_code == 200
    assert unavailable.json()[0]["capabilities"]["availability"] == "unavailable"


def test_capability_constraints_and_idempotent_replay_before_schema(harness: Harness) -> None:
    allowed = {
        "Duplex": ["DuplexNoTumble", "DuplexTumble"],
        "PageSize": ["A4"],
        "BindEdge": ["Left"],
    }
    assert harness.appliance.edit_printer(harness.printer["id"], {"allowed_options": allowed, "default_options": {"BindEdge": "Left"}})
    valid = post_pdf(
        harness,
        harness.key,
        "valid-combination",
        options='{"sides":"two-sided-long-edge","paper_size":"A4"}',
    )
    assert valid.status_code == 202
    invalid = post_pdf(
        harness,
        harness.key,
        "invalid-combination",
        options='{"sides":"two-sided-short-edge","paper_size":"A4"}',
    )
    assert invalid.status_code == 422
    original = post_pdf(harness, harness.key, "schema-replay")
    assert original.status_code == 202
    harness.backend.available = False
    replay = post_pdf(harness, harness.key, "schema-replay")
    assert replay.status_code == 200
    assert replay.json()["job_id"] == original.json()["job_id"]


def test_format_options_size_and_idempotency_validation(harness: Harness) -> None:
    invalid = post_pdf(harness, harness.key, "bad-signature", content=b"not a PDF")
    assert invalid.status_code == 422
    options = post_pdf(harness, harness.key, "bad-option", options='{"paper_size":"invalid"}')
    assert options.status_code == 422
    missing_key = harness.api.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {harness.key}"},
        data={"printer_id": harness.printer["id"], "format": "pdf", "title": "x"},
        files={"file": ("x.pdf", PDF, "application/pdf")},
    )
    assert missing_key.status_code == 422
    duplicate = post_pdf(harness, harness.key, "idem-1")
    replay = post_pdf(harness, harness.key, "idem-1")
    changed = post_pdf(harness, harness.key, "idem-1", content=b"%PDF-1.7\ndifferent")
    assert duplicate.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["deduplicated"] is True
    assert replay.json()["job_id"] == duplicate.json()["job_id"]
    assert changed.status_code == 409

    db = harness.appliance.db()
    try:
        store.save_setting(db, "max_upload_bytes", 40)
    finally:
        db.close()
    oversized = post_pdf(harness, harness.key, "oversized", content=PDF + b"x" * 100)
    assert oversized.status_code == 413


def test_idempotency_retry_survives_grant_and_printer_config_change_but_not_key_revocation(
    harness: Harness,
) -> None:
    accepted = post_pdf(harness, harness.key, "retry-after-edit")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    db = harness.appliance.db()
    try:
        db.execute("DELETE FROM client_printers WHERE client_id=?", (harness.client_id,))
        db.execute("UPDATE printers SET name='Edited printer' WHERE id=?", (harness.printer["id"],))
    finally:
        db.close()
    replay = post_pdf(harness, harness.key, "retry-after-edit")
    assert replay.status_code == 200
    assert replay.json()["job_id"] == job_id
    db = harness.appliance.db()
    try:
        db.execute("UPDATE clients SET revoked=1 WHERE id=?", (harness.client_id,))
    finally:
        db.close()
    revoked = post_pdf(harness, harness.key, "retry-after-edit")
    assert revoked.status_code == 401


def test_concurrent_idempotent_acceptance_creates_one_job(harness: Harness) -> None:
    barrier = threading.Barrier(8)

    def send() -> tuple[dict[str, Any], int]:
        upload = SimpleNamespace(file=io.BytesIO(PDF), filename="invoice.pdf")
        barrier.wait()
        return harness.appliance.accept_job(
            client_id=harness.client_id,
            idempotency_key="race-key",
            printer_id=harness.printer["id"],
            format_name="pdf",
            title="Test invoice",
            copies_raw="1",
            options_raw="{}",
            filename="invoice.pdf",
            upload=upload,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: send(), range(8)))
    assert sorted(code for _, code in responses) == [200] * 7 + [202]
    assert len({result["job_id"] for result, _ in responses}) == 1
    db = harness.appliance.db()
    try:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 1
    finally:
        db.close()


def test_csrf_login_and_admin_route_protection(harness: Harness, tmp_path) -> None:
    password = secrets.token_urlsafe(24)
    salt = b"0123456789abcdef"
    db = harness.appliance.db()
    try:
        db.execute(
            "INSERT INTO admin(id,salt,password_hash,changed_at) VALUES(1,?,?,datetime('now'))",
            (salt.hex(), password_hash(password, salt)),
        )
    finally:
        db.close()
    assert harness.api.get("/admin/api/status").status_code == 401
    login = harness.api.post("/admin/api/login", json={"password": password})
    assert login.status_code == 200
    assert "pa_admin" in harness.api.cookies
    csrf = login.json()["csrf_token"]
    assert harness.api.get("/admin/api/status").status_code == 200
    denied = harness.api.put("/admin/api/settings", json={"history_retention_days": 31})
    assert denied.status_code == 403
    saved = harness.api.put(
        "/admin/api/settings", json={"history_retention_days": 31}, headers={"X-CSRF-Token": csrf}
    )
    assert saved.status_code == 200
    cross_origin = harness.api.put(
        "/admin/api/settings",
        json={"history_retention_days": 30},
        headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.invalid"},
    )
    assert cross_origin.status_code == 403
    logout = harness.api.post("/admin/api/logout", json={}, headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert harness.api.get("/admin/api/status").status_code == 401


def test_tombstone_survives_history_cleanup_and_keeps_job_id(harness: Harness) -> None:
    accepted = post_pdf(harness, harness.key, "tombstone").json()
    job_id = accepted["job_id"]
    harness.appliance._set_status(job_id, "completed", "CUPS reports completed")
    db = harness.appliance.db()
    try:
        db.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job_id,))
        store.save_setting(db, "history_retention_days", 30)
    finally:
        db.close()
    assert harness.appliance.cleanup_history() == 1
    db = harness.appliance.db()
    try:
        assert db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None
        tombstone = db.execute(
            "SELECT history_expired,status FROM idempotency WHERE job_id=?", (job_id,)
        ).fetchone()
        assert tombstone["history_expired"] == 1
        assert tombstone["status"] == "completed"
    finally:
        db.close()
    replay = post_pdf(harness, harness.key, "tombstone")
    assert replay.status_code == 200
    assert replay.json() == {
        "job_id": job_id,
        "status": "completed",
        "deduplicated": True,
        "history_expired": True,
    }
    assert not (harness.appliance.settings.spool_dir / f"{job_id}.payload").exists()


def test_zpl_requires_utf8_command_frame(harness: Harness) -> None:
    malformed = harness.api.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {harness.key}", "Idempotency-Key": "zpl-bad"},
        data={
            "printer_id": harness.printer["id"],
            "format": "zpl",
            "title": "ZPL",
            "copies": "1",
        },
        files={"file": ("job.zpl", b"\xff", "text/plain")},
    )
    valid = harness.api.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {harness.key}", "Idempotency-Key": "zpl-ok"},
        data={
            "printer_id": harness.printer["id"],
            "format": "zpl",
            "title": "ZPL",
            "copies": "1",
        },
        files={"file": ("job.zpl", b"^XA^FO10,10^FDtest^FS^XZ", "text/plain")},
    )
    assert malformed.status_code == 422
    assert valid.status_code == 202
