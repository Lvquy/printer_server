from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import shutil
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import db as store
from .auth import (
    get_session,
    hash_session_token,
    make_session_values,
    password_hash,
    verify_password,
)
from .config import Settings
from .cups import BackendUnavailable, CupsBackend, PyCupsBackend
from .print_options import InvalidPrintOption
from .service import Appliance, hash_api_key

LOG = logging.getLogger("print_appliance")
COOKIE_NAME = "pa_admin"
SESSION_SECONDS = 8 * 60 * 60


class WorkerLock:
    def __init__(self, path: Path):
        self.path = path
        self.file: Any = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.file = self.path.open("a+")
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("Another print-appliance worker owns this data directory") from exc
        self.file.seek(0)
        self.file.truncate()
        self.file.write(f"pid={os.getpid()}\n")
        self.file.flush()

    def release(self) -> None:
        if self.file is not None:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


class RequestSizeLimit:
    """Bound complete ASGI request bodies, including chunked multipart requests."""

    def __init__(self, app: ASGIApp, max_bytes: Callable[[], int]):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        maximum = self.max_bytes()
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > maximum:
                    response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass
        seen = 0
        started = False

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def tracked_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > maximum:
                    raise RequestTooLarge
            return message

        try:
            await self.app(scope, tracked_receive, tracked_send)
        except RequestTooLarge:
            if not started:
                response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                await response(scope, receive, send)


class RequestTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "Request body too large")


