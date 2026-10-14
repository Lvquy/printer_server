from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_LIMITS: dict[str, int] = {
    "max_upload_bytes": 10 * 1024 * 1024,
    "max_pending_jobs": 100,
    "min_free_bytes": 100 * 1024 * 1024,
    "history_retention_days": 30,
}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS printers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    queue TEXT NOT NULL UNIQUE,
    device_uri TEXT NOT NULL,
    driver TEXT NOT NULL,
    mapping_signature TEXT NOT NULL,
    managed INTEGER NOT NULL DEFAULT 0,
    formats_json TEXT NOT NULL,
    defaults_json TEXT NOT NULL,
    allowed_json TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS client_printers (
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    printer_id TEXT NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
    PRIMARY KEY(client_id, printer_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    sequence INTEGER,
    client_id TEXT NOT NULL REFERENCES clients(id),
    printer_id TEXT NOT NULL REFERENCES printers(id),
    status TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT NOT NULL,
    copies INTEGER NOT NULL,
    options_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    spool_name TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    correlation TEXT NOT NULL UNIQUE,
    cups_job_id INTEGER,
    reason TEXT,
    resume_only INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_printer_fifo ON jobs(printer_id, accepted_at, id);
CREATE INDEX IF NOT EXISTS jobs_client_recent ON jobs(client_id, accepted_at DESC);
CREATE TABLE IF NOT EXISTS idempotency (
    client_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    digest TEXT NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    history_expired INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(client_id, request_key)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY CHECK(id=1),
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_guard (
    id INTEGER PRIMARY KEY CHECK(id=1),
    window_started REAL NOT NULL,
    failures INTEGER NOT NULL,
    locked_until REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO login_guard(id, window_started, failures, locked_until) VALUES(1, 0, 0, 0);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if not path.exists():
        path.touch(mode=0o640)
    db = connect(path)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.executescript(SCHEMA)
        # Early development databases lack the fingerprint. Fail closed for old job snapshots.
        columns = {row[1] for row in db.execute("PRAGMA table_info(printers)")}
        if "mapping_signature" not in columns:
            db.execute("ALTER TABLE printers ADD COLUMN mapping_signature TEXT NOT NULL DEFAULT ''")
        if "deleted_at" not in columns:
            db.execute("ALTER TABLE printers ADD COLUMN deleted_at TEXT")
        client_columns = {row[1] for row in db.execute("PRAGMA table_info(clients)")}
        if "deleted_at" not in client_columns:
            db.execute("ALTER TABLE clients ADD COLUMN deleted_at TEXT")
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "sequence" not in job_columns:
            db.execute("ALTER TABLE jobs ADD COLUMN sequence INTEGER")
        db.execute("UPDATE jobs SET sequence=rowid WHERE sequence IS NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_sequence ON jobs(sequence)")
        for key, value in DEFAULT_LIMITS.items():
            db.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (key, json.dumps(value)),
            )
    finally:
        db.close()


def get_limits(db: sqlite3.Connection) -> dict[str, int]:
    values = dict(DEFAULT_LIMITS)
    for row in db.execute("SELECT key,value FROM settings"):
        try:
            values[row["key"]] = int(json.loads(row["value"]))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return values


def save_setting(db: sqlite3.Connection, key: str, value: Any) -> None:
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
