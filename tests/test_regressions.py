from __future__ import annotations

import io
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from print_appliance import db as store
from print_appliance.cups import BackendUnavailable
from print_appliance.service import validate_device_uri, validate_options_config
from tests.test_coordinator import accept, make_client, printer


def test_resume_one_queued_job_while_printer_paused_does_not_open_rest(harness):
    first = accept(harness.appliance, harness.client_id, harness.printer["id"], "one")
    second = accept(harness.appliance, harness.client_id, harness.printer["id"], "two")
    harness.appliance.pause_printer(harness.printer["id"])
    harness.appliance.resume_job(first["job_id"])
    assert harness.appliance.admin_printers()[0]["paused"] is True
    harness.appliance.tick()
    assert len(harness.backend.submit_calls) == 1
    cups_id = harness.backend.correlations[f"pa-{first['job_id']}"]
    harness.backend.set_state(cups_id, "completed")
    harness.appliance.tick()
    harness.appliance.tick()
    assert harness.appliance.client_job(harness.client_id, first["job_id"])["status"] == "completed"
    assert harness.appliance.client_job(harness.client_id, second["job_id"])["status"] == "queued"
    assert len(harness.backend.submit_calls) == 1
    assert harness.printer["queue"] in harness.backend.disabled


def test_individual_resume_preserves_fifo(harness):
    accept(harness.appliance, harness.client_id, harness.printer["id"], "earlier")
    later = accept(harness.appliance, harness.client_id, harness.printer["id"], "later")
    harness.appliance.pause_printer(harness.printer["id"])
    with pytest.raises(ValueError, match="FIFO"):
        harness.appliance.resume_job(later["job_id"])
    assert harness.backend.release_calls == []


def test_unconfirmed_release_does_not_automatically_retry_release(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "release-uncertain")
    harness.backend.release_error = True
    harness.appliance.tick()
    harness.backend.release_error = False
    for _ in range(3):
        harness.appliance.tick()
    assert harness.backend.release_calls == []
    assert harness.appliance.admin_printers()[0]["paused"] is True
    harness.appliance.resume_job(job["job_id"])
    assert len(harness.backend.release_calls) == 1
    assert harness.appliance.admin_printers()[0]["paused"] is True


def test_terminal_state_is_tracked_even_when_printer_paused(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "done-paused")
    harness.appliance.tick()
    harness.appliance.pause_printer(harness.printer["id"])
    harness.backend.set_state(harness.backend.correlations[f"pa-{job['job_id']}"], "completed")
    harness.appliance.tick()
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "completed"
    assert not (harness.appliance.settings.spool_dir / f"{job['job_id']}.payload").exists()


def test_stopped_job_never_restarts_automatically(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "stopped")
    harness.appliance.tick()
    cups_id = harness.backend.correlations[f"pa-{job['job_id']}"]
    harness.backend.set_state(cups_id, "stopped")
    harness.appliance.tick()
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "unknown"
    before = list(harness.backend.release_calls)
    harness.appliance.tick()
    assert harness.backend.release_calls == before
    with pytest.raises(ValueError, match="unknown"):
        harness.appliance.resume_printer(harness.printer["id"])


def test_copies_and_document_format_reach_cups(harness):
    result, _ = harness.appliance.accept_job(
        client_id=harness.client_id,
        idempotency_key="copies",
        printer_id=harness.printer["id"],
        format_name="zpl",
        title="Label",
        copies_raw="3",
        options_raw="{}",
        filename="label.zpl",
        upload=SimpleNamespace(file=io.BytesIO(b"^XA^FDLabel^FS^XZ")),
    )
    harness.appliance.tick()
    assert harness.backend.submit_calls[0][3] == {
        "copies": "3",
        "document-format": "application/vnd.cups-raw",
    }
    assert harness.appliance.client_job(harness.client_id, result["job_id"])["copies"] == 3


