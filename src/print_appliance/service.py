from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from . import db as store
from .config import Settings
from .cups import BackendUnavailable, CupsBackend
from .print_options import InvalidPrintOption, public_capabilities, translate_options

LOG = logging.getLogger("print_appliance")
TERMINAL = {"completed", "failed", "canceled"}
ACTIVE = {"queued", "held", "submitting", "submitted", "unknown"}
KEY_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
SAFE_QUEUE_PREFIX = "pa_"
ALLOWED_SCHEMES = {"usb", "ipp", "ipps", "socket", "lpd", "dnssd"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def safe_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def validate_device_uri(uri: str, discovered: set[str]) -> None:
    if not isinstance(uri, str) or len(uri) > 2048 or any(ord(c) < 32 for c in uri):
        raise ValueError("Invalid device URI")
    parts = urlsplit(uri)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError("Unsupported printer protocol")
    if uri not in discovered:
        if parts.scheme not in {"ipp", "ipps", "socket", "lpd"} or not parts.hostname:
            raise ValueError(
                "USB/DNS-SD devices must be discovered by CUPS; network printers need a valid host"
            )
        import ipaddress

        if parts.hostname.lower() == "localhost":
            raise ValueError("Printer target cannot be localhost")
        try:
            address = ipaddress.ip_address(parts.hostname)
        except ValueError:
            address = None
        if address and (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_global
        ):
            raise ValueError("Manual printer target must be a local network address")
        try:
            _ = parts.port
        except ValueError as exc:
            raise ValueError("Invalid printer port") from exc
    if parts.username or parts.password or parts.fragment or "\x00" in uri:
        raise ValueError("Unsupported device URI")


def validate_formats(formats: Any) -> list[str]:
    if (
        not isinstance(formats, list)
        or not formats
        or any(not isinstance(x, str) or x not in {"pdf", "zpl"} for x in formats)
    ):
        raise ValueError("formats must contain pdf and/or zpl")
    return sorted(set(formats))


def validate_options_config(
    defaults: Any, allowed: Any
) -> tuple[dict[str, str], dict[str, list[str]]]:
    if not isinstance(allowed, dict) or not isinstance(defaults, dict):
        raise ValueError("Printer options must be JSON objects")
    if len(allowed) > 32 or len(defaults) > 32:
        raise ValueError("Too many printer options")
    reserved = {"copies", "raw", "document-format", "job-name", "job-hold-until"}
    normalized: dict[str, list[str]] = {}
    for key, values in allowed.items():
        if (
            not isinstance(key, str)
            or key.lower() in reserved
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key)
        ):
            raise ValueError("Invalid printer option name")
        if (
            not isinstance(values, list)
            or not values
            or len(values) > 64
            or any(
                not isinstance(value, str)
                or not value
                or len(value) > 128
                or any(ord(c) < 32 for c in value)
                for value in values
            )
        ):
            raise ValueError("Allowed option values must be non-empty string arrays")
        normalized[key] = list(dict.fromkeys(values))
    normalized_defaults: dict[str, str] = {}
    for key, value in defaults.items():
        if key not in normalized or not isinstance(value, str) or value not in normalized[key]:
            raise ValueError("Default printer options must be selected from their allowlist")
        normalized_defaults[key] = value
    return normalized_defaults, normalized


def validate_client_options(raw: Any, allowed: dict[str, list[str]]) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("options must be a JSON object")
    values: dict[str, str] = {}
    for key, value in raw.items():
        if key not in allowed or not isinstance(value, str) or value not in allowed[key]:
            raise ValueError("Unsupported printer option")
        values[key] = value
    return values


