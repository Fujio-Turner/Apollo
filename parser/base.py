from __future__ import annotations

import abc


class BaseParser(abc.ABC):
    """Abstract interface for language parsers."""

    @abc.abstractmethod
    def can_parse(self, filepath: str) -> bool:
        """Return True if this parser can handle the given file."""

    @abc.abstractmethod
    def parse_file(self, filepath: str) -> dict | None:
        """Parse a file and return extracted entities.

        Returns a dict with keys:
            file: str — absolute file path
            functions: list[dict] — extracted functions
            classes: list[dict] — extracted classes
            imports: list[dict] — extracted imports
            variables: list[dict] — extracted variables

        Returns None if the file cannot be parsed.
        """

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse from an already-read source string.

        Subclasses may override for efficiency. The default falls back to
        ``parse_file`` (which re-reads from disk).
        """
        return self.parse_file(filepath)
