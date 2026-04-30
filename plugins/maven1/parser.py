"""
plugins.maven1 — Maven pom.xml plugin for Apollo.

Parses pom.xml files to extract dependencies (as imports), modules, and properties.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class MavenParser(BaseParser):
    """Parse Maven pom.xml files into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": ["pom.xml"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        return Path(filepath).name in self.config.get("extensions", ["pom.xml"])

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("failed to read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        try:
            root = ET.fromstring(source)
        except ET.ParseError as exc:
            logger.warning("XML parse error in %s: %s", filepath, exc)
            return None

        imports = []
        variables = []
        functions = []

        # Extract namespace
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}
        
        # Try without namespace first (common case)
        dependencies = root.findall(".//dependency")
        if not dependencies:
            dependencies = root.findall(".//m:dependency", ns)

        for dep in dependencies:
            group_id_elem = dep.find("groupId")
            artifact_id_elem = dep.find("artifactId")
            version_elem = dep.find("version")
            scope_elem = dep.find("scope")

            if group_id_elem is None or artifact_id_elem is None:
                continue

            group_id = group_id_elem.text or ""
            artifact_id = artifact_id_elem.text or ""
            version = version_elem.text if version_elem is not None else ""
            scope = scope_elem.text if scope_elem is not None else "compile"

            module_name = f"{group_id}.{artifact_id}"
            imports.append({
                "module": module_name,
                "names": [],
                "alias": None,
                "line": 0,
                "level": 0,
            })

            if version:
                variables.append({
                    "name": f"{artifact_id}_version",
                    "line": 0,
                })

        # Extract properties
        properties = root.findall(".//properties/*")
        if not properties:
            properties = root.findall(".//m:properties/*", ns)

        for prop in properties:
            if prop.tag and prop.text:
                variables.append({
                    "name": prop.tag,
                    "line": 0,
                })

        # Extract modules
        modules = root.findall(".//module")
        if not modules:
            modules = root.findall(".//m:module", ns)

        for module in modules:
            if module.text:
                functions.append({
                    "name": module.text,
                    "line_start": 0,
                    "line_end": 0,
                    "source": f"<module>{module.text}</module>",
                    "docstring": None,
                    "parameters": [],
                    "decorators": [],
                    "calls": [],
                })

        return {
            "file": filepath,
            "functions": functions,
            "classes": [],
            "imports": imports,
            "variables": variables,
            "comments": [],
        }