def test_driver_change_without_uri_change_holds_snapshot(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "driver-snapshot")
    harness.backend.queue_data[harness.printer["queue"]]["printer-ppd-name"] = "changed-driver"
    harness.appliance.tick()
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "held"
    assert harness.backend.submit_calls == []


def test_handed_off_job_prevents_web_remapping(harness):
    accept(harness.appliance, harness.client_id, harness.printer["id"], "in-cups")
    harness.appliance.tick()
    with pytest.raises(ValueError, match="handed-off"):
        harness.appliance.edit_printer(
            harness.printer["id"], {"device_uri": "ipp://192.168.1.40/print"}
        )
    assert (
        harness.backend.queue_data[harness.printer["queue"]]["device-uri"]
        == harness.backend.device_uri
    )


def test_tampered_payload_is_not_sent(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "tampered")
    (harness.appliance.settings.spool_dir / f"{job['job_id']}.payload").write_bytes(
        b"%PDF-1.7\nchanged"
    )
    harness.appliance.tick()
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "failed"
    assert harness.backend.submit_calls == []


def test_cups_id_reuse_is_not_completion_or_permission_to_cancel(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "id-reused")
    harness.appliance.tick()
    cups_id = harness.backend.correlations[f"pa-{job['job_id']}"]
    harness.backend.jobs[cups_id].update(correlation="unrelated-job", state="completed")
    result = harness.appliance.cancel_client_job(harness.client_id, job["job_id"])
    assert result["status"] == "unknown"
    assert harness.backend.cancel_calls == []
    assert (harness.appliance.settings.spool_dir / f"{job['job_id']}.payload").exists()


def test_cancel_unknown_intent_reconciles_before_cancellation(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "lost-id")
    cups_id = harness.backend.submit_held(
        harness.printer["queue"], "unused", "title", {}, f"pa-{job['job_id']}"
    )
    harness.appliance._set_status(job["job_id"], "unknown", "Response lost")
    result = harness.appliance.cancel_client_job(harness.client_id, job["job_id"])
    assert result["status"] == "canceled"
    assert harness.backend.cancel_calls == [cups_id]


def test_cannot_resolve_unknown_while_cups_can_still_print(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "still-live")
    harness.backend.submit_held(
        harness.printer["queue"], "unused", "title", {}, f"pa-{job['job_id']}"
    )
    harness.appliance._set_status(job["job_id"], "unknown", "Lost response")
    with pytest.raises(ValueError, match="nonterminal"):
        harness.appliance.resolve_unknown(job["job_id"], "failed", "Operator checked printer")
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "unknown"


def test_cups_outage_does_not_claim_cancel(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "cancel-outage")
    harness.appliance.tick()
    harness.backend.available = False
    with pytest.raises(BackendUnavailable):
        harness.appliance.cancel_client_job(harness.client_id, job["job_id"])
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "submitted"


def test_one_blocked_cups_call_does_not_block_other_printer(harness):
    second = printer(harness.appliance, harness.backend, "Second independent printer")
    second_client = make_client(harness.appliance, second["id"], "second-client")
    accept(harness.appliance, harness.client_id, harness.printer["id"], "blocked-printer")
    accept(harness.appliance, second_client, second["id"], "independent-printer")
    second_submitted = threading.Event()
    original = harness.backend.submit_held

    def submit(queue, *args):
        if queue == harness.printer["queue"]:
            assert second_submitted.wait(3), "A blocked printer serialized the other printer"
        else:
            second_submitted.set()
        return original(queue, *args)

    harness.backend.submit_held = submit
    harness.appliance.tick()
    assert len(harness.backend.submit_calls) == 2


