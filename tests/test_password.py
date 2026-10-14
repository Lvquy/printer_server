from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from print_appliance.auth import verify_password
from print_appliance.cli import _bootstrap
from tests.conftest import Harness

CURRENT = "isolated-current-password"
NEW = "isolated-new-password"
URL = "/admin/api/password"


def login(harness: Harness, client: TestClient | None = None) -> dict[str, str]:
    response = (client or harness.api).post("/admin/api/login", json={"password": CURRENT})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def payload(**updates):
    return {"current_password": CURRENT, "new_password": NEW, "confirm_password": NEW, **updates}


def snapshot(harness: Harness):
    with harness.appliance.db() as db:
        return tuple(db.execute("SELECT * FROM admin").fetchone())


def test_change_password_protected_atomic_and_revokes_all_sessions(harness: Harness):
    _bootstrap(harness.appliance.settings, CURRENT)
    before = snapshot(harness)
    assert harness.api.post(URL, json=payload()).status_code == 401
    headers = login(harness)
    assert harness.api.post(URL, json=payload()).status_code == 403
    assert (
        harness.api.post(
            URL, json=payload(), headers={**headers, "Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    other = TestClient(harness.app)
    other_headers = login(harness, other)
    response = harness.api.post(URL, json=payload(), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    cookie = response.headers["set-cookie"].lower()
    assert "max-age=0" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    after = snapshot(harness)
    assert before[1] != after[1] and before[2] != after[2]
    assert verify_password(NEW, after[1], after[2])
    assert not verify_password(CURRENT, after[1], after[2])
    with harness.appliance.db() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert harness.api.get("/admin/api/session").json() == {"authenticated": False}
    assert other.get("/admin/api/printers").status_code == 401
    assert other.post(URL, json=payload(), headers=other_headers).status_code == 401
    assert harness.api.post("/admin/api/login", json={"password": CURRENT}).status_code == 401
    assert harness.api.post("/admin/api/login", json={"password": NEW}).status_code == 200
    events = harness.api.get("/admin/api/audit").json()
    assert any(event["target"] == URL for event in events)
    assert all(value not in json.dumps(events) for value in (CURRENT, NEW, headers["X-CSRF-Token"]))
    assert (
        harness.api.get(
            "/api/v1/printers", headers={"Authorization": f"Bearer {harness.key}"}
        ).status_code
        == 200
    )  # Admin password changes do not revoke clients.


@pytest.mark.parametrize(
    "updates",
    [
        {"current_password": ""},
        {"current_password": None},
        {"current_password": [CURRENT]},
        {"current_password": "x" * 1025},
        {"new_password": "short", "confirm_password": "short"},
        {"new_password": "x" * 1025, "confirm_password": "x" * 1025},
        {"new_password": None},
        {"confirm_password": False},
        {"confirm_password": "mismatch"},
        {"new_password": CURRENT, "confirm_password": CURRENT},
    ],
)
def test_invalid_change_preserves_password_and_sessions(harness: Harness, updates):
    _bootstrap(harness.appliance.settings, CURRENT)
    headers = login(harness)
    before = snapshot(harness)
    response = harness.api.post(URL, json=payload(**updates), headers=headers)
    assert response.status_code == 422
    assert snapshot(harness) == before
    assert harness.api.get("/admin/api/session").json()["authenticated"] is True
    assert CURRENT not in response.text and NEW not in response.text


def test_wrong_current_password_is_bounded_without_destroying_session(harness: Harness):
    _bootstrap(harness.appliance.settings, CURRENT)
    headers = login(harness)
    before = snapshot(harness)
    for _ in range(5):
        response = harness.api.post(
            URL, json=payload(current_password="incorrect"), headers=headers
        )
        assert response.status_code == 400
    assert harness.api.get("/admin/api/session").json()["authenticated"] is True
    assert snapshot(harness) == before
    assert harness.api.post(URL, json=payload(), headers=headers).status_code == 429
    assert harness.api.post("/admin/api/login", json={"password": CURRENT}).status_code == 429
    with harness.appliance.db() as db:
        db.execute("UPDATE login_guard SET locked_until=0,window_started=0 WHERE id=1")
    assert harness.api.post(URL, json=payload(), headers=headers).status_code == 200


def test_concurrent_change_cannot_use_a_revoked_session(harness: Harness):
    _bootstrap(harness.appliance.settings, CURRENT)
    clients = [TestClient(harness.app), TestClient(harness.app)]
    headers = [login(harness, client) for client in clients]

    def change(index):
        return clients[index].post(URL, json=payload(), headers=headers[index]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, range(2))) == [200, 401]
    with harness.appliance.db() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize("data", [CURRENT, [CURRENT, NEW]])
def test_nonobject_body_never_echoes_passwords(harness: Harness, data):
    _bootstrap(harness.appliance.settings, CURRENT)
    headers = login(harness)
    response = harness.api.post(URL, json=data, headers=headers)
    assert response.status_code == 422
    assert CURRENT not in response.text and NEW not in response.text


def test_change_failure_rolls_back_password_and_session_revocation(harness: Harness):
    _bootstrap(harness.appliance.settings, CURRENT)
    headers = login(harness)
    before = snapshot(harness)
    with harness.appliance.db() as db:
        db.execute(
            "CREATE TRIGGER fail_session_delete BEFORE DELETE ON sessions "
            "BEGIN SELECT RAISE(ABORT, 'Injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        harness.api.post(URL, json=payload(), headers=headers)
    assert snapshot(harness) == before
    assert harness.api.get("/admin/api/session").json()["authenticated"] is True
    with harness.appliance.db() as db:
        db.execute("DROP TRIGGER fail_session_delete")
    assert harness.api.post(URL, json=payload(), headers=headers).status_code == 200


def test_password_characters_are_not_trimmed(harness: Harness):
    _bootstrap(harness.appliance.settings, CURRENT)
    headers = login(harness)
    new = "  Mật khẩu mới 123!  "
    assert (
        harness.api.post(
            URL, json=payload(new_password=new, confirm_password=new), headers=headers
        ).status_code
        == 200
    )
    assert harness.api.post("/admin/api/login", json={"password": new}).status_code == 200
