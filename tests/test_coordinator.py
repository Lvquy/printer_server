from __future__ import annotations

import io
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from print_appliance.app import WorkerLock
from print_appliance.config import Settings
from print_appliance.service import Appliance
from tests.fakes import FakeCups

PDF = b"%PDF-1.4\ncoordinator test\n"


def accept(appliance: Appliance, client_id: str, printer_id: str, key: str) -> dict[str, Any]:
    result, code = appliance.accept_job(
        client_id=client_id,
        idempotency_key=key,
        printer_id=printer_id,
        format_name="pdf",
        title=key,
        copies_raw="1",
        options_raw="{}",
        filename="job.pdf",
        upload=SimpleNamespace(file=io.BytesIO(PDF), filename="job.pdf"),
    )
    assert code == 202
    return result


def make_client(appliance: Appliance, printer_id: str, suffix: str) -> str:
    client_id = str(uuid.uuid4())
    db = appliance.db()
    try:
        db.execute(
            "INSERT INTO clients(id,name,key_hash,created_at) VALUES(?,?,?,datetime('now'))",
            (client_id, f"Client {suffix}", suffix),
        )
        db.execute(
            "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)", (client_id, printer_id)
        )
    finally:
        db.close()
    return client_id


def printer(appliance: Appliance, backend: FakeCups, name: str) -> dict[str, Any]:
    return appliance.create_managed_printer(
        name=name,
        device_uri=backend.device_uri,
        driver=backend.driver,
        formats=["pdf"],
        defaults={},
        allowed={},
    )


def test_fifo_per_printer_and_independent_printer_progress(tmp_path: Path) -> None:
    backend = FakeCups()
    appliance = Appliance(Settings(tmp_path / "data"), backend)
    one, two = (
        printer(appliance, backend, "Printer one"),
        printer(appliance, backend, "Printer two"),
    )
    client = make_client(appliance, one["id"], "client1")
    db = appliance.db()
    try:
        db.execute(
            "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)", (client, two["id"])
        )
    finally:
        db.close()
    first = accept(appliance, client, one["id"], "first")
    second = accept(appliance, client, one["id"], "second")
    parallel = accept(appliance, client, two["id"], "parallel")

    appliance.tick()
    assert len(backend.submit_calls) == 2
    by_printer = {call[0] for call in backend.submit_calls}
    assert by_printer == {one["queue"], two["queue"]}
    db = appliance.db()
    try:
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0]
            == "submitted"
        )
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (second["job_id"],)).fetchone()[0]
            == "queued"
        )
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (parallel["job_id"],)).fetchone()[0]
            == "submitted"
        )
    finally:
        db.close()

    first_cups_id = backend.correlations[f"pa-{first['job_id']}"]
    parallel_cups_id = backend.correlations[f"pa-{parallel['job_id']}"]
    backend.set_state(first_cups_id, "completed")
    backend.set_state(parallel_cups_id, "completed")
    appliance.tick()  # Observe terminal states; the next worker pass advances the FIFO.
    appliance.tick()
    assert len(backend.submit_calls) == 3
    assert backend.submit_calls[-1][0] == one["queue"]


def test_snapshot_mapping_change_holds_job_and_prevents_silent_reroute(harness) -> None:
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "snapshot")
    queue = harness.printer["queue"]
    harness.backend.queue_data[queue]["device-uri"] = "ipp://changed.invalid/print"
    harness.appliance.tick()
    db = harness.appliance.db()
    try:
        row = db.execute("SELECT status,reason FROM jobs WHERE id=?", (job["job_id"],)).fetchone()
        assert row["status"] == "held"
        assert "snapshot" in row["reason"]
    finally:
        db.close()
    assert harness.backend.submit_calls == []


def test_offline_latches_until_manual_full_printer_resume(harness) -> None:
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "offline")
    queue = harness.printer["queue"]
    harness.backend.online[queue] = False
    harness.appliance.tick()
    current = harness.appliance.admin_printers()[0]
    assert current["paused"] is True
    assert queue in harness.backend.disabled
    harness.backend.online[queue] = True
    harness.appliance.tick()
    assert harness.backend.submit_calls == []  # Reconnection does not clear the latch.
    harness.appliance.resume_printer(harness.printer["id"])
    assert harness.appliance.admin_printers()[0]["paused"] is False
    harness.appliance.tick()
    assert len(harness.backend.submit_calls) == 1
    assert harness.backend.submit_calls[0][4] == f"pa-{job['job_id']}"


