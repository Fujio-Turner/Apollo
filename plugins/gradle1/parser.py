"""
plugins.gradle1 — Gradle build script plugin for Apollo.

Parses build.gradle and build.gradle.kts files to extract dependencies, tasks, and versions.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class GradleParser(BaseParser):
    """Parse Gradle build files into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".gradle", ".gradle.kts"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".gradle"])
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        name = Path(filepath).name.lower()
        return name in ["build.gradle", "build.gradle.kts"] or any(
            name.endswith(ext) for ext in self._extensions
        )

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("failed to read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        lines = source.splitlines(keepends=False)
        imports = []
        variables = []
        functions = []

        # Patterns
        dep_pattern = re.compile(r'(?:dependencies|implementation|testImplementation|compileOnly|runtimeOnly)\s*[({]')
        task_pattern = re.compile(r'task\s+(\w+)\s*[({]')
        version_pattern = re.compile(r'(?:version|ext\.)\s*=\s*["\']?([^"\'\n]+)["\']?')
        string_dep_pattern = re.compile(r'["\']([a-zA-Z0-9.\-_]+:[a-zA-Z0-9.\-_]+:[a-zA-Z0-9.\-_]+)["\']')

        in_dependencies = False
        brace_depth = 0
        task_num = 0

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1

            # Track dependency blocks
            if dep_pattern.search(line):
                in_dependencies = True
                brace_depth = line.count("{") - line.count("}")

            if in_dependencies:
                # Extract string-based dependencies
                for match in string_dep_pattern.finditer(line):
                    dep = match.group(1)
                    imports.append({
                        "module": dep,
                        "names": [],
                        "alias": None,
                        "line": line_no,
                        "level": 0,
                    })

                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0:
                    in_dependencies = False

            # Extract task definitions
            task_match = task_pattern.search(line)
            if task_match:
                task_name = task_match.group(1)
                task_num += 1
                functions.append({
                    "name": task_name,
                    "line_start": line_no,
                    "line_end": line_no,
                    "source": line.strip(),
                    "docstring": None,
                    "parameters": [],
                    "decorators": [],
                    "calls": [],
                })

            # Extract version declarations
            version_match = version_pattern.search(line)
            if version_match:
                # Try to extract variable name
                var_match = re.match(r'\s*(\w+)\s*', line)
                if var_match:
                    var_name = var_match.group(1)
                    if var_name not in ["task", "dependencies", "configurations"]:
                        variables.append({
                            "name": var_name,
                            "line": line_no,
                        })

        return {
            "file": filepath,
            "functions": functions,
            "classes": [],
            "imports": imports,
            "variables": variables,
            "comments": [],
        }