@pytest.mark.parametrize("key", ["copies", "raw", "document-format", "job-name", "job-hold-until"])
def test_transport_options_cannot_be_overridden_by_client_allowlists(key):
    with pytest.raises(ValueError, match="option"):
        validate_options_config({}, {key: ["unsafe"]})


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "socket://127.0.0.1:631",
        "ipp://169.254.169.254/",
        "ipp://user:password@192.168.1.20/",
        "ipp://8.8.8.8/",
        "usb://unknown/device",
    ],
)
def test_manual_printer_uri_rejects_unsafe_destinations(uri):
    with pytest.raises(ValueError):
        validate_device_uri(uri, set())


def test_manual_lan_uri_supported_without_discovery():
    validate_device_uri("ipp://192.168.1.20:631/ipp/print", set())
    validate_device_uri("socket://192.168.1.21:9100", set())


def test_pending_limit_is_atomic_under_concurrent_acceptance(harness):
    db = harness.appliance.db()
    store.save_setting(db, "max_pending_jobs", 1)
    db.close()
    barrier = threading.Barrier(4)

    def submit(index):
        barrier.wait()
        try:
            accept(harness.appliance, harness.client_id, harness.printer["id"], f"capacity-{index}")
            return "accepted"
        except OverflowError:
            return "full"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert results.count("accepted") == 1
    assert results.count("full") == 3


def test_expired_terminal_file_is_deleted_after_crash(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "crash-unlink")
    db = harness.appliance.db()
    db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job["job_id"],))
    db.close()
    payload = harness.appliance.settings.spool_dir / f"{job['job_id']}.payload"
    assert payload.exists()
    harness.appliance.recover_startup()
    assert not payload.exists()


def test_fifo_not_affected_by_wall_clock_rollback(harness):
    first = accept(harness.appliance, harness.client_id, harness.printer["id"], "clock-first")
    second = accept(harness.appliance, harness.client_id, harness.printer["id"], "clock-second")
    db = harness.appliance.db()
    db.execute(
        "UPDATE jobs SET accepted_at='2000-01-01T00:00:00+00:00' WHERE id=?", (second["job_id"],)
    )
    db.close()
    harness.appliance.tick()
    assert harness.backend.submit_calls[0][4] == f"pa-{first['job_id']}"


def test_import_never_changes_existing_queue_and_requires_safe_policy(harness):
    harness.backend.add_existing_queue("BROTHER_MFC")
    before = dict(harness.backend.queue_data["BROTHER_MFC"])
    imported = harness.appliance.import_queue(
        queue="BROTHER_MFC", name="Existing queue", formats=["pdf"]
    )
    assert imported["paused"] is True
    assert harness.backend.queue_data["BROTHER_MFC"] == before
    with pytest.raises(ValueError, match="stop-printer"):
        harness.appliance.resume_printer(imported["id"])
    assert harness.backend.queue_data["BROTHER_MFC"] == before


def test_changed_error_policy_cannot_auto_dispatch(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "unsafe-policy")
    harness.backend.queue_data[harness.printer["queue"]]["error-policy"] = "retry-job"
    harness.appliance.tick()
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "held"
    assert harness.backend.submit_calls == []


def test_consistent_backup_and_no_overwrite(harness, tmp_path):
    import sqlite3

    from print_appliance.cli import _backup

    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "backup-live-wal")
    destination = tmp_path / "backup.sqlite3"
    _backup(harness.appliance.settings, destination)
    with sqlite3.connect(destination) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0]
            == "queued"
        )
    assert destination.stat().st_mode & 0o777 == 0o640
    with pytest.raises(ValueError, match="differ"):
        _backup(harness.appliance.settings, harness.appliance.settings.database_path)
    with pytest.raises(FileExistsError):
        _backup(harness.appliance.settings, destination)


