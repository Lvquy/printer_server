from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from print_appliance.app import create_app
from print_appliance.config import Settings
from print_appliance.service import Appliance, hash_api_key
from tests.fakes import FakeCups


@dataclass
class Harness:
    app: Any
    appliance: Appliance
    backend: FakeCups
    api: TestClient
    key: str
    client_id: str
    printer: dict[str, Any]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    backend = FakeCups()
    settings = Settings(data_dir=tmp_path / "data", worker_interval_seconds=60)
    app = create_app(settings, backend, worker_enabled=False)
    appliance: Appliance = app.state.appliance
    printer = appliance.create_managed_printer(
        name="Test printer",
        device_uri=backend.device_uri,
        driver=backend.driver,
        formats=["pdf", "zpl"],
        defaults={},
        allowed={"media": ["A4", "Letter"]},
    )
    key = "pa_test_client_key_that_is_random_and_long"
    client_id = str(uuid.uuid4())
    db = appliance.db()
    try:
        db.execute(
            "INSERT INTO clients(id,name,key_hash,created_at) VALUES(?,?,?,datetime('now'))",
            (client_id, "Test client", hash_api_key(key)),
        )
        db.execute(
            "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)",
            (client_id, printer["id"]),
        )
    finally:
        db.close()
    api = TestClient(app)
    return Harness(app, appliance, backend, api, key, client_id, printer)
