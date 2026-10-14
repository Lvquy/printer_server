"""Keep the bundled read-only client reference aligned with real fake-backend responses."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import Harness

CONTRACT = json.loads((Path(__file__).parents[1] / "frontend/src/client-api.json").read_text())


def check_schema(value, schema):
    """Check the subset used by these schemas; not a general JSON Schema validator."""
    if "$ref" in schema:
        schema = CONTRACT["$defs"][schema["$ref"].split("/")[-1]]
    if "oneOf" in schema:
        failures = 0
        for candidate in schema["oneOf"]:
            try:
                check_schema(value, candidate)
            except AssertionError:
                failures += 1
        assert failures < len(schema["oneOf"])
        return
    if "const" in schema:
        assert value == schema["const"]
    if "enum" in schema:
        assert value in schema["enum"]
    kind = schema.get("type")
    if kind is None:
        return
    kinds = kind if isinstance(kind, list) else [kind]
    types = {
        "string": str,
        "integer": int,
        "boolean": bool,
        "object": dict,
        "array": list,
        "null": type(None),
    }
    assert any(type(value) is types[k] for k in kinds), (kind, type(value))
    if value is None:
        return
    if type(value) is int:
        assert schema.get("minimum", value) <= value <= schema.get("maximum", value)
    if isinstance(value, str):
        assert schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value))
    if isinstance(value, list):
        for item in value:
            check_schema(item, schema["items"])
    if isinstance(value, dict):
        assert set(schema.get("required", [])) <= value.keys()
        for key, item in value.items():
            if key in schema.get("properties", {}):
                check_schema(item, schema["properties"][key])
            elif isinstance(schema.get("additionalProperties"), dict):
                check_schema(item, schema["additionalProperties"])


def validate(value, name):
    check_schema(value, CONTRACT["$defs"][name])


def test_api_guide_examples_and_routes(harness: Harness):
    routes = {
        (route.path, method) for route in harness.app.routes for method in (route.methods or [])
    }
    for endpoint in CONTRACT["endpoints"]:
        assert (endpoint["path"], endpoint["method"]) in routes
        if "requestExample" in endpoint:
            validate(endpoint["requestExample"], endpoint["bodySchema"])
        for response in endpoint["responses"]:
            validate(response["example"], response["schema"])
    validate({"detail": "Invalid API key"}, "Error")
    validate(
        {"detail": [{"loc": ["body", "title"], "msg": "Field required", "type": "missing"}]},
        "Error",
    )


def test_api_guide_schemas_match_real_client_responses(harness: Harness):
    headers = {"Authorization": f"Bearer {harness.key}"}
    validate(harness.api.get("/api/v1/printers", headers=headers).json(), "Printers")
    validate(
        harness.api.get("/api/v1/printers", headers=headers).json()[0]["capabilities"],
        "Capabilities",
    )
    harness.backend.available = False
    validate(
        harness.api.get("/api/v1/printers", headers=headers).json()[0]["capabilities"],
        "Capabilities",
    )
    harness.backend.available = True
    data = {
        "printer_id": harness.printer["id"],
        "format": "pdf",
        "title": "Invoice example",
        "copies": "1",
    }
    submit_headers = {**headers, "Idempotency-Key": "guide-test-once"}
    files = {"file": ("invoice.pdf", b"%PDF-1.7\nFake content", "application/pdf")}
    accepted = harness.api.post("/api/v1/jobs", headers=submit_headers, data=data, files=files)
    assert accepted.status_code == 202
    validate(accepted.json(), "Accepted")
    replay = harness.api.post("/api/v1/jobs", headers=submit_headers, data=data, files=files)
    assert replay.status_code == 200
    validate(replay.json(), "Replay")
    job_id = accepted.json()["job_id"]
    validate(harness.api.get("/api/v1/jobs?limit=50", headers=headers).json(), "Jobs")
    validate(harness.api.get(f"/api/v1/jobs/{job_id}", headers=headers).json(), "Job")
    canceled = harness.api.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert canceled.status_code == 200
    validate(canceled.json(), "Job")
    validate(harness.api.get("/api/v1/jobs").json(), "Error")
