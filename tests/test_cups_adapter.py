from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from print_appliance.cups import BackendUnavailable, PyCupsBackend


class FakeConnection:
    def __init__(self) -> None:
        self.printers: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.jobs: dict[int, dict[str, Any]] = {}
        self.ppds = {"drv://test.ppd": {"ppd-make-and-model": "Test"}}
        self.devices = {"ipp://printer.local/ipp/print": {"device-info": "Test printer"}}

    def getPrinters(self):
        return self.printers

    def getDevices(self, **kwargs):
        self.calls.append(("getDevices", kwargs))
        return self.devices

    def getPPDs(self):
        return self.ppds

    def addPrinter(self, *args, **kwargs):
        self.calls.append(("addPrinter", args, kwargs))
        self.printers[args[0]] = {
            "device-uri": kwargs["device"],
            "printer-ppd-name": kwargs["ppdname"],
        }

    def setPrinterErrorPolicy(self, *args):
        self.calls.append(("setPrinterErrorPolicy", *args))

    def acceptJobs(self, *args):
        self.calls.append(("acceptJobs", *args))

    def disablePrinter(self, *args):
        self.calls.append(("disablePrinter", *args))

    def enablePrinter(self, *args):
        self.calls.append(("enablePrinter", *args))

    def printFile(self, *args):
        self.calls.append(("printFile", *args))
        self.jobs[4] = {"job-name": args[3]["job-name"], "job-state": 4}
        return 4

    def adminGetServerSettings(self):
        self.calls.append(("authenticate",))
        return {}

    def getJobs(self, **kwargs):
        assert self.calls[-1] == ("authenticate",)
        self.calls.append(("getJobs", kwargs))
        return dict(self.jobs)

    def getJobAttributes(self, job_id):
        assert self.calls[-1] == ("authenticate",)
        return self.jobs[job_id]

    def setJobHoldUntil(self, *args):
        self.calls.append(("setJobHoldUntil", *args))

    def cancelJob(self, *args, **kwargs):
        self.calls.append(("cancelJob", *args, kwargs))
        return None


def install_fake_cups(monkeypatch: pytest.MonkeyPatch) -> FakeConnection:
    connection = FakeConnection()
    module = SimpleNamespace(Connection=lambda **kwargs: connection)
    monkeypatch.setitem(sys.modules, "cups", module)
    return connection


def test_managed_queue_uses_discovered_driver_and_stop_printer_policy(monkeypatch) -> None:
    connection = install_fake_cups(monkeypatch)
    backend = PyCupsBackend()
    found = backend.discover()
    assert found["devices"][0]["uri"] == "ipp://printer.local/ipp/print"
    assert found["drivers"][0]["id"] == "drv://test.ppd"
    backend.create_queue(
        "pa_1234567890abcdef", found["devices"][0]["uri"], found["drivers"][0]["id"]
    )
    add = next(call for call in connection.calls if call[0] == "addPrinter")
    assert add[2]["ppdname"] == "drv://test.ppd"
    assert "ppd" not in add[2]
    assert ("setPrinterErrorPolicy", "pa_1234567890abcdef", "stop-printer") in connection.calls


def test_queue_collision_never_takes_over_existing_queue(monkeypatch) -> None:
    connection = install_fake_cups(monkeypatch)
    connection.printers["BROTHER_MFC"] = {"device-uri": "ipp://old-printer/print"}
    backend = PyCupsBackend()
    with pytest.raises(ValueError, match="already exists"):
        backend.create_queue("BROTHER_MFC", "ipp://new-device/print", "drv://new.ppd")
    assert not any(call[0] == "addPrinter" for call in connection.calls)


