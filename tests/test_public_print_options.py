import pytest

from print_appliance.print_options import InvalidPrintOption, public_capabilities, translate_options
from tests.test_api import PDF, post_pdf


def test_public_options_ignore_old_allowlist_and_snapshot_native_values(harness):
    harness.appliance.edit_printer(harness.printer["id"], {"allowed_options": {}})
    response = post_pdf(
        harness,
        harness.key,
        "new-public",
        options='{"paper_size":"Letter","sides":"two-sided-long-edge"}',
    )
    assert response.status_code == 202
    with harness.appliance.db() as db:
        row = db.execute(
            "SELECT snapshot_json FROM jobs WHERE id=?", (response.json()["job_id"],)
        ).fetchone()
    assert "DuplexNoTumble" in row[0]
    assert "Letter" in row[0]
    assert "paper_size" not in row[0]
    assert (
        post_pdf(harness, harness.key, "new-public", options='{"paper_size":"A4"}').status_code
        == 409
    )


def test_invalid_field_and_zpl_do_not_accept_jobs(harness):
    auth = {"Authorization": f"Bearer {harness.key}", "Idempotency-Key": "bad-field"}
    for fields in (
        {"options": "not-json"},
        {"options": '{"PageSize":"A4"}'},
        {"options": '{"scaling":"invalid"}'},
        {"options": '{"paper_size":"A4"}', "paper_size": "A4"},
        {"paper_siz": "A4"},
        {"paper_size": ""},
        {"sides": "invalid"},
    ):
        result = harness.api.post(
            "/api/v1/jobs",
            headers=auth,
            data={"printer_id": harness.printer["id"], "format": "pdf", "title": "Test", **fields},
            files={"file": ("x.pdf", PDF, "application/pdf")},
        )
        assert result.status_code == 422
    result = harness.api.post(
        "/api/v1/jobs",
        headers=auth,
        data={
            "printer_id": harness.printer["id"],
            "format": "zpl",
            "title": "Test",
            "sides": "one-sided",
        },
        files={"file": ("x.zpl", b"^XA^XZ", "text/plain")},
    )
    assert result.status_code == 422
    assert harness.appliance.client_jobs(harness.client_id, 50) == []


def test_scaling_in_options_payload_maps_to_cups(harness):
    harness.backend.capability_data["options"].append(
        {"name": "print-scaling", "default": "auto", "choices": [{"value": "auto"}, {"value": "fit"}]}
    )
    response = post_pdf(harness, harness.key, "pdf-scaling", options='{"scaling":"fit"}')
    assert response.status_code == 202
    with harness.appliance.db() as db:
        snapshot = db.execute(
            "SELECT snapshot_json FROM jobs WHERE id=?", (response.json()["job_id"],)
        ).fetchone()[0]
    assert '"print-scaling":"fit"' in snapshot


def test_unknown_mapping_is_not_advertised():
    schema = {
        "availability": "partial",
        "ipp_attributes": {"sides-supported": ["two-sided-long-edge"]},
        "options": [],
    }
    caps, _ = public_capabilities(schema, {}, ["pdf"])
    assert caps["supported"]["sides"] == []
    with pytest.raises(InvalidPrintOption):
        translate_options({"sides": "two-sided-long-edge"}, schema, {}, "pdf")


def test_only_simple_options_are_advertised_and_scaling_is_mapped():
    schema = {
        "availability": "available",
        "options": [
            {
                "name": "ColorModel",
                "default": "Gray",
                "choices": [{"value": v} for v in ["Gray", "RGB"]],
            },
            {
                "name": "print-scaling",
                "default": "auto",
                "choices": [{"value": v} for v in ["auto", "fit"]],
            },
        ],
    }
    caps, _ = public_capabilities(schema, {}, ["pdf"])
    assert set(caps["supported"]) == {"paper_size", "sides", "scaling"}
    assert caps["supported"]["scaling"] == ["auto", "fit"]
    assert translate_options({"scaling": "fit"}, schema, {}, "pdf") == {"print-scaling": "fit"}
    with pytest.raises(InvalidPrintOption):
        translate_options({"color_mode": "color"}, schema, {}, "pdf")


def test_unavailable_and_stale_fail_without_acceptance(harness):
    harness.backend.available = False
    assert post_pdf(harness, harness.key, "offline").status_code == 503
    harness.backend.available = True
    harness.backend.queue_data[harness.printer["queue"]]["printer-ppd-name"] = "changed"
    assert post_pdf(harness, harness.key, "stale").status_code == 503


def test_legacy_replay_survives_contract_change_without_new_acceptance(harness):
    # Old default request and new omitted fields have the same durable identity.
    accepted = post_pdf(harness, harness.key, "before-upgrade")
    harness.backend.available = False
    response = harness.api.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {harness.key}", "Idempotency-Key": "before-upgrade"},
        data={
            "printer_id": harness.printer["id"],
            "format": "pdf",
            "title": "Test invoice",
            "copies": "1",
            "options": "{}",
        },
        files={"file": ("invoice.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == accepted.json()["job_id"]
    assert response.json()["deduplicated"]


def test_public_response_hides_driver_vocabulary(harness):
    response = harness.api.get(
        "/api/v1/printers", headers={"Authorization": f"Bearer {harness.key}"}
    )
    text = response.text
    for hidden in (
        "Duplex",
        "BindEdge",
        "allowed_options",
        "mapping_fingerprint",
        "ipp_attributes",
    ):
        assert hidden not in text
    assert "two-sided-long-edge" in text
