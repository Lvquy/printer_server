from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class BackendUnavailable(RuntimeError):
    """The local CUPS service or its Python binding cannot be reached."""


class CupsBackend(Protocol):
    def health(self) -> dict[str, Any]: ...
    def discover(self) -> dict[str, list[dict[str, str]]]: ...
    def queues(self) -> dict[str, dict[str, Any]]: ...
    def queue_mapping(self, queue: str) -> dict[str, str] | None: ...
    def printer_capabilities(self, queue: str) -> dict[str, Any]: ...
    def printer_status(self, queue: str) -> dict[str, Any]: ...
    def create_queue(self, queue: str, device_uri: str, driver: str) -> None: ...
    def update_queue(self, queue: str, device_uri: str, driver: str) -> None: ...
    def pause_printer(self, queue: str) -> None: ...
    def resume_printer(self, queue: str) -> None: ...
    def submit_held(
        self, queue: str, filename: str, title: str, options: dict[str, str], correlation: str
    ) -> int: ...
    def find_job(self, correlation: str) -> dict[str, Any] | None: ...
    def get_job(self, job_id: int) -> dict[str, Any] | None: ...
    def release_job(self, job_id: int) -> None: ...
    def cancel_job(self, job_id: int) -> None: ...


class PyCupsBackend:
    """Thin, lazy adapter around pycups; connection defaults to local CUPS/UNIX socket."""

    def _connection(self) -> Any:
        try:
            import cups  # type: ignore[import-not-found]

            socket_path = os.environ.get("PRINT_APPLIANCE_CUPS_SOCKET", "/run/cups/cups.sock")
            if not socket_path.startswith("/"):
                raise ValueError("CUPS must use a local Unix socket")
            return cups.Connection(host=socket_path)
        except Exception as exc:
            raise BackendUnavailable("Local CUPS is unavailable or not authorized") from exc

    def health(self) -> dict[str, Any]:
        connection = self._connection()
        try:
            queues = connection.getPrinters()
            return {"available": True, "queue_count": len(queues), "message": "CUPS connected"}
        except Exception as exc:
            raise BackendUnavailable("Unable to query local CUPS") from exc

    def discover(self) -> dict[str, list[dict[str, str]]]:
        connection = self._connection()
        try:
            devices = connection.getDevices(
                include_schemes=["usb", "ipp", "ipps", "socket", "lpd", "dnssd"]
            )
            ppds = connection.getPPDs()
            return {
                "devices": [
                    {
                        "uri": str(uri),
                        "info": str(item.get("device-info", "")),
                        "make_model": str(item.get("device-make-and-model", "")),
                    }
                    for uri, item in sorted(devices.items())
                ],
                "drivers": [
                    {"id": str(name), "make_model": str(item.get("ppd-make-and-model", name))}
                    for name, item in sorted(ppds.items())
                ],
            }
        except Exception as exc:
            raise BackendUnavailable("Unable to discover CUPS devices and drivers") from exc

    def queues(self) -> dict[str, dict[str, Any]]:
        try:
            return {
                str(name): dict(attrs) for name, attrs in self._connection().getPrinters().items()
            }
        except Exception as exc:
            raise BackendUnavailable("Unable to list CUPS queues") from exc

    def queue_mapping(self, queue: str) -> dict[str, str] | None:
        connection = self._connection()
        try:
            attrs = connection.getPrinters().get(queue)
            if attrs is None:
                return None
            device_uri = str(attrs.get("device-uri", ""))
            policy_attrs = connection.getPrinterAttributes(
                queue, requested_attributes=["printer-error-policy"]
            )
            error_policy = str(policy_attrs.get("printer-error-policy", "unknown"))
            driver_source: dict[str, Any] = {
                "ppd_name": attrs.get("printer-ppd-name"),
                "driver_name": attrs.get("printer-driver-name"),
                "make_model": attrs.get("printer-make-and-model"),
                "printer_type": attrs.get("printer-type"),
            }
            ppd_path = None
            try:
                ppd_path = connection.getPPD(queue)
                if ppd_path:
                    path = Path(str(ppd_path))
                    driver_source = {"ppd_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            except Exception as exc:
                # Raw queues deliberately have no PPD. Other queues must provide a fingerprint;
                # model names alone cannot distinguish two drivers with the same display name.
                if "raw" not in str(attrs.get("printer-make-and-model", "")).lower():
                    raise BackendUnavailable(
                        "Cannot verify this queue's driver fingerprint"
                    ) from exc
            finally:
                if ppd_path:
                    Path(str(ppd_path)).unlink(missing_ok=True)
            signature = hashlib.sha256(
                json.dumps(
                    driver_source, sort_keys=True, default=str, separators=(",", ":")
                ).encode()
            ).hexdigest()
            return {
                "device_uri": device_uri,
                "mapping_signature": signature,
                "error_policy": error_policy,
            }
        except Exception as exc:
            raise BackendUnavailable("Unable to inspect CUPS queue mapping") from exc

    @staticmethod
    def _safe_ppd_text(value: Any, limit: int = 160) -> str:
        text = " ".join(str(value or "").split())
        return "".join(char for char in text if char.isprintable())[:limit]

    @classmethod
    def _safe_ipp_attributes(cls, values: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for name in names:
            value = values.get(name)
            if isinstance(value, (tuple, list)):
                safe[name] = [cls._safe_ppd_text(item, 128) for item in list(value)[:64]]
            elif isinstance(value, (str, int, bool)):
                safe[name] = cls._safe_ppd_text(value, 128) if isinstance(value, str) else value
        return safe

    @staticmethod
    def _ppd_constraints(data: bytes, ppd: Any) -> list[dict[str, str]]:
        # Some distro libcups builds omit legacy constraints from pycups getters.
        # Parse only this bounded, declarative pair syntax, not executable PPD code.
        pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        declarations = []
        for line in data.decode("latin-1").splitlines():
            if line.startswith(("*cupsUIConstraints:", "*cupsUIConstraints ", "*cupsUIResolver")):
                raise BackendUnavailable("Driver constraint resolvers are not supported")
            if line.startswith("*UIConstraints:"):
                value = line.split(":", 1)[1].strip().strip('"')
                match = re.fullmatch(
                    r"\*([A-Za-z][A-Za-z0-9_-]{0,63})\s+([^\s*]+)\s+\*([A-Za-z][A-Za-z0-9_-]{0,63})\s+([^\s*]+)",
                    value,
                )
                if not match:
                    raise BackendUnavailable("Driver has unsupported constraint syntax")
                declarations.append(match.groups())
                if len(declarations) > 1024:
                    raise BackendUnavailable("Driver has too many constraint declarations")
        if not declarations:
            for c in getattr(ppd, "constraints", []):
                declarations.append(
                    tuple(
                        str(getattr(c, key, ""))
                        for key in ("option1", "choice1", "option2", "choice2")
                    )
                )
        for left, first, right, second in declarations:
            if (
                not all((left, first, right, second))
                or max(map(len, (left, first, right, second))) > 128
            ):
                raise BackendUnavailable("Driver has unsupported constraint syntax")
            left = "PageSize" if left == "PageRegion" else left
            right = "PageSize" if right == "PageRegion" else right
            pair = tuple(sorted(((left, first), (right, second))))
            pairs.add(pair)
            if len(pairs) > 128:
                raise BackendUnavailable("Driver has too many unique constraints")
        return [
            {"option1": a, "choice1": b, "option2": c, "choice2": d}
            for (a, b), (c, d) in sorted(pairs)
        ]

    def printer_capabilities(self, queue: str) -> dict[str, Any]:
        connection = self._connection()
        ipp_names = (
            "sides-supported",
            "sides-default",
            "color-supported",
            "print-color-mode-supported",
            "print-color-mode-default",
            "media-supported",
            "media-default",
            "print-quality-supported",
            "print-scaling-supported",
            "print-scaling-default",
        )
        try:
            attrs = connection.getPrinters().get(queue)
            if attrs is None:
                raise BackendUnavailable("Registered CUPS queue no longer exists")
            ipp = self._safe_ipp_attributes(
                connection.getPrinterAttributes(queue, requested_attributes=list(ipp_names)),
                ipp_names,
            )
        except BackendUnavailable:
            raise
        except Exception as exc:
            raise BackendUnavailable("Unable to query printer capabilities") from exc
        raw = "raw" in str(attrs.get("printer-make-and-model", "")).lower()
        try:
            ppd_path = connection.getPPD(queue)
        except Exception:
            ppd_path = None
        result: list[dict[str, Any]] = []
        constraints: list[dict[str, str]] = []
        ppd_hash = None
        if ppd_path:
            path = Path(str(ppd_path))
            try:
                with path.open("rb") as file:
                    data = file.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise BackendUnavailable("Driver PPD exceeds the supported size")
                import cups  # type: ignore[import-not-found]

                ppd = cups.PPD(str(path))
                common = {
                    "PageSize",
                    "Duplex",
                    "Resolution",
                    "MediaType",
                    "InputSlot",
                    "ColorModel",
                    "ColorMode",
                }
                groups = list(ppd.optionGroups)
                if len(groups) > 24:
                    raise BackendUnavailable("Driver has too many option groups")
                for group in groups:
                    subgroups = list(getattr(group, "subgroups", []))
                    if len(subgroups) > 16:
                        raise BackendUnavailable("Driver has too many subgroups")
                    for parent in [group, *subgroups]:
                        for option in parent.options:
                            name = str(option.keyword)
                            if name == "PageRegion":
                                continue  # derived PageSize alias; never submit competing defaults
                            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
                                raise BackendUnavailable("Invalid driver option name")
                            choices = list(option.choices)
                            if len(choices) > 64:
                                raise BackendUnavailable("Driver has too many option choices")
                            values = []
                            for choice in choices:
                                value = str(
                                    choice.get("choice", "")
                                    if isinstance(choice, Mapping)
                                    else getattr(choice, "choice", "")
                                )
                                label = (
                                    choice.get("text", "")
                                    if isinstance(choice, Mapping)
                                    else getattr(choice, "text", "")
                                )
                                if not value or len(value) > 128 or any(ord(c) < 32 for c in value):
                                    raise BackendUnavailable("Invalid driver option choice")
                                values.append(
                                    {"value": value, "label": self._safe_ppd_text(label) or value}
                                )
                            if values:
                                result.append(
                                    {
                                        "name": name,
                                        "label": self._safe_ppd_text(option.text) or name,
                                        "group": "common" if name in common else "advanced",
                                        "group_label": self._safe_ppd_text(parent.text),
                                        "default": str(option.defchoice),
                                        "choices": values,
                                        "formats": ["pdf"],
                                    }
                                )
                            if len(result) > 128:
                                raise BackendUnavailable("Driver has too many options")
                constraints = self._ppd_constraints(data, ppd)
                ppd_hash = hashlib.sha256(data).hexdigest()
            except BackendUnavailable:
                raise
            except Exception as exc:
                raise BackendUnavailable("Unable to parse the CUPS PPD capabilities") from exc
            finally:
                path.unlink(missing_ok=True)
        # Scaling is a PDF filter capability, only advertised when CUPS reports it.
        if not raw and ipp.get("print-scaling-supported"):
            values = [
                v
                for v in ipp["print-scaling-supported"]
                if v in {"auto", "auto-fit", "fit", "fill", "none"}
            ]
            if values:
                result.append(
                    {
                        "name": "print-scaling",
                        "label": "PDF scaling",
                        "group": "common",
                        "default": ipp.get("print-scaling-default", ""),
                        "choices": [{"value": v, "label": v} for v in values],
                        "formats": ["pdf"],
                    }
                )
        return {
            "source": "ppd" if ppd_hash else "ipp" if ipp else "unknown",
            "raw": raw,
            "availability": "available" if ppd_hash else "partial" if ipp or raw else "unknown",
            "reason": None
            if ppd_hash
            else "No PPD: only explicitly reported IPP capabilities are known; driver constraints are unavailable.",
            "options": result,
            "constraints": constraints,
            "ipp_attributes": ipp,
            "mapping_fingerprint": hashlib.sha256(
                json.dumps({"ppd": ppd_hash, "ipp": ipp}, sort_keys=True).encode()
            ).hexdigest(),
        }

    def _job_connection(self) -> Any:
        connection = self._connection()
        try:
            # Read-only request establishes local certificate auth on THIS connection.
            # Without a challenge CUPS hides private job-name, even for the owner.
            connection.adminGetServerSettings()
        except Exception as exc:
            raise BackendUnavailable("Unable to authenticate CUPS job metadata access") from exc
        return connection

    def printer_status(self, queue: str) -> dict[str, Any]:
        try:
            attrs = self._connection().getPrinters().get(queue)
            if attrs is None:
                return {"known": False, "online": False, "message": "CUPS queue missing"}
            state = int(attrs.get("printer-state", 0))
            reasons = attrs.get("printer-state-reasons", [])
            reasons = [str(item) for item in reasons] if isinstance(reasons, (tuple, list)) else []
            offline = state == 5 or any(
                word in reason.lower()
                for reason in reasons
                for word in ("offline", "media-empty", "door-open", "paused", "error")
            )
            return {
                "known": True,
                "online": not offline,
                "state": state,
                "reasons": reasons,
                "message": str(attrs.get("printer-state-message", "")),
            }
        except Exception as exc:
            raise BackendUnavailable("Unable to query CUPS printer state") from exc

    def create_queue(self, queue: str, device_uri: str, driver: str) -> None:
        connection = self._connection()
        try:
            if queue in connection.getPrinters():
                raise ValueError("CUPS queue already exists")
            connection.addPrinter(queue, device=device_uri, ppdname=driver)
            connection.setPrinterErrorPolicy(queue, "stop-printer")
            connection.acceptJobs(queue)
            connection.enablePrinter(queue)
        except ValueError:
            raise
        except Exception as exc:
            raise BackendUnavailable("Unable to create the managed CUPS queue") from exc

    def update_queue(self, queue: str, device_uri: str, driver: str) -> None:
        connection = self._connection()
        try:
            if queue not in connection.getPrinters():
                raise ValueError("Managed CUPS queue no longer exists")
            connection.addPrinter(queue, device=device_uri, ppdname=driver)
            connection.setPrinterErrorPolicy(queue, "stop-printer")
        except ValueError:
            raise
        except Exception as exc:
            raise BackendUnavailable("Unable to update the managed CUPS queue") from exc

    def pause_printer(self, queue: str) -> None:
        try:
            self._connection().disablePrinter(queue)
        except Exception as exc:
            raise BackendUnavailable("Unable to pause the CUPS queue") from exc

    def resume_printer(self, queue: str) -> None:
        try:
            self._connection().enablePrinter(queue)
        except Exception as exc:
            raise BackendUnavailable("Unable to resume the CUPS queue") from exc

    def submit_held(
        self, queue: str, filename: str, title: str, options: dict[str, str], correlation: str
    ) -> int:
        try:
            safe_options = {str(key): str(value) for key, value in options.items()}
            safe_options.update({"job-hold-until": "indefinite", "job-name": correlation})
            return int(self._connection().printFile(queue, filename, correlation, safe_options))
        except Exception as exc:
            raise BackendUnavailable("CUPS submission outcome requires reconciliation") from exc

    def find_job(self, correlation: str) -> dict[str, Any] | None:
        try:
            jobs = self._job_connection().getJobs(
                which_jobs="all",
                my_jobs=False,
                requested_attributes=["job-id", "job-name", "job-state", "job-state-reasons"],
            )
            matches = [
                (job_id, attrs)
                for job_id, attrs in jobs.items()
                if attrs.get("job-name") == correlation
            ]
            if len(matches) > 1:
                raise BackendUnavailable("Multiple CUPS jobs match the handoff correlation")
            if matches:
                job_id, attrs = matches[0]
                return self._job_result(int(job_id), attrs)
            return None
        except Exception as exc:
            raise BackendUnavailable("Unable to reconcile CUPS job correlation") from exc

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        try:
            attrs = self._job_connection().getJobAttributes(job_id)
            return self._job_result(job_id, attrs)
        except Exception as exc:
            # CUPS may have purged history; absence is not proof that submission never happened.
            if (
                exc.__class__.__name__ == "IPPError"
                and exc.args
                and exc.args[0] in {0x0406, 0x0407}
            ):
                return None
            raise BackendUnavailable("Unable to query CUPS job state") from exc

    @staticmethod
    def _job_result(job_id: int, attrs: dict[str, Any]) -> dict[str, Any]:
        states = {
            3: "pending",
            4: "held",
            5: "processing",
            6: "stopped",
            7: "canceled",
            8: "aborted",
            9: "completed",
        }
        state_num = int(attrs.get("job-state", 0))
        return {
            "id": job_id,
            "correlation": str(attrs.get("job-name", "")),
            "state": states.get(state_num, "unknown"),
            "state_number": state_num,
            "reasons": attrs.get("job-state-reasons", []),
        }

    def release_job(self, job_id: int) -> None:
        try:
            self._connection().setJobHoldUntil(job_id, "no-hold")
        except Exception as exc:
            raise BackendUnavailable("Unable to release the held CUPS job") from exc

    def cancel_job(self, job_id: int) -> None:
        try:
            # cancelJob returns None; callers must query job state for evidence.
            self._connection().cancelJob(job_id, purge_job=False)
        except Exception as exc:
            raise BackendUnavailable("Unable to request CUPS job cancellation") from exc
