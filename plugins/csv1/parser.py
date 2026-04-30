"""
CSV 1 plugin for Apollo — parses .csv files.

Extracts headers, row counts, and structured table data
for data documentation and analysis.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class CSVParser(BaseParser):
    """Parser for CSV files."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {"enabled": True, "extensions": [".csv"]}

    def can_parse(self, filepath: str) -> bool:
        """Return True if this is a .csv file and plugin is enabled."""
        if not self.config.get("enabled", True):
            return False
        return filepath.lower().endswith(".csv")

    def parse_file(self, filepath: str) -> dict | None:
        """Parse CSV file and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse CSV source and extract entities."""
        variables = []

        try:
            lines = source.split('\n')
            if not lines:
                return {
                    "file": filepath,
                    "functions": [],
                    "classes": [],
                    "imports": [],
                    "variables": [],
                }

            # Parse CSV to extract headers
            reader = csv.reader(lines)
            headers = next(reader, None)

            if headers:
                # Add each header as a variable
                for header in headers:
                    if header.strip():
                        variables.append({
                            "name": header.strip(),
                            "line": 1,
                        })

                # Count data rows
                row_count = 0
                for row in reader:
                    if any(cell.strip() for cell in row):
                        row_count += 1

                # Add a row count variable for metadata
                if row_count > 0:
                    variables.append({
                        "name": f"_rows={row_count}",
                        "line": 1,
                    })

        except Exception as e:
            logger.warning("CSV parse error in %s: %s", filepath, e)

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": [],
            "variables": variables,
        }