def test_individual_resume_releases_only_the_selected_held_cups_job(harness) -> None:
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "one-held")
    harness.backend.release_error = True
    harness.appliance.tick()
    job_id = harness.backend.correlations[f"pa-{job['job_id']}"]
    assert harness.backend.jobs[job_id]["state"] == "held"
    assert harness.backend.release_calls == []
    harness.backend.release_error = False
    result = harness.appliance.resume_job(job["job_id"])
    assert result["status"] == "submitted"
    assert harness.backend.release_calls == [job_id]


def test_pre_handoff_cancel_is_definite_and_post_handoff_requires_cups_evidence(harness) -> None:
    queued = accept(harness.appliance, harness.client_id, harness.printer["id"], "cancel-queued")
    canceled = harness.appliance.cancel_client_job(harness.client_id, queued["job_id"])
    assert canceled["status"] == "canceled"
    assert not (harness.appliance.settings.spool_dir / f"{queued['job_id']}.payload").exists()

    submitted = accept(
        harness.appliance, harness.client_id, harness.printer["id"], "cancel-submitted"
    )
    harness.appliance.tick()
    cups_id = harness.backend.correlations[f"pa-{submitted['job_id']}"]
    harness.backend.cancel_confirms = False
    pending = harness.appliance.cancel_client_job(harness.client_id, submitted["job_id"])
    assert pending["status"] == "submitted"
    assert "Cancellation is best-effort" in pending["reason"]
    harness.backend.set_state(cups_id, "canceled")
    confirmed = harness.appliance.cancel_client_job(harness.client_id, submitted["job_id"])
    assert confirmed["status"] == "canceled"


def test_unknown_handoff_is_never_resubmitted(harness) -> None:
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "unknown-submit")
    harness.backend.submit_error = True
    harness.appliance.tick()
    assert len(harness.backend.submit_calls) == 0
    db = harness.appliance.db()
    try:
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0]
            == "unknown"
        )
    finally:
        db.close()
    harness.backend.submit_error = False
    harness.appliance.tick()
    assert harness.backend.submit_calls == []


def test_restart_reconciles_found_correlation_and_marks_missing_history_unknown(harness) -> None:
    found = accept(harness.appliance, harness.client_id, harness.printer["id"], "found")
    correlation = f"pa-{found['job_id']}"
    cups_id = harness.backend.submit_held(
        harness.printer["queue"], "unused", "title", {}, correlation
    )
    db = harness.appliance.db()
    try:
        db.execute("UPDATE jobs SET status='submitting' WHERE id=?", (found["job_id"],))
        db.execute("UPDATE idempotency SET status='submitting' WHERE job_id=?", (found["job_id"],))
    finally:
        db.close()
    missing = accept(harness.appliance, harness.client_id, harness.printer["id"], "missing")
    db = harness.appliance.db()
    try:
        db.execute("UPDATE jobs SET status='submitting' WHERE id=?", (missing["job_id"],))
    finally:
        db.close()
    harness.appliance.recover_startup()
    db = harness.appliance.db()
    try:
        statuses = {
            row["id"]: row["status"]
            for row in db.execute(
                "SELECT id,status FROM jobs WHERE id IN (?,?)", (found["job_id"], missing["job_id"])
            )
        }
        found_row = db.execute(
            "SELECT cups_job_id FROM jobs WHERE id=?", (found["job_id"],)
        ).fetchone()
    finally:
        db.close()
    assert statuses[found["job_id"]] == "submitted"
    assert found_row["cups_job_id"] == cups_id
    assert statuses[missing["job_id"]] == "unknown"
    before = len(harness.backend.submit_calls)
    harness.appliance.tick()
    assert (
        len(harness.backend.submit_calls) == before
    )  # Found held job is never recreated or automatically released.
    assert harness.backend.release_calls == []
    harness.appliance.resolve_unknown(missing["job_id"], "failed", "Operator verified no output")
    harness.appliance.resume_job(found["job_id"])
    assert harness.backend.release_calls == [cups_id]


def test_submitted_job_missing_cups_history_becomes_unknown_on_restart(harness) -> None:
    job = accept(harness.appliance, harness.client_id, harness.printer["id"], "history-gone")
    db = harness.appliance.db()
    try:
        db.execute(
            "UPDATE jobs SET status='submitted',cups_job_id=999 WHERE id=?", (job["job_id"],)
        )
    finally:
        db.close()
    harness.appliance.recover_startup()
    db = harness.appliance.db()
    try:
        assert (
            db.execute("SELECT status FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0]
            == "unknown"
        )
    finally:
        db.close()


def test_worker_lock_rejects_second_process(tmp_path: Path) -> None:
    path = tmp_path / "data" / "worker.lock"
    first, second = WorkerLock(path), WorkerLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another print-appliance worker"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
