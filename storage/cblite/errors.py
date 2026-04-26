"""Couchbase Lite error types."""


class CouchbaseLiteError(Exception):
    """Base exception for Couchbase Lite operations."""
    def __init__(self, message: str, domain: int = 0, code: int = 0):
        self.domain = domain
        self.code = code
        super().__init__(message)


class CouchbaseLiteNotFound(CouchbaseLiteError):
    """Document or resource not found."""
    pass


class CouchbaseLiteNotAvailable(CouchbaseLiteError):
    """libcblite shared library not found on this system."""
    pass
