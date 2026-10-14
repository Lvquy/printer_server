from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

PASSWORD_N = 1 << 14
PASSWORD_R = 8
PASSWORD_P = 1


def password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=PASSWORD_N, r=PASSWORD_R, p=PASSWORD_P, dklen=32
    ).hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        actual = password_hash(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(actual, expected_hex)
    except (ValueError, TypeError):
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def make_session_values() -> tuple[str, str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    return token, hash_session_token(token), csrf


def get_session(appliance: Any, token: str | None) -> dict[str, Any] | None:
    if not token or len(token) > 128 or not token.isascii():
        return None
    db = appliance.db()
    try:
        import time

        row = db.execute(
            "SELECT csrf_token,expires_at FROM sessions WHERE token_hash=?",
            (hash_session_token(token),),
        ).fetchone()
        if row is None or row["expires_at"] <= time.time():
            return None
        return {"token_hash": hash_session_token(token), "csrf_token": row["csrf_token"]}
    finally:
        db.close()