def test_chunked_request_limit_reports_413(harness):
    def body():
        for _ in range(14):
            yield b"x" * (1024 * 1024)

    response = harness.api.post(
        "/admin/api/login", content=body(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_configuration_mutations_are_audited_without_api_key(harness):
    from print_appliance.cli import _bootstrap

    password = secrets.token_urlsafe(24)
    _bootstrap(harness.appliance.settings, password)
    login = harness.api.post("/admin/api/login", json={"password": password})
    token = login.json()["csrf_token"]
    response = harness.api.post(
        "/admin/api/clients",
        json={"name": "Audited client", "printer_ids": []},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    key = response.json()["api_key"]
    audit = harness.api.get("/admin/api/audit")
    assert audit.status_code == 200
    assert any(item["target"] == "/admin/api/clients" for item in audit.json())
    assert key not in audit.text and password not in audit.text and token not in audit.text


def test_malformed_admin_fields_are_not_internal_errors(harness):
    from print_appliance.cli import _bootstrap

    password = secrets.token_urlsafe(24)
    _bootstrap(harness.appliance.settings, password)
    token = harness.api.post("/admin/api/login", json={"password": password}).json()["csrf_token"]
    headers = {"X-CSRF-Token": token}
    assert (
        harness.api.post(
            "/admin/api/clients", json={"name": "Invalid", "printer_ids": [{}]}, headers=headers
        ).status_code
        == 422
    )
    assert (
        harness.api.post(
            "/admin/api/printers", json={"name": [], "formats": ["pdf"]}, headers=headers
        ).status_code
        == 422
    )
    assert (
        harness.api.post(
            "/admin/api/jobs/missing/resolve", json={"outcome": [], "reason": None}, headers=headers
        ).status_code
        == 422
    )


def test_manual_pause_revokes_previous_single_job_permission(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "pause-revokes")
    harness.appliance.resume_job(job["job_id"])
    harness.appliance.pause_printer(harness.printer["id"])
    harness.appliance.tick()
    assert harness.backend.submit_calls == []


def test_new_offline_error_revokes_single_job_resume_permission(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "new-offline")
    harness.appliance.resume_job(job["job_id"])
    queue = harness.printer["queue"]

    def pause_fails(queue):
        raise BackendUnavailable("Pause response lost")

    harness.backend.pause_printer = pause_fails
    harness.backend.online[queue] = False
    harness.appliance.tick()
    harness.backend.online[queue] = True
    harness.appliance.tick()
    assert harness.backend.submit_calls == []
    assert harness.appliance.admin_printers()[0]["paused"] is True


def test_unknown_resolution_requires_matching_cups_identity(harness):
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "resolution-identity")
    harness.appliance.tick()
    cups_id = harness.backend.correlations[f"pa-{job['job_id']}"]
    harness.backend.set_state(cups_id, "completed")
    harness.appliance._set_status(job["job_id"], "unknown", "test uncertain identity")
    harness.backend.jobs[cups_id]["correlation"] = "pa-unrelated"
    with pytest.raises(ValueError, match="identity"):
        harness.appliance.resolve_unknown(job["job_id"], "completed", "CUPS reports completed")
    assert harness.appliance.client_job(harness.client_id, job["job_id"])["status"] == "unknown"
    harness.backend.jobs[cups_id]["correlation"] = f"pa-{job['job_id']}"
    assert (
        harness.appliance.resolve_unknown(
            job["job_id"], "completed", "CUPS identity and completed verified"
        )["status"]
        == "completed"
    )
    assert len(harness.backend.submit_calls) == 1


def test_request_digest_stays_compatible_with_pre_capability_release(harness):
    import hashlib
    import json

    fields = {
        "printer_id": "legacy-printer",
        "format": "pdf",
        "title": "old job",
        "copies": "1",
        "options": {"Duplex": "None"},
        "filename": "old.pdf",
    }
    expected = hashlib.sha256(
        json.dumps(
            {"fields": fields, "content_sha256": "abc123"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert (
        harness.appliance._request_digest(
            printer_id=fields["printer_id"],
            format_name=fields["format"],
            title=fields["title"],
            copies_raw=fields["copies"],
            options_raw='{"Duplex":"None"}',
            filename=fields["filename"],
            content_hash="abc123",
        )
        == expected
    )