class Appliance:
    def __init__(self, settings: Settings, backend: CupsBackend):
        self.settings = settings
        self.backend = backend
        self.lock = threading.RLock()
        self._printer_locks: dict[str, Any] = {}
        self.settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.settings.spool_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chmod(self.settings.data_dir, 0o750)
        os.chmod(self.settings.spool_dir, 0o750)
        store.initialize(settings.database_path)

    def _get_job_row(self, job_id: str) -> sqlite3.Row | None:
        db = self.db()
        try:
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            db.close()

    def printer_lock(self, printer_id: str) -> Any:
        with self.lock:
            return self._printer_locks.setdefault(printer_id, threading.RLock())

    def job_lock(self, job_id: str) -> Any:
        db = self.db()
        try:
            row = db.execute("SELECT printer_id FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self.printer_lock(row[0]) if row else self.lock
        finally:
            db.close()

    def db(self) -> sqlite3.Connection:
        return store.connect(self.settings.database_path)

    def limits(self, db: sqlite3.Connection | None = None) -> dict[str, int]:
        owned = db is None
        connection = db or self.db()
        try:
            return store.get_limits(connection)
        finally:
            if owned:
                connection.close()

    @staticmethod
    def _event(db: sqlite3.Connection, job_id: str, actor: str, status: str, message: str) -> None:
        db.execute(
            "INSERT INTO events(job_id,at,actor,status,message) VALUES(?,?,?,?,?)",
            (job_id, now_iso(), actor, status, message[:500]),
        )

    def _job_record(self, row: sqlite3.Row, include_events: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": row["id"],
            "printer_id": row["printer_id"],
            "status": row["status"],
            "accepted_at": row["accepted_at"],
            "updated_at": row["updated_at"],
            "title": row["title"],
            "format": row["format"],
            "copies": row["copies"],
            "reason": row["reason"],
        }
        if include_events:
            db = self.db()
            try:
                result["events"] = [
                    dict(event)
                    for event in db.execute(
                        "SELECT at,actor,status,message FROM events WHERE job_id=? ORDER BY id",
                        (row["id"],),
                    )
                ]
            finally:
                db.close()
        return result

    def client_for_key(self, key: str) -> sqlite3.Row | None:
        db = self.db()
        try:
            return db.execute(
                "SELECT id,name FROM clients WHERE key_hash=? AND revoked=0 AND deleted_at IS NULL",
                (hash_api_key(key),),
            ).fetchone()
        finally:
            db.close()

    def list_client_printers(self, client_id: str) -> list[dict[str, Any]]:
        db = self.db()
        try:
            rows = db.execute(
                "SELECT p.* FROM printers p JOIN client_printers cp ON cp.printer_id=p.id "
                "JOIN clients c ON c.id=cp.client_id "
                "WHERE cp.client_id=? AND p.deleted_at IS NULL AND c.deleted_at IS NULL "
                "ORDER BY p.name",
                (client_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "formats": safe_json(row["formats_json"], []),
                    "capabilities": public_capabilities(
                        self.printer_capabilities(row["id"]) or {},
                        safe_json(row["defaults_json"], {}),
                        safe_json(row["formats_json"], []),
                    )[0],
                    "status": "paused" if row["paused"] else "ready",
                    "pause_reason": row["pause_reason"],
                }
                for row in rows
            ]
        finally:
            db.close()

    def _request_digest(
        self,
        *,
        printer_id: str,
        format_name: str,
        title: str,
        copies_raw: str,
        options_raw: str,
        filename: str,
        content_hash: str,
    ) -> str:
        canonical = json_dump(
            {
                "fields": {
                    "printer_id": printer_id,
                    "format": format_name,
                    "title": title,
                    "copies": copies_raw,
                    "options": safe_json(options_raw, options_raw),
                    "filename": filename,
                },
                "content_sha256": content_hash,
            }
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _existing_idempotency(
        self, db: sqlite3.Connection, client_id: str, key: str, digest: str
    ) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT digest,job_id,status,history_expired FROM idempotency "
            "WHERE client_id=? AND request_key=?",
            (client_id, key),
        ).fetchone()
        if row is None:
            return None
        if row["digest"] != digest:
            raise ValueError("idempotency_conflict")
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "deduplicated": True,
            "history_expired": bool(row["history_expired"]),
        }

    def accept_job(
        self,
        *,
        client_id: str,
        idempotency_key: str,
        printer_id: str,
        format_name: str,
        title: str,
        copies_raw: str,
        options_raw: str,
        filename: str,
        upload: Any,
        legacy_options: bool = False,
    ) -> tuple[dict[str, Any], int]:
        if not KEY_RE.fullmatch(idempotency_key):
            raise ValueError("invalid_idempotency_key")
        temp_path = self.settings.spool_dir / f"upload_{secrets.token_hex(16)}.part"
        content_hash = hashlib.sha256()
        size = 0
        db = self.db()
        try:
            max_bytes = self.limits(db)["max_upload_bytes"]
            if shutil.disk_usage(self.settings.spool_dir).free < self.limits(db)["min_free_bytes"]:
                raise OSError("insufficient_disk_space")
            with temp_path.open("xb") as target:
                os.chmod(temp_path, 0o640)
                while True:
                    chunk = upload.file.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise OverflowError("file_too_large")
                    if (
                        shutil.disk_usage(self.settings.spool_dir).free - len(chunk)
                        < self.limits(db)["min_free_bytes"]
                    ):
                        raise OSError("insufficient_disk_space")
                    content_hash.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            digest_content = content_hash.hexdigest()
            request_digest = self._request_digest(
                printer_id=printer_id,
                format_name=format_name,
                title=title,
                copies_raw=copies_raw,
                options_raw=options_raw,
                filename=filename,
                content_hash=digest_content,
            )
            existing = self._existing_idempotency(db, client_id, idempotency_key, request_digest)
            if existing is not None:
                return existing, 200
            if legacy_options:
                raise InvalidPrintOption("options", None, [], "Legacy options removed. Use paper_size, sides, color_mode and scaling for new requests.")

            try:
                parsed_options = json.loads(options_raw)
                copies = int(copies_raw)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid_form_fields") from exc
            if format_name not in {"pdf", "zpl"} or len(title.strip()) > 128 or not title.strip():
                raise ValueError("invalid_form_fields")
            if any(ord(char) < 32 and char not in "\t" for char in title):
                raise ValueError("invalid_form_fields")
            if not 1 <= copies <= 100:
                raise ValueError("invalid_form_fields")
            if format_name == "pdf":
                with temp_path.open("rb") as source:
                    if source.read(5) != b"%PDF-":
                        raise ValueError("invalid_format_signature")
            else:
                try:
                    raw = temp_path.read_bytes()
                    zpl = raw.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise ValueError("invalid_format_signature") from exc
                if not zpl.startswith("^XA") or not zpl.endswith("^XZ") or b"\x00" in raw:
                    raise ValueError("invalid_format_signature")
            if not isinstance(parsed_options, dict):
                raise ValueError("invalid_options")
            printer = db.execute(
                "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
            ).fetchone()
            grant = db.execute(
                "SELECT 1 FROM client_printers WHERE client_id=? AND printer_id=?",
                (client_id, printer_id),
            ).fetchone()
            if printer is None or grant is None:
                raise PermissionError("printer_not_granted")
            formats = safe_json(printer["formats_json"], [])
            if format_name not in formats:
                raise ValueError("format_not_supported")
            allowed = safe_json(printer["allowed_json"], {})
            defaults = safe_json(printer["defaults_json"], {})
            schema = self.backend.printer_capabilities(printer["queue"])
            mapping = self.backend.queue_mapping(printer["queue"])
            if not mapping or mapping["mapping_signature"] != printer["mapping_signature"]:
                raise BackendUnavailable("Printer mapping changed; review configuration")
            if schema.get("availability") not in {"available", "partial"}:
                raise BackendUnavailable("Printer capabilities unavailable")
            options = translate_options(parsed_options, schema, defaults, format_name)
            driver_defaults = (
                {
                    item["name"]: item["default"]
                    for item in schema.get("options", [])
                    if item.get("default")
                    in {choice["value"] for choice in item.get("choices", [])}
                }
                if format_name == "pdf"
                else {}
            )
            effective_options = dict(driver_defaults)
            effective_options.update(defaults)
            effective_options.update(options)
            if format_name == "zpl":
                if options:
                    raise ValueError("unsupported_option_choice")
                # Raw ZPL carries its own device commands, never PDF driver defaults.
                effective_options = {}
            try:
                self._validate_capability_options(schema, effective_options, allowed)
            except ValueError as exc:
                raise InvalidPrintOption(
                    "combination", parsed_options, [],
                    "Options conflict with one another or printer defaults; change the requested combination.",
                ) from exc
            if (
                shutil.disk_usage(self.settings.spool_dir).free - size
                < self.limits(db)["min_free_bytes"]
            ):
                raise OSError("insufficient_disk_space")
            pending = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','held','submitting','submitted','unknown')"
            ).fetchone()[0]
            if pending >= self.limits(db)["max_pending_jobs"]:
                raise OverflowError("queue_full")

            job_id = str(uuid.uuid4())
            spool_name = f"{job_id}.payload"
            target_path = self.settings.spool_dir / spool_name
            snapshot = {
                "queue": printer["queue"],
                "device_uri": printer["device_uri"],
                "driver": printer["driver"],
                "mapping_signature": printer["mapping_signature"],
                "format": format_name,
                "options": effective_options,
            }
            os.replace(temp_path, target_path)
            os.chmod(target_path, 0o640)
            directory_fd = os.open(self.settings.spool_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            try:
                db.execute("BEGIN IMMEDIATE")
                raced = self._existing_idempotency(db, client_id, idempotency_key, request_digest)
                if raced is not None:
                    db.execute("ROLLBACK")
                    target_path.unlink(missing_ok=True)
                    return raced, 200
                if (
                    not db.execute(
                        "SELECT 1 FROM clients WHERE id=? AND revoked=0 AND deleted_at IS NULL",
                        (client_id,),
                    ).fetchone()
                    or not db.execute(
                        "SELECT 1 FROM client_printers cp JOIN printers p ON p.id=cp.printer_id "
                        "WHERE cp.client_id=? AND cp.printer_id=? AND p.deleted_at IS NULL",
                        (client_id, printer_id),
                    ).fetchone()
                ):
                    raise PermissionError("printer_not_granted")
                # Re-check admission under the writer lock to close concurrent acceptance races.
                pending = db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','held','submitting','submitted','unknown')"
                ).fetchone()[0]
                if pending >= self.limits(db)["max_pending_jobs"]:
                    raise OverflowError("queue_full")
                accepted = now_iso()
                correlation = f"pa-{job_id}"
                sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM jobs").fetchone()[0]
                db.execute(
                    "INSERT INTO jobs(id,client_id,printer_id,status,accepted_at,updated_at,title,format,copies,"
                    "options_json,content_hash,spool_name,snapshot_json,correlation,sequence) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        client_id,
                        printer_id,
                        "queued",
                        accepted,
                        accepted,
                        title.strip(),
                        format_name,
                        copies,
                        json_dump(effective_options),
                        digest_content,
                        spool_name,
                        json_dump(snapshot),
                        correlation,
                        sequence,
                    ),
                )
                db.execute(
                    "INSERT INTO idempotency(client_id,request_key,digest,job_id,status,accepted_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (client_id, idempotency_key, request_digest, job_id, "queued", accepted),
                )
                self._event(db, job_id, "client", "queued", "Durably accepted")
                db.execute("COMMIT")
                return {"job_id": job_id, "status": "queued", "deduplicated": False}, 202
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                target_path.unlink(missing_ok=True)
                raise
        finally:
            temp_path.unlink(missing_ok=True)
            db.close()

    @staticmethod
    def _validate_capability_options(
        schema: dict[str, Any], effective: dict[str, str], allowed: dict[str, list[str]]
    ) -> None:
        options = {item["name"]: item for item in schema.get("options", [])}
        for name, value in effective.items():
            descriptor = options.get(name)
            if descriptor and value not in {choice["value"] for choice in descriptor["choices"]}:
                raise ValueError("unsupported_option_choice")
        for constraint in schema.get("constraints", []):
            left, right = constraint["option1"], constraint["option2"]
            if (
                effective.get(left) == constraint["choice1"]
                and effective.get(right) == constraint["choice2"]
            ):
                raise ValueError("incompatible_options")

    def printer_capabilities(
        self, printer_id: str, client_id: str | None = None
    ) -> dict[str, Any] | None:
        db = self.db()
        try:
            row = db.execute(
                "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
            ).fetchone()
            if row is None:
                return None
            if (
                client_id is not None
                and not db.execute(
                    "SELECT 1 FROM client_printers WHERE client_id=? AND printer_id=?",
                    (client_id, printer_id),
                ).fetchone()
            ):
                return None
        finally:
            db.close()
        try:
            mapping = self.backend.queue_mapping(row["queue"])
            if mapping is None:
                raise BackendUnavailable("Registered queue is missing")
            if mapping["mapping_signature"] != row["mapping_signature"]:
                return {
                    "schema_version": 1,
                    "printer_id": printer_id,
                    "availability": "stale",
                    "reason": "CUPS mapping changed; review printer configuration.",
                    "mapping_fingerprint": mapping["mapping_signature"],
                    "source": "unknown",
                    "groups": [
                        {"id": "common", "label": "Common"},
                        {"id": "advanced", "label": "Advanced"},
                    ],
                    "schema_fingerprint": None,
                    "ipp_attributes": {},
                    "options": [],
                    "constraints": [],
                    "default_options": safe_json(row["defaults_json"], {}),
                    "allowed_options": safe_json(row["allowed_json"], {})
                    if client_id is None
                    else None,
                }
            schema = self.backend.printer_capabilities(row["queue"])
        except BackendUnavailable:
            return {
                "schema_version": 1,
                "printer_id": printer_id,
                "availability": "unavailable",
                "reason": "Local CUPS capabilities could not be queried.",
                "mapping_fingerprint": None,
                "schema_fingerprint": None,
                "source": "unknown",
                "groups": [],
                "options": [],
                "constraints": [],
                "ipp_attributes": {},
                "default_options": safe_json(row["defaults_json"], {}),
                "allowed_options": safe_json(row["allowed_json"], {})
                if client_id is None
                else None,
            }
        allowed = safe_json(row["allowed_json"], {})
        defaults = {
            item["name"]: item["default"]
            for item in schema.get("options", [])
            if item.get("default") in {choice["value"] for choice in item.get("choices", [])}
        }
        defaults.update(safe_json(row["defaults_json"], {}))
        descriptors = schema.get("options", [])
        if client_id is not None:
            descriptors = [
                {
                    **item,
                    "choices": [
                        c for c in item["choices"] if c["value"] in allowed.get(item["name"], [])
                    ],
                    "overridable": item["name"] in allowed,
                }
                for item in descriptors
            ]
            descriptors = [item for item in descriptors if item["overridable"]]
        return {
            "schema_version": 1,
            "printer_id": printer_id,
            "availability": schema.get("availability", "unknown"),
            "reason": schema.get("reason"),
            "mapping_fingerprint": mapping["mapping_signature"],
            "schema_fingerprint": schema.get("mapping_fingerprint"),
            "source": schema.get("source"),
            "raw": bool(schema.get("raw")),
            "groups": [
                {"id": "common", "label": "Common"},
                {"id": "advanced", "label": "Advanced"},
            ],
            "options": descriptors,
            "constraints": schema.get("constraints", []),
            "ipp_attributes": schema.get("ipp_attributes", {}),
            "default_options": defaults,
            "allowed_options": allowed if client_id is None else None,
        }

    def client_jobs(self, client_id: str, limit: int) -> list[dict[str, Any]]:
        db = self.db()
        try:
            rows = db.execute(
                "SELECT * FROM jobs WHERE client_id=? ORDER BY sequence DESC LIMIT ?",
                (client_id, max(1, min(limit, 100))),
            ).fetchall()
            return [self._job_record(row) for row in rows]
        finally:
            db.close()

    def client_job(self, client_id: str, job_id: str) -> dict[str, Any] | None:
        db = self.db()
        try:
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND client_id=?", (job_id, client_id)
            ).fetchone()
            if row is None:
                return None
            return self._job_record(row, include_events=True)
        finally:
            db.close()

    def _set_status(
        self,
        job_id: str,
        status: str,
        message: str,
        *,
        reason: str | None = None,
        actor: str = "worker",
    ) -> None:
        db = self.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET status=?,updated_at=?,reason=? WHERE id=?",
                (status, now_iso(), reason, job_id),
            )
            db.execute("UPDATE idempotency SET status=? WHERE job_id=?", (status, job_id))
            self._event(db, job_id, actor, status, message)
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()
        if status in TERMINAL:
            self._unlink_job_payload(job_id)

    def _unlink_job_payload(self, job_id: str) -> None:
        db = self.db()
        try:
            row = db.execute("SELECT spool_name FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                (self.settings.spool_dir / row["spool_name"]).unlink(missing_ok=True)
        finally:
            db.close()

    def cancel_client_job(self, client_id: str, job_id: str) -> dict[str, Any] | None:
        with self.job_lock(job_id):
            db = self.db()
            try:
                row = db.execute(
                    "SELECT * FROM jobs WHERE id=? AND client_id=?", (job_id, client_id)
                ).fetchone()
                if row is None:
                    return None
                if row["status"] in TERMINAL:
                    return self._job_record(row)
                if row["status"] in {"queued", "held"}:
                    self._set_status(
                        job_id, "canceled", "Canceled before CUPS handoff", actor="client"
                    )
                    return self.client_job(client_id, job_id)
                if not row["cups_job_id"]:
                    found = self.backend.find_job(row["correlation"])
                    if not found:
                        self._set_status(
                            job_id,
                            "unknown",
                            "Handoff has no reconcilable CUPS ID; operator review required",
                        )
                        return self.client_job(client_id, job_id)
                    db.execute(
                        "UPDATE jobs SET cups_job_id=? WHERE id=?", (int(found["id"]), job_id)
                    )
                    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                job = self.backend.get_job(int(row["cups_job_id"]))
                if job and job.get("correlation") != row["correlation"]:
                    self._set_status(
                        job_id, "unknown", "CUPS job identity differs; cancellation refused"
                    )
                    return self.client_job(client_id, job_id)
                if job and job["state"] == "completed":
                    self._set_status(job_id, "completed", "CUPS confirms completion")
                elif job and job["state"] == "canceled":
                    self._set_status(
                        job_id, "canceled", "CUPS confirms cancellation", actor="client"
                    )
                elif job and job["state"] == "aborted":
                    self._set_status(job_id, "failed", "CUPS confirms the job aborted")
                elif job:
                    try:
                        self.backend.cancel_job(int(row["cups_job_id"]))
                        confirmation = self.backend.get_job(int(row["cups_job_id"]))
                    except BackendUnavailable:
                        confirmation = None
                    if confirmation and confirmation["state"] == "canceled":
                        self._set_status(
                            job_id, "canceled", "CUPS confirms cancellation", actor="client"
                        )
                    elif confirmation and confirmation["state"] == "completed":
                        self._set_status(job_id, "completed", "CUPS confirms completion")
                    elif confirmation and confirmation["state"] == "aborted":
                        self._set_status(job_id, "failed", "CUPS confirms the job aborted")
                    else:
                        self._set_status(
                            job_id,
                            "submitted",
                            "Cancellation requested; CUPS has not confirmed a terminal state",
                            reason="Cancellation is best-effort; poll for CUPS evidence",
                            actor="client",
                        )
                else:
                    self._set_status(job_id, "unknown", "CUPS history no longer proves the outcome")
                return self.client_job(client_id, job_id)
            finally:
                db.close()

    def recover_startup(self) -> None:
        """Reconcile handoff intents. Missing CUPS history always becomes unknown, never resubmitted."""
        with self.lock:
            db = self.db()
            try:
                db.execute(
                    "DELETE FROM sessions WHERE expires_at < ?", (datetime.now(UTC).timestamp(),)
                )
                intents = db.execute("SELECT * FROM jobs WHERE status='submitting'").fetchall()
                submitted = db.execute("SELECT * FROM jobs WHERE status='submitted'").fetchall()
            finally:
                db.close()
            for row in intents:
                try:
                    found = self.backend.find_job(row["correlation"])
                except BackendUnavailable:
                    found = None
                if found:
                    db = self.db()
                    try:
                        db.execute(
                            "UPDATE jobs SET cups_job_id=?,status='submitted',updated_at=? WHERE id=?",
                            (int(found["id"]), now_iso(), row["id"]),
                        )
                        db.execute(
                            "UPDATE idempotency SET status='submitted' WHERE job_id=?", (row["id"],)
                        )
                        self._event(
                            db, row["id"], "recovery", "submitted", "Reconciled held CUPS job"
                        )
                    finally:
                        db.close()
                else:
                    self._set_status(
                        row["id"],
                        "unknown",
                        "Handoff intent has no reconcilable CUPS history; not resubmitted",
                        reason="Unknown outcome; operator evidence required",
                    )
            for row in submitted:
                try:
                    found = (
                        self.backend.get_job(int(row["cups_job_id"]))
                        if row["cups_job_id"]
                        else None
                    )
                except BackendUnavailable:
                    found = None
                if found is None:
                    self._set_status(
                        row["id"],
                        "unknown",
                        "CUPS job ID cannot be reconciled after restart",
                        reason="Unknown outcome; operator evidence required",
                    )
            # Remove interrupted uploads and orphaned committed-before-DB payloads.
            db = self.db()
            try:
                referenced = {row[0] for row in db.execute("SELECT spool_name FROM jobs")}
            finally:
                db.close()
            for path in self.settings.spool_dir.iterdir():
                if path.name.endswith(".part") or (
                    path.name.endswith(".payload") and path.name not in referenced
                ):
                    path.unlink(missing_ok=True)
            db = self.db()
            try:
                terminal = db.execute(
                    "SELECT spool_name FROM jobs WHERE status IN ('completed','failed','canceled')"
                ).fetchall()
            finally:
                db.close()
            for row in terminal:
                (self.settings.spool_dir / row["spool_name"]).unlink(missing_ok=True)
            # Accepted queued work whose payload disappeared cannot be safely printed.
            db = self.db()
            try:
                queued = db.execute(
                    "SELECT id,spool_name FROM jobs WHERE status IN ('queued','held')"
                ).fetchall()
            finally:
                db.close()
            for row in queued:
                if not (self.settings.spool_dir / row["spool_name"]).is_file():
                    self._set_status(
                        row["id"], "failed", "Accepted payload is missing after restart"
                    )

    def tick(self) -> None:
        db = self.db()
        try:
            ids = [row[0] for row in db.execute("SELECT id FROM printers WHERE deleted_at IS NULL")]
        finally:
            db.close()
        if ids:
            with ThreadPoolExecutor(max_workers=min(4, len(ids))) as pool:
                list(pool.map(self._tick_locked, ids))
        self.cleanup_history()

    def _tick_locked(self, printer_id: str) -> None:
        with self.printer_lock(printer_id):
            db = self.db()
            try:
                printer = db.execute(
                    "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                ).fetchone()
            finally:
                db.close()
            if printer is not None:
                try:
                    self._tick_printer(printer)
                except BackendUnavailable:
                    return
                except Exception:
                    LOG.exception("Printer cycle failed for %s", printer_id)

    def _latch_pause(self, printer: sqlite3.Row, reason: str) -> None:
        # Persist the latch first. A failed CUPS pause is NOT permission to resume on reconnect.
        db = self.db()
        try:
            db.execute(
                "UPDATE jobs SET resume_only=0 WHERE printer_id=? AND status NOT IN ('completed','failed','canceled')",
                (printer["id"],),
            )
        finally:
            db.close()
        if not printer["paused"] or printer["pause_reason"] != reason[:300]:
            db = self.db()
            try:
                db.execute(
                    "UPDATE printers SET paused=1,pause_reason=?,updated_at=? WHERE id=?",
                    (reason[:300], now_iso(), printer["id"]),
                )
                self._event_printer_jobs(db, printer["id"], "Printer paused: " + reason[:300])
            finally:
                db.close()
        try:
            self.backend.pause_printer(printer["queue"])
        except BackendUnavailable:
            LOG.warning("CUPS pause not confirmed for printer %s", printer["id"])

    def _tick_printer(self, printer: sqlite3.Row) -> None:
        db = self.db()
        try:
            active = db.execute(
                "SELECT * FROM jobs WHERE printer_id=? AND status IN ('submitting','submitted','unknown') "
                "ORDER BY sequence LIMIT 1",
                (printer["id"],),
            ).fetchone()
            # An unresolved outcome blocks the entire printer, irrespective of other job timestamps.
            unknown = db.execute(
                "SELECT 1 FROM jobs WHERE printer_id=? AND status='unknown'", (printer["id"],)
            ).fetchone()
            first = db.execute(
                "SELECT * FROM jobs WHERE printer_id=? AND status IN ('queued','held') "
                "ORDER BY sequence LIMIT 1",
                (printer["id"],),
            ).fetchone()
        finally:
            db.close()
        if active and active["status"] != "unknown":
            self._track_active(active, printer)
            return
        if unknown:
            return
        if first is None or first["status"] == "held":
            return
        if printer["paused"] and not first["resume_only"]:
            # Reassert an unconfirmed pause after a backend outage.
            try:
                self.backend.pause_printer(printer["queue"])
            except BackendUnavailable:
                pass
            return
        health = self.backend.printer_status(printer["queue"])
        if not health.get("known") or not health.get("online"):
            reason = (
                health.get("message")
                or ", ".join(health.get("reasons", []))
                or "CUPS printer unavailable"
            )
            self._latch_pause(printer, reason)
            return
        try:
            self._require_manual_error_policy(printer)
        except ValueError as exc:
            self._hold_for_mapping(first["id"], str(exc))
            return
        if not self._mapping_matches(first):
            self._hold_for_mapping(first["id"], "CUPS queue mapping differs from accepted snapshot")
            return
        payload = self.settings.spool_dir / first["spool_name"]
        if not payload.is_file():
            self._set_status(first["id"], "failed", "Accepted payload file is missing")
            return
        with payload.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != first["content_hash"]:
            self._set_status(first["id"], "failed", "Accepted payload integrity check failed")
            return
        db = self.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET status='submitting',updated_at=?,reason=NULL WHERE id=?",
                (now_iso(), first["id"]),
            )
            db.execute("UPDATE idempotency SET status='submitting' WHERE job_id=?", (first["id"],))
            self._event(
                db, first["id"], "worker", "submitting", "Persisted handoff intent before CUPS"
            )
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()
        try:
            cups_id = self.backend.submit_held(
                printer["queue"],
                str(payload),
                first["title"],
                {
                    **safe_json(first["options_json"], {}),
                    "copies": str(first["copies"]),
                    "document-format": "application/pdf"
                    if first["format"] == "pdf"
                    else "application/vnd.cups-raw",
                },
                first["correlation"],
            )
        except BackendUnavailable:
            self._set_status(
                first["id"],
                "unknown",
                "CUPS handoff response is uncertain; no automatic retry",
                reason="Unknown outcome; operator evidence required",
            )
            return
        db = self.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET cups_job_id=?,status='submitted',updated_at=? WHERE id=?",
                (cups_id, now_iso(), first["id"]),
            )
            db.execute("UPDATE idempotency SET status='submitted' WHERE job_id=?", (first["id"],))
            self._event(
                db, first["id"], "worker", "submitted", "CUPS accepted a held job; ID persisted"
            )
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            LOG.error(
                "Failed to persist CUPS job ID for %s; retained held job requires reconciliation",
                first["id"],
            )
            return
        finally:
            db.close()
        try:
            self.backend.release_job(cups_id)
        except BackendUnavailable:
            # Do not retry release blindly; query the held/processing state and require operator if held.
            return

    def _event_printer_jobs(self, db: sqlite3.Connection, printer_id: str, message: str) -> None:
        rows = db.execute(
            "SELECT id,status FROM jobs WHERE printer_id=? AND status IN ('queued','submitted')",
            (printer_id,),
        ).fetchall()
        for row in rows:
            self._event(db, row["id"], "worker", row["status"], message)

    def _require_manual_error_policy(self, printer: sqlite3.Row) -> None:
        mapping = self.backend.queue_mapping(printer["queue"])
        if not mapping or mapping.get("error_policy") != "stop-printer":
            raise ValueError(
                "CUPS stop-printer error policy is required; use a managed queue or configure the imported queue explicitly"
            )

    def _mapping_matches(self, row: sqlite3.Row) -> bool:
        snapshot = safe_json(row["snapshot_json"], {})
        mapping = self.backend.queue_mapping(snapshot.get("queue", ""))
        return bool(
            mapping
            and mapping["device_uri"] == snapshot.get("device_uri")
            and mapping["mapping_signature"] == snapshot.get("mapping_signature")
        )

    def _hold_for_mapping(self, job_id: str, reason: str) -> None:
        self._set_status(job_id, "held", reason, reason=reason)

    def _track_active(self, row: sqlite3.Row, printer: sqlite3.Row) -> None:
        if row["status"] == "submitting":
            try:
                found = self.backend.find_job(row["correlation"])
            except BackendUnavailable:
                return
            if found:
                db = self.db()
                try:
                    db.execute(
                        "UPDATE jobs SET cups_job_id=?,status='submitted',updated_at=? WHERE id=?",
                        (int(found["id"]), now_iso(), row["id"]),
                    )
                    db.execute(
                        "UPDATE idempotency SET status='submitted' WHERE job_id=?", (row["id"],)
                    )
                    self._event(db, row["id"], "worker", "submitted", "Reconciled held CUPS job")
                finally:
                    db.close()
            else:
                self._set_status(
                    row["id"],
                    "unknown",
                    "Cannot reconcile a persisted handoff intent; not resubmitted",
                    reason="Unknown outcome; operator evidence required",
                )
            return
        if row["status"] == "unknown":
            return
        if not row["cups_job_id"]:
            self._set_status(row["id"], "unknown", "Submitted state has no CUPS job ID")
            return
        try:
            found = self.backend.get_job(int(row["cups_job_id"]))
        except BackendUnavailable:
            return
        if found is None:
            self._set_status(
                row["id"],
                "unknown",
                "CUPS job history is missing; outcome is unknown",
                reason="CUPS history missing; operator evidence required",
            )
            return
        if found.get("correlation") != row["correlation"]:
            self._set_status(
                row["id"], "unknown", "CUPS job ID refers to another correlation; not released"
            )
            return
        state = found["state"]
        if state == "completed":
            self._set_status(row["id"], "completed", "CUPS reports completed")
        elif state == "canceled":
            self._set_status(row["id"], "canceled", "CUPS reports canceled")
        elif state == "aborted":
            self._set_status(row["id"], "failed", "CUPS reports aborted")
        elif state == "stopped":
            self._latch_pause(
                printer, "CUPS job stopped; possible partial output requires operator evidence"
            )
            self._set_status(
                row["id"], "unknown", "CUPS stopped job may have partial output; not restarted"
            )
            return
        elif state == "held":
            reason = "CUPS job held/stopped; operator resume required"
            if row["reason"] != reason:
                self._set_status(row["id"], "submitted", reason, reason=reason)
            self._latch_pause(printer, reason)
            return
        elif state == "unknown":
            self._set_status(
                row["id"],
                "unknown",
                "CUPS returned an unrecognized job state",
                reason="Operator evidence required",
            )
            return
        else:
            health = self.backend.printer_status(printer["queue"])
            if not health.get("known") or not health.get("online"):
                self._latch_pause(printer, health.get("message") or "CUPS printer unavailable")
            return
        if row["resume_only"] and printer["paused"]:
            self.backend.pause_printer(printer["queue"])

    def cleanup_history(self) -> int:
        db = self.db()
        removed = 0
        try:
            retention = self.limits(db)["history_retention_days"]
            cutoff = (datetime.now(UTC) - timedelta(days=retention)).isoformat(timespec="seconds")
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT id,spool_name FROM jobs WHERE status IN ('completed','failed','canceled') AND updated_at < ?",
                (cutoff,),
            ).fetchall()
            for row in old:
                (self.settings.spool_dir / row["spool_name"]).unlink(missing_ok=True)
                db.execute("UPDATE idempotency SET history_expired=1 WHERE job_id=?", (row["id"],))
                db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                removed += 1
            db.execute(
                "DELETE FROM sessions WHERE expires_at < ?", (datetime.now(UTC).timestamp(),)
            )
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()
        return removed

    def resolve_unknown(self, job_id: str, outcome: str, reason: str) -> dict[str, Any] | None:
        if (
            not isinstance(outcome, str)
            or outcome not in {"completed", "failed", "canceled"}
            or not isinstance(reason, str)
            or not 8 <= len(reason.strip()) <= 1000
        ):
            raise ValueError("outcome and operator evidence are required")
        with self.job_lock(job_id):
            db = self.db()
            try:
                row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            finally:
                db.close()
            if row is None:
                return None
            if row["status"] != "unknown":
                raise ValueError("Only unknown jobs can be resolved")
            record = self._get_job_row(job_id)
            try:
                known = (
                    self.backend.get_job(int(record["cups_job_id"]))
                    if record["cups_job_id"]
                    else self.backend.find_job(record["correlation"])
                )
            except BackendUnavailable as exc:
                raise ValueError(
                    "CUPS must be reachable before resolving an uncertain handoff"
                ) from exc
            if known and known.get("correlation") != record["correlation"]:
                raise ValueError("CUPS identity differs; cannot resolve using an unrelated job")
            if known and known["state"] not in {"completed", "canceled", "aborted"}:
                raise ValueError("CUPS still has a nonterminal job; cancel and verify it first")
            if (
                known
                and {"completed": "completed", "canceled": "canceled", "aborted": "failed"}[
                    known["state"]
                ]
                != outcome
            ):
                raise ValueError("Resolution conflicts with CUPS evidence")
            self._set_status(
                job_id, outcome, f"Operator resolution: {reason.strip()}", actor="admin"
            )
            db = self.db()
            try:
                return self._job_record(
                    db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                )
            finally:
                db.close()

    def resume_printer(self, printer_id: str) -> dict[str, Any] | None:
        with self.printer_lock(printer_id):
            db = self.db()
            try:
                printer = db.execute(
                    "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                ).fetchone()
                if printer is None:
                    return None
                if db.execute(
                    "SELECT 1 FROM jobs WHERE printer_id=? AND status='unknown'", (printer_id,)
                ).fetchone():
                    raise ValueError("Resolve unknown outcomes before resuming the printer")
                active = db.execute(
                    "SELECT * FROM jobs WHERE printer_id=? AND status='submitted'", (printer_id,)
                ).fetchone()
                if active and not self._mapping_matches(active):
                    raise ValueError("CUPS queue mapping differs from accepted snapshot")
                self._require_manual_error_policy(printer)
                current = None
                if active:
                    current = (
                        self.backend.get_job(int(active["cups_job_id"]))
                        if active["cups_job_id"]
                        else None
                    )
                    if (
                        not current
                        or current.get("correlation") != active["correlation"]
                        or current["state"] in {"unknown", "stopped"}
                    ):
                        self._set_status(
                            active["id"],
                            "unknown",
                            "CUPS identity/outcome cannot be verified for resume",
                        )
                        raise ValueError("Reconcile the unknown CUPS outcome before resuming")
                self.backend.resume_printer(printer["queue"])
                if active and current and current["state"] == "held":
                    self.backend.release_job(int(active["cups_job_id"]))
                db.execute(
                    "UPDATE printers SET paused=0,pause_reason=NULL,updated_at=? WHERE id=?",
                    (now_iso(), printer_id),
                )
                db.execute(
                    "UPDATE jobs SET status='queued',reason=NULL,updated_at=? WHERE printer_id=? AND status='held' "
                    "AND reason LIKE 'Printer paused:%'",
                    (now_iso(), printer_id),
                )
                return self._printer_record(
                    db.execute("SELECT * FROM printers WHERE id=?", (printer_id,)).fetchone()
                )
            finally:
                db.close()

    def pause_printer(
        self, printer_id: str, reason: str = "Paused by operator"
    ) -> dict[str, Any] | None:
        with self.printer_lock(printer_id):
            db = self.db()
            try:
                printer = db.execute(
                    "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                ).fetchone()
                if printer is None:
                    return None
                # Revoke any prior single-job permission even if the physical pause is unconfirmed.
                db.execute(
                    "UPDATE jobs SET resume_only=0 WHERE printer_id=? AND status NOT IN ('completed','failed','canceled')",
                    (printer_id,),
                )
                db.execute(
                    "UPDATE printers SET paused=1,pause_reason=?,updated_at=? WHERE id=?",
                    (reason[:300], now_iso(), printer_id),
                )
                self.backend.pause_printer(printer["queue"])
                return self._printer_record(
                    db.execute("SELECT * FROM printers WHERE id=?", (printer_id,)).fetchone()
                )
            finally:
                db.close()

    def resume_job(self, job_id: str) -> dict[str, Any] | None:
        with self.job_lock(job_id):
            db = self.db()
            try:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None:
                    return None
                printer = db.execute(
                    "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (row["printer_id"],)
                ).fetchone()
                if printer is None:
                    return None
                if row["status"] not in {"queued", "held", "submitted"}:
                    raise ValueError("Job is not resumable; unknown outcomes require evidence")
                predecessor = db.execute(
                    "SELECT id FROM jobs WHERE printer_id=? AND status IN ('queued','held','submitting','submitted','unknown') "
                    "ORDER BY sequence LIMIT 1",
                    (row["printer_id"],),
                ).fetchone()
                if predecessor and predecessor[0] != job_id:
                    raise ValueError("An earlier or unresolved job blocks FIFO; resolve it first")
                self._require_manual_error_policy(printer)
                if not self._mapping_matches(row):
                    raise ValueError("CUPS queue mapping differs from accepted snapshot")
                current = None
                if row["status"] == "submitted":
                    current = (
                        self.backend.get_job(int(row["cups_job_id"]))
                        if row["cups_job_id"]
                        else None
                    )
                    if (
                        not current
                        or current.get("correlation") != row["correlation"]
                        or current["state"] not in {"held", "pending"}
                    ):
                        raise ValueError("CUPS does not report a resumable job")
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE printers SET paused=1,pause_reason=?,updated_at=? WHERE id=?",
                    ("Individual job resume; remaining queue held", now_iso(), row["printer_id"]),
                )
                target_status = "submitted" if current else "queued"
                db.execute(
                    "UPDATE jobs SET status=?,resume_only=1,reason=NULL,updated_at=? WHERE id=?",
                    (target_status, now_iso(), job_id),
                )
                db.execute(
                    "UPDATE idempotency SET status=? WHERE job_id=?", (target_status, job_id)
                )
                self._event(
                    db,
                    job_id,
                    "admin",
                    target_status,
                    "Operator requested this job only; remaining queue held",
                )
                db.execute("COMMIT")
                self.backend.resume_printer(printer["queue"])
                if current and current["state"] == "held":
                    self.backend.release_job(int(row["cups_job_id"]))
                return self._job_record(
                    db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                )
            finally:
                db.close()

    @staticmethod
    def _printer_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "queue": row["queue"],
            "device_uri": row["device_uri"],
            "driver": row["driver"],
            "managed": bool(row["managed"]),
            "formats": safe_json(row["formats_json"], []),
            "default_options": safe_json(row["defaults_json"], {}),
            "allowed_options": safe_json(row["allowed_json"], {}),
            "paused": bool(row["paused"]),
            "pause_reason": row["pause_reason"],
        }

    def admin_printers(self) -> list[dict[str, Any]]:
        db = self.db()
        try:
            return [
                self._printer_record(row)
                for row in db.execute(
                    "SELECT * FROM printers WHERE deleted_at IS NULL ORDER BY name"
                )
            ]
        finally:
            db.close()

    def admin_printer(self, printer_id: str) -> dict[str, Any] | None:
        db = self.db()
        try:
            row = db.execute(
                "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
            ).fetchone()
            return self._printer_record(row) if row else None
        finally:
            db.close()

    def delete_printer(self, printer_id: str) -> bool:
        with self.printer_lock(printer_id):
            db = self.db()
            try:
                db.execute("BEGIN IMMEDIATE")
                printer = db.execute(
                    "SELECT id FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                ).fetchone()
                if printer is None:
                    db.execute("ROLLBACK")
                    return False
                active = db.execute(
                    "SELECT 1 FROM jobs WHERE printer_id=? "
                    "AND status IN ('queued','held','submitting','submitted','unknown') LIMIT 1",
                    (printer_id,),
                ).fetchone()
                if active:
                    raise ValueError("Printer has nonterminal jobs")
                retired_queue = f"__deleted__{uuid.uuid4().hex}"
                while db.execute(
                    "SELECT 1 FROM printers WHERE queue=?", (retired_queue,)
                ).fetchone():
                    retired_queue = f"__deleted__{uuid.uuid4().hex}"
                db.execute("DELETE FROM client_printers WHERE printer_id=?", (printer_id,))
                # Free the unique queue value for later registration without touching CUPS.
                deleted_at = now_iso()
                db.execute(
                    "UPDATE printers SET deleted_at=?,queue=?,updated_at=? "
                    "WHERE id=? AND deleted_at IS NULL",
                    (deleted_at, retired_queue, deleted_at, printer_id),
                )
                db.execute("COMMIT")
                return True
            finally:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                db.close()

    def delete_client(self, client_id: str) -> bool:
        db = self.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            client = db.execute(
                "SELECT id FROM clients WHERE id=? AND deleted_at IS NULL", (client_id,)
            ).fetchone()
            if client is None:
                db.execute("ROLLBACK")
                return False
            active = db.execute(
                "SELECT 1 FROM jobs WHERE client_id=? "
                "AND status IN ('queued','held','submitting','submitted','unknown') LIMIT 1",
                (client_id,),
            ).fetchone()
            if active:
                raise ValueError("Client has nonterminal jobs")
            deleted_at = now_iso()
            db.execute("DELETE FROM client_printers WHERE client_id=?", (client_id,))
            db.execute(
                "UPDATE clients SET deleted_at=?,revoked=1 WHERE id=? AND deleted_at IS NULL",
                (deleted_at, client_id),
            )
            db.execute("COMMIT")
            return True
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()

    def create_managed_printer(
        self,
        *,
        name: str,
        device_uri: str,
        driver: str,
        formats: Any,
        defaults: Any,
        allowed: Any,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Invalid printer name")
        formats = validate_formats(formats)
        defaults, allowed = validate_options_config(defaults, allowed)
        inventory = self.backend.discover()
        device_set = {x["uri"] for x in inventory["devices"]}
        driver_set = {x["id"] for x in inventory["drivers"]}
        validate_device_uri(device_uri, device_set)
        if driver not in driver_set:
            raise ValueError("Choose a driver discovered from local CUPS")
        printer_id = str(uuid.uuid4())
        queue = SAFE_QUEUE_PREFIX + printer_id.replace("-", "")[:16]
        self.backend.create_queue(queue, device_uri, driver)
        return self._insert_printer(
            printer_id, name, queue, device_uri, driver, True, formats, defaults, allowed
        )

    def import_queue(self, *, queue: str, name: str, formats: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Invalid printer name")
        formats = validate_formats(formats)
        queue_attrs = self.backend.queues().get(queue)
        if not queue_attrs:
            raise ValueError("Select an existing CUPS queue")
        device_uri = str(queue_attrs.get("device-uri", ""))
        driver = str(
            queue_attrs.get("printer-ppd-name")
            or queue_attrs.get("printer-driver-name")
            or queue_attrs.get("printer-make-and-model", "Imported existing queue")
        )
        printer_id = str(uuid.uuid4())
        result = self._insert_printer(
            printer_id, name, queue, device_uri, driver, False, formats, {}, {}
        )
        db = self.db()
        try:
            db.execute(
                "UPDATE printers SET paused=1,pause_reason=? WHERE id=?",
                (
                    "Imported queue: verify exclusive ownership and stop-printer error policy before resume",
                    printer_id,
                ),
            )
        finally:
            db.close()
        result.update(
            paused=True,
            pause_reason="Imported queue: verify exclusive ownership and stop-printer error policy before resume",
        )
        return result

    def _insert_printer(
        self,
        printer_id: str,
        name: str,
        queue: str,
        uri: str,
        driver: str,
        managed: bool,
        formats: list[str],
        defaults: dict[str, str],
        allowed: dict[str, list[str]],
    ) -> dict[str, Any]:
        mapping = self.backend.queue_mapping(queue)
        if not mapping or mapping["device_uri"] != uri:
            raise ValueError("Unable to verify CUPS queue mapping")
        db = self.db()
        try:
            now = now_iso()
            db.execute(
                "INSERT INTO printers(id,name,queue,device_uri,driver,mapping_signature,managed,formats_json,defaults_json,"
                "allowed_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    printer_id,
                    name.strip(),
                    queue,
                    uri,
                    driver,
                    mapping["mapping_signature"],
                    int(managed),
                    json_dump(formats),
                    json_dump(defaults),
                    json_dump(allowed),
                    now,
                    now,
                ),
            )
            return self._printer_record(
                db.execute("SELECT * FROM printers WHERE id=?", (printer_id,)).fetchone()
            )
        finally:
            db.close()

    def edit_printer(self, printer_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
        with self.printer_lock(printer_id):
            return self._edit_printer(printer_id, body)

    def _edit_printer(self, printer_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
        db = self.db()
        try:
            current = db.execute(
                "SELECT * FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
            ).fetchone()
        finally:
            db.close()
        if current is None:
            return None
        name = body.get("name", current["name"])
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Invalid printer name")
        formats = validate_formats(body.get("formats", safe_json(current["formats_json"], [])))
        defaults, allowed = validate_options_config(
            body.get("default_options", safe_json(current["defaults_json"], {})),
            body.get("allowed_options", safe_json(current["allowed_json"], {})),
        )
        if "default_options" in body or "allowed_options" in body:
            caps = self.backend.printer_capabilities(current["queue"])
            descriptors = {item["name"]: item for item in caps.get("options", [])}
            if caps.get("availability") != "available":
                raise ValueError("Current printer option schema is unavailable")
            for key, values in allowed.items():
                descriptor = descriptors.get(key)
                choices = {item["value"] for item in descriptor["choices"]} if descriptor else set()
                if not choices or not set(values) <= choices:
                    raise ValueError("Printer options must use choices in the current CUPS schema")
        uri, driver = current["device_uri"], current["driver"]
        mapping_changed = body.get("device_uri", uri) != uri or body.get("driver", driver) != driver
        if mapping_changed:
            db = self.db()
            try:
                if db.execute(
                    "SELECT 1 FROM jobs WHERE printer_id=? AND status IN ('submitting','submitted','unknown')",
                    (printer_id,),
                ).fetchone():
                    raise ValueError(
                        "Finish or reconcile handed-off jobs before changing the printer mapping"
                    )
            finally:
                db.close()
        if current["managed"] and mapping_changed:
            inventory = self.backend.discover()
            uri = body.get("device_uri", uri)
            driver = body.get("driver", driver)
            validate_device_uri(uri, {x["uri"] for x in inventory["devices"]})
            if driver not in {x["id"] for x in inventory["drivers"]}:
                raise ValueError("Choose a driver discovered from local CUPS")
            self.backend.update_queue(current["queue"], uri, driver)
        elif not current["managed"] and mapping_changed:
            raise ValueError("Imported queues cannot be reconfigured here")
        mapping = self.backend.queue_mapping(current["queue"])
        if not mapping or mapping["device_uri"] != uri:
            raise ValueError("CUPS mapping changed externally; inspect and re-register explicitly")
        db = self.db()
        try:
            db.execute(
                "UPDATE printers SET name=?,device_uri=?,driver=?,mapping_signature=?,formats_json=?,defaults_json=?,allowed_json=?,"
                "updated_at=? WHERE id=? AND deleted_at IS NULL",
                (
                    name.strip(),
                    uri,
                    driver,
                    mapping["mapping_signature"],
                    json_dump(formats),
                    json_dump(defaults),
                    json_dump(allowed),
                    now_iso(),
                    printer_id,
                ),
            )
            return self._printer_record(
                db.execute("SELECT * FROM printers WHERE id=?", (printer_id,)).fetchone()
            )
        finally:
            db.close()

    def admin_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        db = self.db()
        try:
            rows = db.execute(
                "SELECT j.*,c.name AS client_name,p.name AS printer_name FROM jobs j "
                "JOIN clients c ON c.id=j.client_id JOIN printers p ON p.id=j.printer_id "
                "ORDER BY j.sequence DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
            return [
                {
                    **self._job_record(row),
                    "client_name": row["client_name"],
                    "printer_name": row["printer_name"],
                }
                for row in rows
            ]
        finally:
            db.close()

    def admin_job(self, job_id: str) -> dict[str, Any] | None:
        db = self.db()
        try:
            row = db.execute(
                "SELECT j.*,c.name AS client_name,p.name AS printer_name FROM jobs j "
                "JOIN clients c ON c.id=j.client_id JOIN printers p ON p.id=j.printer_id WHERE j.id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                **self._job_record(row, include_events=True),
                "client_name": row["client_name"],
                "printer_name": row["printer_name"],
                "printer_snapshot": safe_json(row["snapshot_json"], {}),
                "cups_job_id": row["cups_job_id"],
            }
        finally:
            db.close()
