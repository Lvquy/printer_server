from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from . import db as store
from .auth import password_hash
from .config import Settings


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else settings.data_dir
    return replace(
        settings,
        data_dir=data_dir,
        host=getattr(args, "host", None) or settings.host,
        port=getattr(args, "port", None) or settings.port,
    )


def _bootstrap(settings: Settings, password: str) -> None:
    if len(password) < 12:
        raise ValueError("Admin password must be at least 12 characters")
    store.initialize(settings.database_path)
    salt = secrets.token_bytes(16)
    digest = password_hash(password, salt)
    connection = store.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO admin(id,salt,password_hash,changed_at) VALUES(1,?,?,datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,"
            "changed_at=excluded.changed_at",
            (salt.hex(), digest),
        )
        connection.execute("DELETE FROM sessions")
    finally:
        connection.close()
    os.chmod(settings.database_path, 0o640)
    print("Admin password set. Existing sessions were invalidated.")


def _backup(settings: Settings, destination: Path) -> None:
    if not settings.database_path.exists():
        raise FileNotFoundError("Appliance database does not exist")
    if destination.resolve() == settings.database_path.resolve():
        raise ValueError("Backup destination must differ from the active database")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    os.close(fd)
    target = sqlite3.connect(destination)
    source = store.connect(settings.database_path)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed")
    finally:
        source.close()
        target.close()
    os.chmod(destination, 0o640)
    print(f"Consistent SQLite backup written to {destination}")
    print(
        "This command backs up SQLite only; stop the service before copying the full data directory/spool."
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="print-appliance")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the single-process API and worker")
    run.add_argument("--data-dir")
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    password = sub.add_parser("admin-password", help="Set or reset the admin password")
    password.add_argument("--data-dir")
    backup = sub.add_parser(
        "backup", help="Create a consistent SQLite backup using sqlite3 backup API"
    )
    backup.add_argument("--data-dir")
    backup.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    settings = _settings(args)

    try:
        if args.command == "admin-password":
            first = getpass.getpass("New admin password (12+ characters): ")
            second = getpass.getpass("Confirm admin password: ")
            if first != second:
                raise ValueError("Passwords do not match")
            _bootstrap(settings, first)
        elif args.command == "backup":
            _backup(settings, args.destination)
        elif args.command == "run":
            import uvicorn

            from .app import create_app

            application = create_app(settings)
            uvicorn.run(
                application, host=settings.host, port=settings.port, workers=1, access_log=False
            )
    except (ValueError, OSError) as exc:
        print(f"print-appliance: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
