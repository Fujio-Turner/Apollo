"""Semantic error codes used by the Apollo HTTP API (Phase 14).

Clients can route errors by `error.code` rather than fragile string matches
against `error.message`. Add a new member here whenever a new error category
is introduced; keep the wire string SCREAMING_SNAKE_CASE.
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # Client-side input
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"

    # Server-side
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # File / path
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    INVALID_PATH = "INVALID_PATH"
    PATH_ESCAPE = "PATH_ESCAPE"
    FILE_CHANGED = "FILE_CHANGED"

    # Subsystem-specific
    GRAPH_ERROR = "GRAPH_ERROR"
    INDEX_ERROR = "INDEX_ERROR"
    CHAT_ERROR = "CHAT_ERROR"
    PROJECT_ERROR = "PROJECT_ERROR"

    @classmethod
    def from_status(cls, status: int) -> "ErrorCode":
        """Best-effort mapping from an HTTP status code to a generic ErrorCode."""
        return {
            400: cls.VALIDATION_ERROR,
            401: cls.UNAUTHORIZED,
            403: cls.FORBIDDEN,
            404: cls.NOT_FOUND,
            409: cls.CONFLICT,
            422: cls.VALIDATION_ERROR,
        }.get(status, cls.INTERNAL_ERROR)
