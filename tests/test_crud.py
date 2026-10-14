from __future__ import annotations

import io
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from print_appliance import db as store
from print_appliance.service import hash_api_key
from tests.conftest import Harness
from tests.test_api import PDF, post_pdf

ACTIVE_STATUSES = ("queued", "held", "submitting", "submitted", "unknown")


def admin_headers(harness: Harness) -> dict[str, str]:
    from print_appliance.cli import _bootstrap

    password = secrets.token_urlsafe(24)
    _bootstrap(harness.appliance.settings, password)
    login = harness.api.post("/admin/api/login", json={"password": password})
    assert login.status_code == 200
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def accept_job(harness: Harness, key: str) -> tuple[dict[str, Any], int]:
    return harness.appliance.accept_job(
        client_id=harness.client_id,
        idempotency_key=key,
        printer_id=harness.printer["id"],
        format_name="pdf",
        title="CRUD regression",
        copies_raw="1",
        options_raw="{}",
        filename="crud.pdf",
        upload=SimpleNamespace(file=io.BytesIO(PDF), filename="crud.pdf"),
    )


def test_capabilities_read_and_non_option_edit_do_not_mutate_cups(harness: Harness) -> None:
    headers = admin_headers(harness)
    before = {name: dict(attrs) for name, attrs in harness.backend.queue_data.items()}
    url = f"/admin/api/printers/{harness.printer['id']}/capabilities"
    assert harness.api.get(url, headers=headers).status_code == 200
    assert (
        harness.api.put(
            f"/admin/api/printers/{harness.printer['id']}",
            headers=headers,
            json={"name": "Renamed"},
        ).status_code
        == 200
    )
    edited = harness.api.put(
        f"/admin/api/printers/{harness.printer['id']}",
        headers=headers,
        json={"allowed_options": {"Duplex": ["DuplexTumble"]}},
    )
    assert edited.status_code == 200
    assert harness.backend.queue_data == before


def test_soft_delete_columns_migrate_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    import sqlite3

    with sqlite3.connect(path) as old_db:
        old_db.executescript(
            """
            CREATE TABLE printers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, queue TEXT NOT NULL UNIQUE,
                device_uri TEXT NOT NULL, driver TEXT NOT NULL, mapping_signature TEXT NOT NULL,
                managed INTEGER NOT NULL DEFAULT 0, formats_json TEXT NOT NULL,
                defaults_json TEXT NOT NULL, allowed_json TEXT NOT NULL,
                paused INTEGER NOT NULL DEFAULT 0, pause_reason TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE clients (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            INSERT INTO printers VALUES(
                'printer-old', 'Legacy printer', 'legacy_queue', 'usb://old/device', 'old-driver',
                'signature', 0, '["pdf"]', '{}', '{}', 0, NULL, 'created', 'updated'
            );
            INSERT INTO clients VALUES('client-old', 'Legacy client', 'hash', 0, 'created');
            """
        )

    store.initialize(path)
    db = store.connect(path)
    try:
        assert {row[1] for row in db.execute("PRAGMA table_info(printers)")} >= {
            "deleted_at",
            "mapping_signature",
        }
        assert {row[1] for row in db.execute("PRAGMA table_info(clients)")} >= {"deleted_at"}
        assert (
            db.execute("SELECT deleted_at FROM printers WHERE id='printer-old'").fetchone()[0]
            is None
        )
        assert (
            db.execute("SELECT deleted_at FROM clients WHERE id='client-old'").fetchone()[0] is None
        )
        assert (
            db.execute("SELECT queue FROM printers WHERE id='printer-old'").fetchone()[0]
            == "legacy_queue"
        )
    finally:
        db.close()


@pytest.mark.parametrize("status", ACTIVE_STATUSES)
def test_printer_delete_conflicts_with_every_nonterminal_job(harness: Harness, status: str) -> None:
    headers = admin_headers(harness)
    accepted = post_pdf(harness, harness.key, f"printer-busy-{status}")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    db = harness.appliance.db()
    try:
        db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    finally:
        db.close()

    response = harness.api.delete(f"/admin/api/printers/{harness.printer['id']}", headers=headers)
    assert response.status_code == 409
    assert harness.appliance.admin_printer(harness.printer["id"]) is not None
    assert harness.backend.queue_data[harness.printer["queue"]]


@pytest.mark.parametrize("status", ACTIVE_STATUSES)
def test_client_delete_conflicts_with_every_nonterminal_job(harness: Harness, status: str) -> None:
    headers = admin_headers(harness)
    accepted = post_pdf(harness, harness.key, f"client-busy-{status}")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    db = harness.appliance.db()
    try:
        db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    finally:
        db.close()

    response = harness.api.delete(f"/admin/api/clients/{harness.client_id}", headers=headers)
    assert response.status_code == 409
    assert harness.appliance.client_for_key(harness.key) is not None
    assert harness.api.get("/admin/api/clients", headers=headers).status_code == 200


