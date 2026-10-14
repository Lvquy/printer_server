from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from print_appliance.cups import BackendUnavailable, PyCupsBackend


class FakeConnection:
    def __init__(self, path: Path):
        self.path = path

    def getPrinters(self):
        return {"queue": {"printer-make-and-model": "Example laser", "printer-ppd-name": "example"}}

    def getPrinterAttributes(self, queue, requested_attributes):
        return {"sides-supported": ["one-sided", "two-sided-long-edge"]}

    def getPPD(self, queue):
        return str(self.path)


def test_py_cups_ppd_capabilities_are_bounded_and_temp_ppd_is_unlinked(monkeypatch):
    path = Path(tempfile.mktemp(suffix=".ppd"))
    path.write_bytes(b"ppd fixture")

    def choice(name):
        # pycups 2.0.1 exposes PPD option choices as dictionaries.
        return {"choice": name, "text": name, "marked": False}

    option = SimpleNamespace(
        keyword="Duplex",
        text="Duplex",
        defchoice="None",
        choices=[choice("None"), choice("DuplexTumble")],
    )
    group = SimpleNamespace(name="General", text="General", options=[option], subgroups=[])
    ppd = SimpleNamespace(optionGroups=[group], constraints=[])
    monkeypatch.setitem(sys.modules, "cups", SimpleNamespace(PPD=lambda filename: ppd))
    backend = PyCupsBackend()
    monkeypatch.setattr(backend, "_connection", lambda: FakeConnection(path))

    result = backend.printer_capabilities("queue")

    assert result["source"] == "ppd"
    assert result["options"][0]["choices"][1]["value"] == "DuplexTumble"
    assert result["ipp_attributes"]["sides-supported"] == ["one-sided", "two-sided-long-edge"]
    assert not path.exists()


def test_py_cups_ppd_is_unlinked_when_parser_fails(monkeypatch):
    path = Path(tempfile.mktemp(suffix=".ppd"))
    path.write_bytes(b"bad ppd")
    monkeypatch.setitem(
        sys.modules,
        "cups",
        SimpleNamespace(PPD=lambda filename: (_ for _ in ()).throw(ValueError("bad PPD"))),
    )
    backend = PyCupsBackend()
    monkeypatch.setattr(backend, "_connection", lambda: FakeConnection(path))

    with pytest.raises(BackendUnavailable):
        backend.printer_capabilities("queue")
    assert not path.exists()


def test_legacy_constraints_are_read_even_when_binding_omits_them():
    declarations = b"*UIConstraints: *Duplex DuplexTumble *BindEdge Left\n*UIConstraints: *BindEdge Left *Duplex DuplexTumble\n"
    result = PyCupsBackend._ppd_constraints(declarations, SimpleNamespace(constraints=[]))
    assert len(result) == 1
    assert result[0] == {
        "option1": "BindEdge",
        "choice1": "Left",
        "option2": "Duplex",
        "choice2": "DuplexTumble",
    }


@pytest.mark.parametrize(
    "data",
    [
        b"*UIConstraints: *Duplex *MediaType LABELS",
        b"*cupsUIConstraints foo: *Duplex None *MediaType Plain",
        b"*UIConstraints: malformed",
    ],
)
def test_unsupported_constraints_never_silently_pass(data):
    with pytest.raises(BackendUnavailable):
        PyCupsBackend._ppd_constraints(data, SimpleNamespace(constraints=[]))


def test_constraint_limit_is_after_symmetric_deduplication():
    rows = []
    for i in range(70):
        rows.extend([f"*UIConstraints: *A a{i} *B b{i}", f"*UIConstraints: *B b{i} *A a{i}"])
    assert (
        len(
            PyCupsBackend._ppd_constraints(
                "\n".join(rows).encode(), SimpleNamespace(constraints=[])
            )
        )
        == 70
    )
    with pytest.raises(BackendUnavailable):
        PyCupsBackend._ppd_constraints(
            "\n".join(f"*UIConstraints: *A a{i} *B b{i}" for i in range(129)).encode(),
            SimpleNamespace(constraints=[]),
        )
