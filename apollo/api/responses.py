"""Standardized response shapes for the Apollo HTTP API (Phase 14).

All API errors flow through `ErrorResponse` so clients see a stable shape:

    {
      "status_code": 404,
      "error": {
        "code": "FILE_NOT_FOUND",
        "message": "Not a file: /tmp/x.py",
        "details": { ... }                  // optional
      }
    }

Successful responses use `StandardResponse`:

    { "status": "success", "data": <payload> }

The classes are dataclass-friendly (`to_dict()`) and the `error_json_response`
/ `success_json_response` helpers return a fastapi `JSONResponse` directly.

`ResponseValidator` lazily loads `schema/api-response.schema.json` and
performs **non-blocking** validation against any candidate body — i.e. it
returns a list of error strings rather than raising. The web-server middleware
uses it to *log* mismatches without 500-ing the request.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .error_codes import ErrorCode

# Optional dependency — `jsonschema` is already used elsewhere in the
# codebase (manifest validation). Fall back to no-op validator if missing.
try:
    import jsonschema  # type: ignore
    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover - exercised only if jsonschema absent
    jsonschema = None  # type: ignore
    _HAS_JSONSCHEMA = False


_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "api-response.schema.json"


# ── Dataclasses ────────────────────────────────────────────────────────────


@dataclass
class StandardResponse:
    """Wrapper for successful API payloads."""

    data: Any
    status: str = "success"

    def to_dict(self) -> dict:
        return {"status": self.status, "data": self.data}


@dataclass
class ErrorResponse:
    """Standardized error payload.

    `code` is the semantic ErrorCode (string-coercible). `details` is optional
    and intentionally typed as a free-form dict so subsystems can include
    structured context (e.g. expected_md5, validation field paths).
    """

    code: str
    message: str
    status_code: int = 500
    details: Optional[dict] = None

    def __post_init__(self):
        # Accept either ErrorCode or raw string.
        if isinstance(self.code, ErrorCode):
            self.code = self.code.value

    def to_dict(self) -> dict:
        body: dict[str, Any] = {
            "status_code": self.status_code,
            "error": {
                "code": self.code,
                "message": self.message,
            },
        }
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


# ── JSONResponse helpers ───────────────────────────────────────────────────


def error_json_response(
    code: ErrorCode | str,
    message: str,
    status_code: int = 500,
    details: Optional[dict] = None,
):
    """Build a fastapi.responses.JSONResponse for an error.

    Lazily imports fastapi to keep this module importable in non-web contexts.
    """
    from fastapi.responses import JSONResponse

    err = ErrorResponse(
        code=code if isinstance(code, str) else code.value,
        message=message,
        status_code=status_code,
        details=details,
    )
    return JSONResponse(status_code=status_code, content=err.to_dict())


def success_json_response(data: Any, status_code: int = 200):
    """Build a fastapi.responses.JSONResponse wrapping a success payload."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=StandardResponse(data).to_dict())


# ── Validator ──────────────────────────────────────────────────────────────


class ResponseValidator:
    """Validates response bodies against the api-response JSON schema.

    Non-blocking: `validate(body)` returns a list of error message strings
    instead of raising. `is_valid(body)` is the boolean shortcut.
    """

    def __init__(self, schema_path: Path | None = None):
        self.schema_path = Path(schema_path) if schema_path else _SCHEMA_PATH
        self._schema: Optional[dict] = None
        self._available = _HAS_JSONSCHEMA

    @property
    def schema(self) -> dict:
        if self._schema is None:
            with open(self.schema_path, "r", encoding="utf-8") as f:
                self._schema = _json.load(f)
        return self._schema

    def validate(self, body: Any) -> list[str]:
        if not self._available:
            return []
        try:
            validator = jsonschema.Draft202012Validator(self.schema)  # type: ignore[attr-defined]
            return [e.message for e in validator.iter_errors(body)]
        except Exception as e:
            return [f"validator_error: {e!r}"]

    def is_valid(self, body: Any) -> bool:
        return not self.validate(body)