def create_app(
    settings: Settings | None = None,
    backend: CupsBackend | None = None,
    *,
    worker_enabled: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    appliance = Appliance(settings, backend or PyCupsBackend())
    lock: WorkerLock | None = None
    stopping = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal lock
        try:
            if worker_enabled:
                lock = WorkerLock(settings.data_dir / "worker.lock")
                lock.acquire()
                await asyncio.to_thread(appliance.recover_startup)
                task = asyncio.create_task(worker_loop())
                app.state.worker_task = task
            yield
        finally:
            task = getattr(app.state, "worker_task", None)
            if task:
                stopping.set()
                # Keep the directory lock until the outstanding thread/CUPS operation is finished.
                await task
            if lock:
                lock.release()

    app = FastAPI(
        title="Print appliance", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.appliance = appliance
    app.add_middleware(
        RequestSizeLimit,
        max_bytes=lambda: max(
            settings.max_request_bytes,
            appliance.limits()["max_upload_bytes"] + 2 * 1024 * 1024,
        ),
    )

    async def worker_loop() -> None:
        while not stopping.is_set():
            try:
                await asyncio.to_thread(appliance.tick)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Worker cycle failed")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=settings.worker_interval_seconds)
            except TimeoutError:
                pass

    def get_appliance() -> Appliance:
        return appliance

    def api_client(
        authorization: str | None = Header(default=None),
        app_state: Appliance = Depends(get_appliance),
    ) -> dict[str, str]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid API key")
        key = authorization[7:].strip()
        client = app_state.client_for_key(key)
        if not client:
            raise HTTPException(401, "Invalid API key")
        return {"id": client["id"], "name": client["name"]}

    def admin_session(request: Request) -> dict[str, str]:
        session = get_session(appliance, request.cookies.get(COOKIE_NAME))
        if not session:
            raise HTTPException(401, "Admin login required")
        return session

    def admin_mutation(
        request: Request, session: dict[str, str] = Depends(admin_session)
    ) -> dict[str, str]:
        origin = request.headers.get("origin")
        if origin:
            request_origin = f"{request.url.scheme}://{request.url.netloc}"
            if not hmac.compare_digest(origin.rstrip("/"), request_origin.rstrip("/")):
                raise HTTPException(403, "Cross-origin mutation rejected")
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not hmac.compare_digest(csrf, session["csrf_token"]):
            raise HTTPException(403, "Invalid CSRF token")
        return session

    def body_json(data: dict[str, Any], field: str, fallback: Any = None) -> Any:
        value = data.get(field, fallback)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field} must be valid JSON") from exc
        return value

    def safe_backend(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except BackendUnavailable as exc:
            raise HTTPException(503, "Local CUPS is unavailable or not authorized") from exc

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path in {"/", "/client"} or request.url.path.startswith(
            ("/admin/", "/api/")
        ):
            response.headers["Cache-Control"] = "no-store"
        if (
            request.method in {"POST", "PUT", "DELETE"}
            and request.url.path.startswith("/admin/api/")
            and response.status_code < 400
        ):
            db = appliance.db()
            try:
                db.execute(
                    "INSERT INTO audit_events(at,actor,action,target,message) VALUES(?,?,?,?,?)",
                    (
                        datetime.now(UTC).isoformat(),
                        "admin",
                        request.method,
                        request.url.path,
                        "Succeeded",
                    ),
                )
            finally:
                db.close()
        return response

    @app.get("/admin/api/audit")
    def audit_events(
        limit: int = 100, _: dict[str, str] = Depends(admin_session)
    ) -> list[dict[str, Any]]:
        db = appliance.db()
        try:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                )
            ]
        finally:
            db.close()

    @app.exception_handler(RequestTooLarge)
    async def request_too_large(_: Request, __: RequestTooLarge) -> JSONResponse:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/client", include_in_schema=False)
    def client_page() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "client.html")

    @app.get("/assets/{asset}", include_in_schema=False)
    def assets(asset: str) -> FileResponse:
        if asset not in {"app.js", "client.js", "style.css", "THIRD_PARTY_LICENSES.txt"}:
            raise HTTPException(404, "Not found")
        return FileResponse(Path(__file__).parent / "static" / asset)

    # Client API: key scope is checked after idempotency replay lookup for safe same-client retries.
    @app.get("/api/v1/printers")
    def client_printers(client: dict[str, str] = Depends(api_client)) -> list[dict[str, Any]]:
        return appliance.list_client_printers(client["id"])

    async def validate_print_form(request: Request) -> None:
        form = await request.form()
        known = {"file", "printer_id", "format", "title", "copies", "options",
                 "paper_size", "sides", "color_mode", "scaling"}
        for name in form:
            if name not in known or len(form.getlist(name)) != 1 or (
                name in {"paper_size", "sides", "color_mode", "scaling"} and form[name] == ""
            ):
                raise HTTPException(422, {"code": "invalid_print_field", "field": name,
                                          "message": "Unknown, repeated or empty print field."})

    @app.post("/api/v1/jobs", dependencies=[Depends(validate_print_form)])
    def submit_job(
        file: UploadFile = File(...),
        printer_id: str = Form(...),
        format: str = Form(...),
        title: str = Form(...),
        copies: str = Form("1"),
        options: str | None = Form(None),
        paper_size: str | None = Form(None),
        sides: str | None = Form(None),
        color_mode: str | None = Form(None),
        scaling: str | None = Form(None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        client: dict[str, str] = Depends(api_client),
    ) -> JSONResponse:
        if options is not None and any(v is not None for v in (paper_size, sides, color_mode, scaling)):
            raise HTTPException(422, {"field": "options", "message": "Do not mix legacy and public fields."})
        if not idempotency_key:
            raise HTTPException(422, "Idempotency-Key is required")
        try:
            result, code = appliance.accept_job(
                client_id=client["id"],
                idempotency_key=idempotency_key,
                printer_id=printer_id,
                format_name=format,
                title=title,
                copies_raw=copies,
                legacy_options=options is not None,
                options_raw=options if options is not None else json.dumps({k: v for k, v in {
                    "paper_size": paper_size, "sides": sides,
                    "color_mode": color_mode, "scaling": scaling,
                }.items() if v is not None}),
                filename=file.filename or "",
                upload=file,
            )
            return JSONResponse(result, status_code=code)
        except InvalidPrintOption as exc:
            raise HTTPException(422, exc.detail) from exc
        except BackendUnavailable as exc:
            raise HTTPException(503, "Printer capabilities are unavailable") from exc
        except PermissionError as exc:
            raise HTTPException(403, "Printer is not granted to this client") from exc
        except OverflowError as exc:
            message = str(exc)
            if message == "file_too_large":
                raise HTTPException(413, "File exceeds configured upload limit") from exc
            raise HTTPException(429, "Appliance queue is full") from exc
        except OSError as exc:
            if str(exc) == "insufficient_disk_space":
                raise HTTPException(507, "Insufficient appliance disk space") from exc
            raise HTTPException(507, "Unable to persist print job") from exc
        except ValueError as exc:
            code = str(exc)
            if code == "idempotency_conflict":
                raise HTTPException(
                    409, "Idempotency-Key was already used for different request contents"
                ) from exc
            if code == "invalid_format_signature":
                raise HTTPException(422, "File signature does not match requested format") from exc
            if code in {"invalid_form_fields", "invalid_options", "format_not_supported"}:
                raise HTTPException(
                    422, "Invalid format, title, copies, or printer options"
                ) from exc
            if code == "invalid_idempotency_key":
                raise HTTPException(422, "Invalid Idempotency-Key") from exc
            raise HTTPException(422, "Invalid print request") from exc

    @app.get("/api/v1/jobs")
    def client_jobs(
        limit: int = 50, client: dict[str, str] = Depends(api_client)
    ) -> list[dict[str, Any]]:
        return appliance.client_jobs(client["id"], limit)

    @app.get("/api/v1/jobs/{job_id}")
    def client_job(job_id: str, client: dict[str, str] = Depends(api_client)) -> dict[str, Any]:
        job = appliance.client_job(client["id"], job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, client: dict[str, str] = Depends(api_client)) -> dict[str, Any]:
        try:
            job = appliance.cancel_client_job(client["id"], job_id)
        except BackendUnavailable as exc:
            raise HTTPException(
                503, "CUPS state cannot be verified; job is not reported canceled"
            ) from exc
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.post("/admin/api/login")
    def login(request: Request, response: Response, data: dict[str, Any]) -> dict[str, Any]:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}".rstrip(
            "/"
        ):
            raise HTTPException(403, "Cross-origin login rejected")
        password = data.get("password")
        if not isinstance(password, str) or not password or len(password) > 1024:
            raise HTTPException(401, "Invalid credentials")
        db = appliance.db()
        try:
            now = time.time()
            db.execute("BEGIN IMMEDIATE")
            guard = db.execute("SELECT * FROM login_guard WHERE id=1").fetchone()
            if guard["locked_until"] > now:
                raise HTTPException(429, "Login temporarily locked; try again later")
            admin = db.execute("SELECT * FROM admin WHERE id=1").fetchone()
            if not admin:
                raise HTTPException(503, "Bootstrap the admin password with the CLI")
            if not verify_password(password, admin["salt"], admin["password_hash"]):
                window = guard["window_started"]
                failures = guard["failures"]
                if now - window > 300:
                    window, failures = now, 0
                failures += 1
                locked_until = now + 300 if failures >= 5 else 0
                db.execute(
                    "UPDATE login_guard SET window_started=?,failures=?,locked_until=? WHERE id=1",
                    (window, failures, locked_until),
                )
                db.execute("COMMIT")
                raise HTTPException(401, "Invalid credentials")
            db.execute(
                "UPDATE login_guard SET window_started=0,failures=0,locked_until=0 WHERE id=1"
            )
            token, token_hash, csrf = make_session_values()
            expires = now + SESSION_SECONDS
            db.execute(
                "INSERT INTO sessions(token_hash,csrf_token,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, csrf, expires, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="strict",
            path="/",
        )
        return {"authenticated": True, "csrf_token": csrf, "expires_at": expires}

    @app.get("/admin/api/session")
    def session_status(request: Request) -> dict[str, Any]:
        session = get_session(appliance, request.cookies.get(COOKIE_NAME))
        if not session:
            return {"authenticated": False}
        return {"authenticated": True, "csrf_token": session["csrf_token"]}

    @app.post("/admin/api/logout")
    def logout(
        request: Request, response: Response, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, bool]:
        token = request.cookies.get(COOKIE_NAME)
        db = appliance.db()
        try:
            if token:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_session_token(token),))
        finally:
            db.close()
        response.delete_cookie(
            COOKIE_NAME, path="/", httponly=True, secure=settings.secure_cookie, samesite="strict"
        )
        return {"authenticated": False}

    @app.post("/admin/api/password")
    def change_password(
        response: Response,
        data: Any = Body(...),
        session: dict[str, str] = Depends(admin_mutation),
    ) -> dict[str, bool]:
        if not isinstance(data, dict):
            raise HTTPException(422, "Password change requires a JSON object")
        current = data.get("current_password")
        new = data.get("new_password")
        confirmation = data.get("confirm_password")
        # Validate manually so error responses never echo password values.
        if not isinstance(current, str) or not 1 <= len(current) <= 1024:
            raise HTTPException(422, "Current password is required (maximum 1024 characters)")
        if not isinstance(new, str) or not 12 <= len(new) <= 1024:
            raise HTTPException(422, "New password must contain 12 to 1024 characters")
        if not isinstance(confirmation, str) or confirmation != new:
            raise HTTPException(422, "New password confirmation does not match")
        if new == current:
            raise HTTPException(422, "New password must differ from the current password")
        db = appliance.db()
        try:
            now = time.time()
            db.execute("BEGIN IMMEDIATE")
            # Recheck under the write lock: another password change/reset may have
            # revoked this session after the dependency authenticated it.
            if not db.execute(
                "SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?",
                (session["token_hash"], now),
            ).fetchone():
                raise HTTPException(401, "Admin login required")
            guard = db.execute("SELECT * FROM login_guard WHERE id=1").fetchone()
            if guard["locked_until"] > now:
                raise HTTPException(429, "Password change temporarily locked; try again later")
            admin = db.execute("SELECT * FROM admin WHERE id=1").fetchone()
            if not admin:
                raise HTTPException(503, "Bootstrap the admin password with the CLI")
            if not verify_password(current, admin["salt"], admin["password_hash"]):
                # Share the login throttle so a stolen session cannot bypass it.
                window, failures = guard["window_started"], guard["failures"]
                if now - window > 300:
                    window, failures = now, 0
                failures += 1
                db.execute(
                    "UPDATE login_guard SET window_started=?,failures=?,locked_until=? WHERE id=1",
                    (window, failures, now + 300 if failures >= 5 else 0),
                )
                db.execute("COMMIT")
                # A wrong current password is not an expired session; keep the
                # signed-in form available while giving no credential details.
                raise HTTPException(400, "Current password is incorrect")
            salt = secrets.token_bytes(16)
            db.execute(
                "UPDATE admin SET salt=?,password_hash=?,changed_at=? WHERE id=1",
                (
                    salt.hex(),
                    password_hash(new, salt),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            db.execute("DELETE FROM sessions")
            db.execute(
                "UPDATE login_guard SET window_started=0,failures=0,locked_until=0 WHERE id=1"
            )
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()
        response.delete_cookie(
            COOKIE_NAME, path="/", httponly=True, secure=settings.secure_cookie, samesite="strict"
        )
        return {"authenticated": False}

    @app.get("/admin/api/status")
    def admin_status(_: dict[str, str] = Depends(admin_session)) -> dict[str, Any]:
        try:
            cups = appliance.backend.health()
        except BackendUnavailable:
            cups = {"available": False, "message": "Local CUPS is unavailable or not authorized"}
        disk = shutil.disk_usage(settings.data_dir)
        limits = appliance.limits()
        db = appliance.db()
        try:
            counts = {
                "queued": db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[
                    0
                ],
                "held": db.execute("SELECT COUNT(*) FROM jobs WHERE status='held'").fetchone()[0],
                "submitted": db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status='submitted'"
                ).fetchone()[0],
                "unknown": db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status='unknown'"
                ).fetchone()[0],
            }
            bootstrapped = db.execute("SELECT 1 FROM admin WHERE id=1").fetchone() is not None
        finally:
            db.close()
        return {
            "cups": cups,
            "disk": {"free_bytes": disk.free, "total_bytes": disk.total},
            "limits": limits,
            "jobs": counts,
            "admin_bootstrapped": bootstrapped,
        }

    @app.get("/admin/api/printers")
    def admin_printers(_: dict[str, str] = Depends(admin_session)) -> list[dict[str, Any]]:
        return appliance.admin_printers()

    @app.get("/admin/api/printers/{printer_id}/capabilities")
    def admin_printer_capabilities(
        printer_id: str, _: dict[str, str] = Depends(admin_session)
    ) -> dict[str, Any]:
        try:
            result = appliance.printer_capabilities(printer_id)
        except BackendUnavailable as exc:
            raise HTTPException(503, "Printer capabilities are unavailable") from exc
        if result is None:
            raise HTTPException(404, "Printer not found")
        return result

    @app.get("/admin/api/printers/{printer_id}")
    def admin_printer(
        printer_id: str, _: dict[str, str] = Depends(admin_session)
    ) -> dict[str, Any]:
        printer = appliance.admin_printer(printer_id)
        if printer is None:
            raise HTTPException(404, "Printer not found")
        return printer

    @app.get("/admin/api/printers/{printer_id}/api-key")
    def printer_api_key(
        printer_id: str, _: dict[str, str] = Depends(admin_session)
    ) -> Response:
        key = appliance.printer_api_key(printer_id)
        if key is None:
            raise HTTPException(404, "Printer not found")
        return JSONResponse({"id": printer_id, "api_key": key}, headers={"Cache-Control": "no-store"})

    @app.post("/admin/api/printers/{printer_id}/rotate-key")
    def rotate_printer_api_key(
        printer_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> Response:
        key = appliance.rotate_printer_api_key(printer_id)
        if key is None:
            raise HTTPException(404, "Printer not found")
        return JSONResponse({"id": printer_id, "api_key": key}, headers={"Cache-Control": "no-store"})

    @app.delete("/admin/api/printers/{printer_id}")
    def delete_printer(
        printer_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            deleted = appliance.delete_printer(printer_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "Printer not found")
        return {"id": printer_id, "deleted": True}

    @app.get("/admin/api/discovery")
    def discovery(_: dict[str, str] = Depends(admin_session)) -> dict[str, Any]:
        found = safe_backend(appliance.backend.discover)
        found["queues"] = safe_backend(appliance.backend.queues)
        return found

    @app.post("/admin/api/printers")
    def create_printer(
        data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            return appliance.create_managed_printer(
                name=data.get("name", ""),
                device_uri=data.get("device_uri", ""),
                driver=data.get("driver", ""),
                formats=body_json(data, "formats", ["pdf"]),
                defaults=body_json(data, "default_options", {}),
                allowed=body_json(data, "allowed_options", {}),
            )
        except BackendUnavailable as exc:
            raise HTTPException(503, "CUPS is unavailable or not authorized") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/admin/api/printers/import")
    def import_printer(
        data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            return appliance.import_queue(
                queue=str(data.get("queue", "")),
                name=str(data.get("name", "")),
                formats=body_json(data, "formats", ["pdf"]),
            )
        except BackendUnavailable as exc:
            raise HTTPException(503, "CUPS is unavailable or not authorized") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/admin/api/printers/{printer_id}")
    def edit_printer(
        printer_id: str, data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            result = appliance.edit_printer(
                printer_id,
                {
                    **data,
                    **{
                        key: body_json(data, key, data.get(key))
                        for key in ("formats", "default_options", "allowed_options")
                        if key in data
                    },
                },
            )
            if result is None:
                raise HTTPException(404, "Printer not found")
            return result
        except BackendUnavailable as exc:
            raise HTTPException(503, "CUPS is unavailable or not authorized") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/admin/api/printers/{printer_id}/pause")
    def pause_printer(
        printer_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            result = appliance.pause_printer(printer_id)
        except BackendUnavailable as exc:
            raise HTTPException(503, "CUPS did not confirm printer pause") from exc
        if result is None:
            raise HTTPException(404, "Printer not found")
        return result

    @app.post("/admin/api/printers/{printer_id}/resume")
    def resume_printer(
        printer_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            result = appliance.resume_printer(printer_id)
        except BackendUnavailable as exc:
            raise HTTPException(503, "CUPS did not confirm printer resume") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if result is None:
            raise HTTPException(404, "Printer not found")
        return result

    def client_record(db: Any, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "revoked": bool(row["revoked"]),
            "created_at": row["created_at"],
            "printer_ids": [
                item[0]
                for item in db.execute(
                    "SELECT cp.printer_id FROM client_printers cp "
                    "JOIN printers p ON p.id=cp.printer_id "
                    "WHERE cp.client_id=? AND p.deleted_at IS NULL ORDER BY cp.printer_id",
                    (row["id"],),
                )
            ],
        }

    @app.get("/admin/api/clients")
    def list_clients(_: dict[str, str] = Depends(admin_session)) -> list[dict[str, Any]]:
        db = appliance.db()
        try:
            rows = db.execute(
                "SELECT id,name,revoked,created_at FROM clients "
                "WHERE deleted_at IS NULL ORDER BY name"
            ).fetchall()
            return [client_record(db, row) for row in rows]
        finally:
            db.close()

    @app.get("/admin/api/clients/{client_id}")
    def get_client(client_id: str, _: dict[str, str] = Depends(admin_session)) -> dict[str, Any]:
        db = appliance.db()
        try:
            row = db.execute(
                "SELECT id,name,revoked,created_at FROM clients WHERE id=? AND deleted_at IS NULL",
                (client_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Client not found")
            return client_record(db, row)
        finally:
            db.close()

    @app.delete("/admin/api/clients/{client_id}")
    def delete_client(
        client_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            deleted = appliance.delete_client(client_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "Client not found")
        return {"id": client_id, "deleted": True}

    @app.post("/admin/api/clients")
    def create_client(
        data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        import uuid

        name = data.get("name")
        printer_ids = data.get("printer_ids", [])
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or not isinstance(printer_ids, list)
            or len(printer_ids) > 1000
            or any(not isinstance(item, str) or len(item) > 128 for item in printer_ids)
        ):
            raise HTTPException(422, "Client name and printer IDs are required")
        key = "pa_" + secrets.token_urlsafe(32)
        client_id = str(uuid.uuid4())
        db = appliance.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            for printer_id in set(printer_ids):
                if not db.execute(
                    "SELECT 1 FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                ).fetchone():
                    raise HTTPException(422, "Unknown printer ID")
            db.execute(
                "INSERT INTO clients(id,name,key_hash,created_at) VALUES(?,?,?,?)",
                (
                    client_id,
                    name.strip(),
                    hash_api_key(key),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            db.executemany(
                "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)",
                [(client_id, printer_id) for printer_id in set(printer_ids)],
            )
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        return {"id": client_id, "name": name.strip(), "api_key": key, "shown_once": True}

    @app.put("/admin/api/clients/{client_id}")
    def update_client(
        client_id: str, data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        if not data or set(data) - {"name", "printer_ids"}:
            raise HTTPException(422, "Provide name and/or printer_ids")
        name = data.get("name")
        if "name" in data and (not isinstance(name, str) or not name.strip() or len(name) > 120):
            raise HTTPException(422, "Invalid client name")
        ids = data.get("printer_ids")
        if "printer_ids" in data and (
            not isinstance(ids, list)
            or len(ids) > 1000
            or any(not isinstance(item, str) or len(item) > 128 for item in ids)
        ):
            raise HTTPException(422, "printer_ids must be an array")
        db = appliance.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            client = db.execute(
                "SELECT id FROM clients WHERE id=? AND deleted_at IS NULL", (client_id,)
            ).fetchone()
            if not client:
                raise HTTPException(404, "Client not found")
            if db.execute("SELECT 1 FROM printer_api_keys WHERE client_id=?", (client_id,)).fetchone():
                raise HTTPException(409, "Manage this API key on the printer")
            if "printer_ids" in data:
                for printer_id in set(ids):
                    if not db.execute(
                        "SELECT 1 FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                    ).fetchone():
                        raise HTTPException(422, "Unknown printer ID")
                db.execute("DELETE FROM client_printers WHERE client_id=?", (client_id,))
                db.executemany(
                    "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)",
                    [(client_id, printer_id) for printer_id in set(ids)],
                )
            if name is not None:
                db.execute("UPDATE clients SET name=? WHERE id=?", (name.strip(), client_id))
            row = db.execute(
                "SELECT id,name,revoked,created_at FROM clients WHERE id=?", (client_id,)
            ).fetchone()
            result = client_record(db, row)
            db.execute("COMMIT")
            return result
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @app.put("/admin/api/clients/{client_id}/printers")
    def grant_printers(
        client_id: str, data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        ids = data.get("printer_ids")
        if (
            not isinstance(ids, list)
            or len(ids) > 1000
            or any(not isinstance(item, str) or len(item) > 128 for item in ids)
        ):
            raise HTTPException(422, "printer_ids must be an array")
        db = appliance.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            client = db.execute(
                "SELECT id FROM clients WHERE id=? AND deleted_at IS NULL", (client_id,)
            ).fetchone()
            if not client:
                raise HTTPException(404, "Client not found")
            if db.execute("SELECT 1 FROM printer_api_keys WHERE client_id=?", (client_id,)).fetchone():
                raise HTTPException(409, "Manage this API key on the printer")
            for printer_id in set(ids):
                if not db.execute(
                    "SELECT 1 FROM printers WHERE id=? AND deleted_at IS NULL", (printer_id,)
                ).fetchone():
                    raise HTTPException(422, "Unknown printer ID")
            db.execute("DELETE FROM client_printers WHERE client_id=?", (client_id,))
            db.executemany(
                "INSERT INTO client_printers(client_id,printer_id) VALUES(?,?)",
                [(client_id, printer_id) for printer_id in set(ids)],
            )
            db.execute("COMMIT")
            return {"client_id": client_id, "printer_ids": sorted(set(ids))}
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def rotate_client_key(client_id: str, revoke: bool) -> dict[str, Any]:
        db = appliance.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM clients WHERE id=? AND deleted_at IS NULL", (client_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Client not found")
            if db.execute("SELECT 1 FROM printer_api_keys WHERE client_id=?", (client_id,)).fetchone():
                raise HTTPException(409, "Manage this API key on the printer")
            if revoke:
                db.execute("UPDATE clients SET revoked=1 WHERE id=?", (client_id,))
                result = {"id": client_id, "revoked": True}
            else:
                key = "pa_" + secrets.token_urlsafe(32)
                db.execute(
                    "UPDATE clients SET key_hash=?,revoked=0 WHERE id=? AND deleted_at IS NULL",
                    (hash_api_key(key), client_id),
                )
                result = {"id": client_id, "api_key": key, "shown_once": True}
            db.execute("COMMIT")
            return result
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()

    @app.post("/admin/api/clients/{client_id}/rotate-key")
    def rotate_key(client_id: str, _: dict[str, str] = Depends(admin_mutation)) -> dict[str, Any]:
        return rotate_client_key(client_id, False)

    @app.post("/admin/api/clients/{client_id}/revoke")
    def revoke_client(
        client_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        return rotate_client_key(client_id, True)

    @app.get("/admin/api/jobs")
    def admin_jobs(
        limit: int = 100, _: dict[str, str] = Depends(admin_session)
    ) -> list[dict[str, Any]]:
        return appliance.admin_jobs(limit)

    @app.get("/admin/api/jobs/{job_id}")
    def admin_job(job_id: str, _: dict[str, str] = Depends(admin_session)) -> dict[str, Any]:
        job = appliance.admin_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.post("/admin/api/jobs/{job_id}/cancel")
    def admin_cancel(job_id: str, _: dict[str, str] = Depends(admin_mutation)) -> dict[str, Any]:
        db = appliance.db()
        try:
            row = db.execute("SELECT client_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            db.close()
        if not row:
            raise HTTPException(404, "Job not found")
        try:
            result = appliance.cancel_client_job(row["client_id"], job_id)
        except BackendUnavailable as exc:
            raise HTTPException(
                503, "CUPS state cannot be verified; job is not reported canceled"
            ) from exc
        if not result:
            raise HTTPException(404, "Job not found")
        return result

    @app.post("/admin/api/jobs/{job_id}/resume")
    def admin_resume_job(
        job_id: str, _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            result = appliance.resume_job(job_id)
        except (BackendUnavailable, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "Job not found")
        return result

    @app.post("/admin/api/jobs/{job_id}/resolve")
    def resolve_job(
        job_id: str, data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, Any]:
        try:
            result = appliance.resolve_unknown(
                job_id, data.get("outcome", ""), data.get("reason", "")
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not result:
            raise HTTPException(404, "Job not found")
        return result

    @app.get("/admin/api/settings")
    def get_settings(_: dict[str, str] = Depends(admin_session)) -> dict[str, int]:
        return appliance.limits()

    @app.put("/admin/api/settings")
    def update_settings(
        data: dict[str, Any], _: dict[str, str] = Depends(admin_mutation)
    ) -> dict[str, int]:
        ranges = {
            "max_upload_bytes": (64 * 1024, 512 * 1024 * 1024),
            "max_pending_jobs": (1, 10_000),
            "min_free_bytes": (0, 100 * 1024 * 1024 * 1024),
            "history_retention_days": (1, 3650),
        }
        if set(data) - set(ranges):
            raise HTTPException(422, "Unsupported settings key")
        values = appliance.limits()
        for key, value in data.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not ranges[key][0] <= value <= ranges[key][1]
            ):
                raise HTTPException(422, f"Invalid value for {key}")
            values[key] = value
        db = appliance.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            for key, value in data.items():
                store.save_setting(db, key, value)
            db.execute("COMMIT")
        finally:
            db.close()
        return values

    return app