def test_printer_delete_preserves_history_revokes_grants_and_reuses_cups_queue(
    harness: Harness,
) -> None:
    headers = admin_headers(harness)
    accepted = post_pdf(harness, harness.key, "deleted-printer-tombstone")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    harness.appliance._set_status(job_id, "completed", "CUPS reports completed")

    original_queue_data = {name: dict(attrs) for name, attrs in harness.backend.queue_data.items()}
    original_disabled = set(harness.backend.disabled)
    original_submits = list(harness.backend.submit_calls)
    original_releases = list(harness.backend.release_calls)
    original_cancels = list(harness.backend.cancel_calls)
    printer_id = harness.printer["id"]
    queue = harness.printer["queue"]

    missing_csrf = harness.api.delete(f"/admin/api/printers/{printer_id}")
    assert missing_csrf.status_code == 403
    deleted = harness.api.delete(f"/admin/api/printers/{printer_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"id": printer_id, "deleted": True}
    assert harness.api.get(f"/admin/api/printers/{printer_id}", headers=headers).status_code == 404
    assert (
        harness.api.put(
            f"/admin/api/printers/{printer_id}",
            json={"name": "No longer editable"},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        harness.api.post(f"/admin/api/printers/{printer_id}/resume", headers=headers).status_code
        == 404
    )
    assert harness.api.get("/admin/api/printers", headers=headers).json() == []
    assert (
        harness.api.get(
            "/api/v1/printers", headers={"Authorization": f"Bearer {harness.key}"}
        ).json()
        == []
    )
    assert post_pdf(harness, harness.key, "new-after-printer-delete").status_code == 403

    replay = post_pdf(harness, harness.key, "deleted-printer-tombstone")
    assert replay.status_code == 200
    assert replay.json()["job_id"] == job_id
    assert replay.json()["deduplicated"] is True
    history = harness.api.get(f"/admin/api/jobs/{job_id}", headers=headers)
    assert history.status_code == 200
    assert history.json()["status"] == "completed"
    assert history.json()["printer_snapshot"]["queue"] == queue
    assert any(event["status"] == "completed" for event in history.json()["events"])
    assert any(
        event["target"] == f"/admin/api/printers/{printer_id}"
        for event in harness.api.get("/admin/api/audit", headers=headers).json()
    )

    db = harness.appliance.db()
    try:
        record = db.execute(
            "SELECT deleted_at,queue FROM printers WHERE id=?", (printer_id,)
        ).fetchone()
        assert record["deleted_at"] is not None
        assert record["queue"] != queue
        assert (
            db.execute(
                "SELECT COUNT(*) FROM client_printers WHERE printer_id=?", (printer_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "completed"
        )
        assert db.execute("SELECT 1 FROM idempotency WHERE job_id=?", (job_id,)).fetchone()
    finally:
        db.close()

    # The same CUPS queue is still present and can be registered with a fresh application ID.
    replacement = harness.appliance.import_queue(
        queue=queue, name="Replacement registration", formats=["pdf"]
    )
    assert replacement["id"] != printer_id
    assert replacement["queue"] == queue
    assert replacement["paused"] is True
    assert (
        harness.api.get("/admin/api/printers", headers=headers).json()[0]["id"] == replacement["id"]
    )
    assert (
        harness.api.get(f"/admin/api/clients/{harness.client_id}", headers=headers).json()[
            "printer_ids"
        ]
        == []
    )
    assert harness.backend.queue_data == original_queue_data
    assert harness.backend.disabled == original_disabled
    assert harness.backend.submit_calls == original_submits
    assert harness.backend.release_calls == original_releases
    assert harness.backend.cancel_calls == original_cancels


def test_client_rename_grants_delete_and_key_cannot_be_resurrected(harness: Harness) -> None:
    headers = admin_headers(harness)
    client_url = f"/admin/api/clients/{harness.client_id}"
    detail = harness.api.get(client_url, headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "Test client"
    assert detail.json()["printer_ids"] == [harness.printer["id"]]

    updated = harness.api.put(
        client_url,
        json={"name": "Renamed integration", "printer_ids": [harness.printer["id"]]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed integration"
    assert updated.json()["printer_ids"] == [harness.printer["id"]]
    assert "api_key" not in updated.json()

    accepted = post_pdf(harness, harness.key, "deleted-client-tombstone")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    harness.appliance._set_status(job_id, "completed", "CUPS reports completed")
    key_hash_before = hash_api_key(harness.key)
    deleted = harness.api.delete(client_url, headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"id": harness.client_id, "deleted": True}
    assert harness.api.get(client_url, headers=headers).status_code == 404
    assert all(
        item["id"] != harness.client_id
        for item in harness.api.get("/admin/api/clients", headers=headers).json()
    )
    for method, body, suffix in (
        ("post", None, "/rotate-key"),
        ("post", None, "/revoke"),
        ("put", {"name": "Resurrected"}, ""),
        ("put", {"printer_ids": []}, "/printers"),
    ):
        response = getattr(harness.api, method)(
            client_url + suffix, json=body or {}, headers=headers
        )
        assert response.status_code == 404
    assert (
        harness.api.get(
            "/api/v1/printers", headers={"Authorization": f"Bearer {harness.key}"}
        ).status_code
        == 401
    )
    assert (
        harness.api.get(
            "/api/v1/jobs", headers={"Authorization": f"Bearer {harness.key}"}
        ).status_code
        == 401
    )
    history = harness.api.get(f"/admin/api/jobs/{job_id}", headers=headers)
    assert history.status_code == 200
    assert history.json()["status"] == "completed"

    db = harness.appliance.db()
    try:
        client = db.execute(
            "SELECT deleted_at,revoked,key_hash FROM clients WHERE id=?", (harness.client_id,)
        ).fetchone()
        assert client["deleted_at"] is not None
        assert client["revoked"] == 1
        assert client["key_hash"] == key_hash_before
        assert (
            db.execute(
                "SELECT COUNT(*) FROM client_printers WHERE client_id=?", (harness.client_id,)
            ).fetchone()[0]
            == 0
        )
        assert db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert db.execute("SELECT 1 FROM idempotency WHERE job_id=?", (job_id,)).fetchone()
    finally:
        db.close()


@pytest.mark.parametrize("target", ["printer", "client"])
def test_delete_is_atomic_with_job_admission(harness: Harness, target: str) -> None:
    barrier = threading.Barrier(2)
    result: dict[str, Any] = {}

    def accept() -> None:
        barrier.wait()
        try:
            result["accept"] = accept_job(harness, f"atomic-{target}")
        except PermissionError:
            result["accept"] = "rejected"

    def delete() -> None:
        barrier.wait()
        delete_resource = (
            harness.appliance.delete_printer
            if target == "printer"
            else harness.appliance.delete_client
        )
        try:
            result["delete"] = delete_resource(
                harness.printer["id"] if target == "printer" else harness.client_id
            )
        except ValueError:
            result["delete"] = "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_future = pool.submit(accept)
        delete_future = pool.submit(delete)
        accept_future.result()
        delete_future.result()

    db = harness.appliance.db()
    try:
        active_count = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN "
            "('queued','held','submitting','submitted','unknown')"
        ).fetchone()[0]
        if result["delete"] is True:
            assert result["accept"] == "rejected"
            assert active_count == 0
        else:
            assert result["delete"] == "blocked"
            assert result["accept"][1] == 202
            assert active_count == 1
        if target == "printer":
            deleted_at = db.execute(
                "SELECT deleted_at FROM printers WHERE id=?", (harness.printer["id"],)
            ).fetchone()[0]
        else:
            deleted_at = db.execute(
                "SELECT deleted_at FROM clients WHERE id=?", (harness.client_id,)
            ).fetchone()[0]
        assert (deleted_at is not None) == (result["delete"] is True)
    finally:
        db.close()


def test_packaged_admin_control_assets_are_served(harness):
    page = harness.api.get("/")
    assert page.status_code == 200
    assert "/assets/app.js" in page.text
    assert 'id="root"' in page.text
    for asset in ("app.js", "style.css", "THIRD_PARTY_LICENSES.txt"):
        response = harness.api.get(f"/assets/{asset}")
        assert response.status_code == 200
        assert response.content
    assert harness.api.get("/assets/config.py").status_code == 404


def test_client_test_page_public_but_api_stays_scoped(harness):
    page = harness.api.get("/client")
    assert page.status_code == 200
    assert "/assets/client.js" in page.text
    assert 'id="root"' in page.text
    assert harness.api.get("/assets/client.js").status_code == 200
    assert harness.api.get("/assets/controls.js").status_code == 404
    assert harness.api.get("/assets/print-options.js").status_code == 404
    assert harness.api.get("/api/v1/printers").status_code == 401
    assert harness.api.get("/api/v1/jobs").status_code == 401
    assert harness.api.get("/admin/api/printers").status_code == 401
    assert "no-store" in page.headers["cache-control"]
