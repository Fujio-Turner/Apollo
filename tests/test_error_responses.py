"""Phase 14 — unit tests for the standardized error response shapes."""
from __future__ import annotations

import json

import pytest

from apollo.api import (
    ErrorCode,
    StandardResponse,
    ErrorResponse,
    error_json_response,
    success_json_response,
)


# ── ErrorCode enum ─────────────────────────────────────────────────────────


def test_error_code_values_are_screaming_snake():
    for code in ErrorCode:
        assert code.value.isupper()
        assert " " not in code.value


def test_error_code_from_status_known():
    assert ErrorCode.from_status(404) is ErrorCode.NOT_FOUND
    assert ErrorCode.from_status(403) is ErrorCode.FORBIDDEN
    assert ErrorCode.from_status(409) is ErrorCode.CONFLICT
    assert ErrorCode.from_status(422) is ErrorCode.VALIDATION_ERROR
    assert ErrorCode.from_status(401) is ErrorCode.UNAUTHORIZED
    assert ErrorCode.from_status(400) is ErrorCode.VALIDATION_ERROR


def test_error_code_from_status_unknown_falls_back_to_internal():
    assert ErrorCode.from_status(599) is ErrorCode.INTERNAL_ERROR
    assert ErrorCode.from_status(200) is ErrorCode.INTERNAL_ERROR


def test_error_code_is_string_coercible():
    # Enum member should serialise as its string value when used in JSON.
    assert json.dumps({"code": ErrorCode.NOT_FOUND.value}) == '{"code": "NOT_FOUND"}'


# ── StandardResponse / ErrorResponse dataclasses ───────────────────────────


def test_standard_response_to_dict():
    resp = StandardResponse(data={"x": 1})
    assert resp.to_dict() == {"status": "success", "data": {"x": 1}}


def test_standard_response_with_list_payload():
    resp = StandardResponse(data=[1, 2, 3])
    assert resp.to_dict()["data"] == [1, 2, 3]


def test_error_response_minimal_shape():
    err = ErrorResponse(code=ErrorCode.NOT_FOUND, message="missing", status_code=404)
    body = err.to_dict()
    assert body["status_code"] == 404
    assert body["error"] == {"code": "NOT_FOUND", "message": "missing"}
    # Details is omitted when None.
    assert "details" not in body["error"]


def test_error_response_includes_details_when_set():
    err = ErrorResponse(
        code=ErrorCode.VALIDATION_ERROR,
        message="bad input",
        status_code=422,
        details={"field": "name"},
    )
    body = err.to_dict()
    assert body["error"]["details"] == {"field": "name"}


def test_error_response_accepts_string_code():
    """Non-enum string codes work too (forward-compat with new codes)."""
    err = ErrorResponse(code="CUSTOM_CODE", message="m", status_code=500)
    assert err.to_dict()["error"]["code"] == "CUSTOM_CODE"


def test_error_response_round_trips_to_json():
    err = ErrorResponse(code=ErrorCode.PATH_ESCAPE, message="bad", status_code=403)
    body = json.loads(json.dumps(err.to_dict()))
    assert body["error"]["code"] == "PATH_ESCAPE"
    assert body["status_code"] == 403


# ── JSONResponse helpers ───────────────────────────────────────────────────


def test_error_json_response_has_correct_status():
    resp = error_json_response(ErrorCode.FILE_NOT_FOUND, "missing", status_code=404)
    assert resp.status_code == 404
    body = json.loads(resp.body)
    assert body["error"]["code"] == "FILE_NOT_FOUND"
    assert body["error"]["message"] == "missing"
    assert body["status_code"] == 404


def test_error_json_response_with_details():
    resp = error_json_response(
        ErrorCode.FILE_CHANGED, "md5 mismatch", status_code=409,
        details={"expected": "abc", "actual": "xyz"},
    )
    body = json.loads(resp.body)
    assert body["error"]["details"]["expected"] == "abc"


def test_success_json_response():
    resp = success_json_response({"hello": "world"})
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["status"] == "success"
    assert body["data"] == {"hello": "world"}


def test_success_json_response_custom_status():
    resp = success_json_response({"id": "x"}, status_code=201)
    assert resp.status_code == 201


# ── Coverage of all enum members ───────────────────────────────────────────


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_serializes(code: ErrorCode):
    err = ErrorResponse(code=code, message="m", status_code=500)
    body = err.to_dict()
    assert body["error"]["code"] == code.value
