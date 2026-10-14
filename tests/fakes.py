from __future__ import annotations

from typing import Any

from print_appliance.cups import BackendUnavailable


class FakeCups:
    """Explicit test-only adapter. The production CLI never selects this backend."""

    def __init__(self) -> None:
        self.device_uri = "usb://test/printer0"
        self.driver = "drv:///test/test.ppd"
        self.available = True
        self.online: dict[str, bool] = {}
        self.disabled: set[str] = set()
        self.queue_data: dict[str, dict[str, Any]] = {}
        self.jobs: dict[int, dict[str, Any]] = {}
        self.correlations: dict[str, int] = {}
        self.submit_calls: list[tuple[str, str, str, dict[str, str], str]] = []
        self.release_calls: list[int] = []
        self.cancel_calls: list[int] = []
        self.next_id = 1
        self.submit_error = False
        self.release_error = False
        self.cancel_confirms = True
        self.capability_data: dict[str, Any] = {
            "source": "ppd",
            "availability": "available",
            "reason": None,
            "mapping_fingerprint": "fake-ppd-v1",
            "options": [
                {
                    "name": "Duplex",
                    "label": "Duplex",
                    "group": "common",
                    "group_label": "General",
                    "default": "None",
                    "choices": [
                        {"value": "None", "label": "Off"},
                        {"value": "DuplexNoTumble", "label": "Long edge"},
                        {"value": "DuplexTumble", "label": "Short edge"},
                    ],
                },
                {
                    "name": "PageSize",
                    "label": "Paper",
                    "group": "common",
                    "group_label": "General",
                    "default": "A4",
                    "choices": [
                        {"value": "A4", "label": "A4"},
                        {"value": "Letter", "label": "Letter"},
                    ],
                },
                {
                    "name": "BindEdge",
                    "label": "Binding",
                    "group": "advanced",
                    "group_label": "Finishing",
                    "default": "None",
                    "choices": [
                        {"value": "None", "label": "None"},
                        {"value": "Left", "label": "Left"},
                    ],
                },
            ],
            "constraints": [
                {
                    "option1": "Duplex",
                    "choice1": "DuplexTumble",
                    "option2": "BindEdge",
                    "choice2": "Left",
                }
            ],
            "ipp_attributes": {},
        }

    def _check(self) -> None:
        if not self.available:
            raise BackendUnavailable("fake CUPS offline")

    def health(self) -> dict[str, Any]:
        self._check()
        return {
            "available": True,
            "queue_count": len(self.queue_data),
            "message": "Fake backend (test only)",
        }

    def discover(self) -> dict[str, list[dict[str, str]]]:
        self._check()
        return {
            "devices": [{"uri": self.device_uri, "info": "Test device", "make_model": "Test"}],
            "drivers": [{"id": self.driver, "make_model": "Test PPD"}],
        }

    def queues(self) -> dict[str, dict[str, Any]]:
        self._check()
        return {key: dict(value) for key, value in self.queue_data.items()}

    def queue_mapping(self, queue: str) -> dict[str, str] | None:
        self._check()
        attrs = self.queue_data.get(queue)
        if attrs is None:
            return None
        return {
            "device_uri": attrs["device-uri"],
            "mapping_signature": attrs["printer-ppd-name"],
            "error_policy": attrs.get("error-policy", "unknown"),
        }

    def printer_capabilities(self, queue: str) -> dict[str, Any]:
        self._check()
        return dict(self.capability_data)

    def printer_status(self, queue: str) -> dict[str, Any]:
        self._check()
        known = queue in self.queue_data
        online = self.online.get(queue, True) and queue not in self.disabled
        return {
            "known": known,
            "online": known and online,
            "message": "test offline" if not online else "",
        }

    def create_queue(self, queue: str, device_uri: str, driver: str) -> None:
        self._check()
        if queue in self.queue_data:
            raise ValueError("queue collision")
        self.queue_data[queue] = {
            "device-uri": device_uri,
            "printer-ppd-name": driver,
            "printer-make-and-model": "Test PPD",
            "error-policy": "stop-printer",
        }
        self.online[queue] = True

    def update_queue(self, queue: str, device_uri: str, driver: str) -> None:
        self._check()
        if queue not in self.queue_data:
            raise ValueError("missing queue")
        self.queue_data[queue]["device-uri"] = device_uri
        self.queue_data[queue]["printer-ppd-name"] = driver

    def add_existing_queue(self, name: str, device_uri: str | None = None) -> None:
        self.queue_data[name] = {
            "device-uri": device_uri or self.device_uri,
            "printer-ppd-name": "legacy-driver",
            "printer-make-and-model": "Imported Test Printer",
        }
        self.online[name] = True

    def pause_printer(self, queue: str) -> None:
        self._check()
        if queue not in self.queue_data:
            raise BackendUnavailable("unknown queue")
        self.disabled.add(queue)

    def resume_printer(self, queue: str) -> None:
        self._check()
        self.disabled.discard(queue)

    def submit_held(
        self, queue: str, filename: str, title: str, options: dict[str, str], correlation: str
    ) -> int:
        self._check()
        if self.submit_error:
            raise BackendUnavailable("uncertain submit")
        job_id = self.next_id
        self.next_id += 1
        self.jobs[job_id] = {"id": job_id, "state": "held", "correlation": correlation}
        self.correlations[correlation] = job_id
        self.submit_calls.append((queue, filename, title, dict(options), correlation))
        return job_id

    def find_job(self, correlation: str) -> dict[str, Any] | None:
        self._check()
        job_id = self.correlations.get(correlation)
        return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        self._check()
        job = self.jobs.get(job_id)
        return dict(job) if job is not None else None

    def release_job(self, job_id: int) -> None:
        self._check()
        if self.release_error:
            raise BackendUnavailable("release uncertain")
        if job_id not in self.jobs:
            raise BackendUnavailable("job missing")
        self.release_calls.append(job_id)
        self.jobs[job_id]["state"] = "processing"

    def cancel_job(self, job_id: int) -> None:
        self._check()
        self.cancel_calls.append(job_id)
        if self.cancel_confirms and job_id in self.jobs:
            self.jobs[job_id]["state"] = "canceled"

    def set_state(self, job_id: int, state: str) -> None:
        self.jobs[job_id]["state"] = state