def test_release_uses_set_job_hold_until_and_cancel_return_is_not_evidence(monkeypatch) -> None:
    connection = install_fake_cups(monkeypatch)
    backend = PyCupsBackend()
    job_id = backend.submit_held(
        "pa_queue", "/spool/private", "title", {"copies": "2"}, "pa-job-id"
    )
    print_call = next(call for call in connection.calls if call[0] == "printFile")
    options = print_call[4]
    assert options["job-hold-until"] == "indefinite"
    assert options["job-name"] == "pa-job-id"
    assert print_call[3] == "pa-job-id"
    assert options["copies"] == "2"
    backend.release_job(job_id)
    assert ("setJobHoldUntil", 4, "no-hold") in connection.calls
    result = backend.cancel_job(job_id)
    assert result is None
    assert any(call[0] == "cancelJob" for call in connection.calls)


def test_reconciliation_queries_all_jobs_and_maps_ipp_states(monkeypatch) -> None:
    connection = install_fake_cups(monkeypatch)
    backend = PyCupsBackend()
    found = backend.find_job("pa-job-id")  # No matching fake record is a missing-history result.
    assert found is None
    connection.jobs[4] = {
        "job-name": "pa-job-id",
        "job-state": 4,
        "job-state-reasons": ["job-hold-until-specified"],
    }
    connection.jobs[4]["job-name"] = "pa-job-id"
    result = backend.find_job("pa-job-id")
    assert result and result["state"] == "held"
    call = next(call for call in connection.calls if call[0] == "getJobs")
    assert call[1]["which_jobs"] == "all"
    assert call[1]["my_jobs"] is False
    assert "job-name" in call[1]["requested_attributes"]
    connection.jobs[4]["job-state"] = 9
    assert backend.get_job(4)["state"] == "completed"


def test_missing_cups_binding_is_explicit_not_fake_success(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "cups", None)
    backend = PyCupsBackend()
    with pytest.raises(BackendUnavailable):
        backend.health()


@pytest.mark.parametrize(
    "code,missing", [(0x0406, True), (0x0407, True), (0x0401, False), (0x0502, False)]
)
def test_only_missing_history_codes_mean_absent_job(monkeypatch, code, missing):
    connection = install_fake_cups(monkeypatch)

    class IPPError(Exception):
        pass

    def fail(job_id):
        raise IPPError(code, "CUPS test error")

    connection.getJobAttributes = fail
    backend = PyCupsBackend()
    if missing:
        assert backend.get_job(1) is None
    else:
        with pytest.raises(BackendUnavailable):
            backend.get_job(1)


def test_ppd_content_not_model_name_determines_fingerprint(monkeypatch, tmp_path):
    connection = install_fake_cups(monkeypatch)
    connection.printers["pa_test"] = {
        "device-uri": "usb://test/printer",
        "printer-make-and-model": "Same display model",
    }
    connection.getPrinterAttributes = lambda *args, **kwargs: {
        "printer-error-policy": "stop-printer"
    }
    content = [b"*PPD-Adobe: driver-version-one"]

    def ppd(queue):
        p = tmp_path / "temporary.ppd"
        p.write_bytes(content[0])
        return str(p)

    connection.getPPD = ppd
    backend = PyCupsBackend()
    one = backend.queue_mapping("pa_test")
    content[0] = b"*PPD-Adobe: driver-version-two"
    two = backend.queue_mapping("pa_test")
    assert one["device_uri"] == two["device_uri"]
    assert one["mapping_signature"] != two["mapping_signature"]
    assert not (tmp_path / "temporary.ppd").exists()


def test_job_identity_read_fails_closed_without_local_auth(monkeypatch):
    connection = install_fake_cups(monkeypatch)
    connection.jobs[4] = {"job-name": "pa-job-id", "job-state": 9}

    def denied():
        raise PermissionError("local auth denied")

    connection.adminGetServerSettings = denied
    backend = PyCupsBackend()
    with pytest.raises(BackendUnavailable):
        backend.get_job(4)
    with pytest.raises(BackendUnavailable):
        backend.find_job("pa-job-id")
    assert not any(call[0] == "getJobs" for call in connection.calls)
