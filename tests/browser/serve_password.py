"""Disposable loopback password-test server: explicit FakeCups, worker disabled.

Run from the repo root: python -m tests.browser.serve_password
Use the printed fixture path as UI_TEST_FIXTURE when running password-ui.cjs.
This never opens the appliance's real data directory or submits to CUPS.
"""

from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from pathlib import Path

import uvicorn

from print_appliance.app import create_app
from print_appliance.cli import _bootstrap
from print_appliance.config import Settings
from tests.fakes import FakeCups


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="print-password-test-"))
    settings = Settings(data_dir=root / "data")
    password = secrets.token_urlsafe(24)
    _bootstrap(settings, password)
    app = create_app(settings, FakeCups(), worker_enabled=False)
    fixture = root / "fixture.json"
    fixture.write_text(
        json.dumps({"password": password, "test_only": True, "worker_enabled": False})
    )
    fixture.chmod(0o600)
    print(f"UI_TEST_FIXTURE={fixture}", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=18086, access_log=False)
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    main()
