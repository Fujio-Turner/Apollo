"""Phase 14 — tests for the response-validation middleware and ResponseValidator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apollo.api import ResponseValidator, ErrorCode, ErrorResponse, StandardResponse


@pytest.fixture
def validator() -> ResponseValidator:
    return ResponseValidator()


# ── Schema loads ───────────────────────────────────────────────────────────


def test_schema_file_exists():
    p = Path(__file__).resolve().parents[1] / "schema" / "api-response.schema.json"
    assert p.exists(), "api-response.schema.json must ship with the repo"


def test_validator_loads_schema(validator):
    schema = validator.schema
    assert schema["$id"].endswith("api-response.schema.json")
    assert "oneOf" in schema


# ── Valid bodies pass ──────────────────────────────────────────────────────


def test_validate_well_formed_error_body(validator):
    body = ErrorResponse(
        code=ErrorCode.NOT_FOUND, message="missing", status_code=404,
    ).to_dict()
    assert validator.is_valid(body), validator.validate(body)


def test_validate_well_formed_success_body(validator):
    body = StandardResponse(data={"items": [1, 2]}).to_dict()
    assert validator.is_valid(body), validator.validate(body)


def test_validate_error_body_with_details(validator):
    body = ErrorResponse(
        code=ErrorCode.FILE_CHANGED, message="md5 changed", status_code=409,
        details={"expected": "abc", "actual": "xyz"},
    ).to_dict()
    assert validator.is_valid(body), validator.validate(body)


def test_validate_error_body_with_legacy_detail_key(validator):
    """Legacy `detail` is allowed alongside the new `error` object."""
    body = {
        "status_code": 404,
        "error": {"code": "NOT_FOUND", "message": "x"},
        "detail": "legacy string",
    }
    assert validator.is_valid(body)


# ── Malformed bodies are rejected ──────────────────────────────────────────


def test_validate_rejects_unknown_error_code(validator):
    body = {
        "status_code": 500,
        "error": {"code": "MADE_UP", "message": "x"},
    }
    problems = validator.validate(body)
    assert problems, "unknown enum value should fail validation"


def test_validate_rejects_missing_message(validator):
    body = {"status_code": 500, "error": {"code": "INTERNAL_ERROR"}}
    assert validator.validate(body)


def test_validate_rejects_missing_code(validator):
    body = {"status_code": 500, "error": {"message": "boom"}}
    assert validator.validate(body)


def test_validate_rejects_out_of_range_status(validator):
    body = {
        "status_code": 99,
        "error": {"code": "INTERNAL_ERROR", "message": "x"},
    }
    assert validator.validate(body)


def test_validate_rejects_completely_wrong_shape(validator):
    assert validator.validate({"foo": "bar"})


# ── Non-blocking semantics ─────────────────────────────────────────────────


def test_validate_returns_list_not_raises(validator):
    # validate() must always return a list, even for garbage input.
    result = validator.validate({"random": object()})
    assert isinstance(result, list)


# ── Middleware integration via the FastAPI app ─────────────────────────────


def _make_app():
    """Spin up the real Apollo app over an empty graph store for HTTP tests."""
    import networkx as nx

    class _StubStore:
        backend = "json"

        def load(self, include_embeddings: bool = True):
            return nx.DiGraph()

    from web.server import create_app

    return create_app(_StubStore(), backend="json", root_dir=None)


def test_404_returns_standard_error_envelope():
    fastapi_testclient = pytest.importorskip("fastapi.testclient").TestClient
    app = _make_app()
    client = fastapi_testclient(app)
    resp = client.get("/api/node/does_not_exist")
    assert resp.status_code == 404
    body = resp.json()
    # New shape:
    assert "error" in body and isinstance(body["error"], dict)
    assert body["error"]["code"] == "NOT_FOUND"
    assert "message" in body["error"]
    # Legacy backward-compat keys:
    assert body["status_code"] == 404


def test_validation_error_envelope_uses_validation_error_code():
    fastapi_testclient = pytest.importorskip("fastapi.testclient").TestClient
    app = _make_app()
    client = fastapi_testclient(app)
    # /api/search/multi requires a non-empty `queries` list — sending an
    # empty list triggers a 400 with our HTTP exception handler.
    resp = client.post("/api/search/multi", json={"queries": []})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_response_envelope_passes_validator_on_real_404():
    fastapi_testclient = pytest.importorskip("fastapi.testclient").TestClient
    app = _make_app()
    client = fastapi_testclient(app)
    body = client.get("/api/node/missing").json()
    assert ResponseValidator().is_valid(body)
