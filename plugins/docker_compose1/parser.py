"""
plugins.docker_compose1 — Docker Compose plugin for Apollo
===========================================================

Parses docker-compose.yml into Apollo's structured result dict.

Extracts:
- Services as entities
- Image references
- Volume references
- Dependencies between services
"""
from __future__ import annotations
import logging

import re
import json
from pathlib import Path
from typing import Optional, Any

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class DockerComposeParser(BaseParser):
    """Parse docker-compose YAML files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "filenames": ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._filenames = frozenset(
            self.config.get("filenames") or [
                "docker-compose.yml", "docker-compose.yaml",
                "compose.yml", "compose.yaml"
            ]
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        return Path(filepath).name in self._filenames

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse docker-compose YAML source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_dependencies(source, lines),
            "classes": self._extract_services(source, lines),
            "imports": self._extract_images(source, lines),
            "variables": self._extract_volumes(source, lines),
        }

    def _extract_services(self, source: str, lines: list[str]) -> list[dict]:
        """Extract service definitions."""
        services = []
        # Match "services:" block and extract service names
        services_match = re.search(r'^services:\s*$', source, re.MULTILINE)
        if not services_match:
            return services
        
        services_start = services_match.end()
        services_section = source[services_start:]
        
        # Find all service names (indented lines starting at root services level)
        service_re = re.compile(r'^(\s{2})(\w[\w-]*)\s*:', re.MULTILINE)
        
        for m in service_re.finditer(services_section):
            service_name = m.group(2)
            line_start = source[:services_start + m.start()].count("\n") + 1
            
            # Find end of service (next service or end of section)
            next_service = service_re.search(services_section[m.end():])
            if next_service:
                line_end = source[:services_start + m.end() + next_service.start()].count("\n") + 1
            else:
                line_end = len(lines)
            
            if line_start <= line_end and line_start <= len(lines):
                service_lines = lines[line_start - 1:line_end]
                service_source = "\n".join(service_lines)
                
                services.append({
                    "name": service_name,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": service_source,
                    "methods": [],
                    "bases": [],
                })
        
        return services

    def _extract_images(self, source: str, lines: list[str]) -> list[dict]:
        """Extract image references."""
        imports = []
        # Match image: references
        image_re = re.compile(r'^\s+image:\s*([^\s#]+)', re.MULTILINE)
        
        for m in image_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            image = m.group(1)
            
            imports.append({
                "module": image,
                "names": [],
                "alias": None,
                "line": line_num,
            })
        
        return imports

    def _extract_volumes(self, source: str, lines: list[str]) -> list[dict]:
        """Extract volume definitions."""
        variables = []
        
        # Top-level volumes section
        volumes_match = re.search(r'^volumes:\s*$', source, re.MULTILINE)
        if volumes_match:
            volumes_start = volumes_match.end()
            volumes_section = source[volumes_start:]
            
            # Volume names (indented lines)
            volume_re = re.compile(r'^(\s{2})(\w[\w-]*)\s*:', re.MULTILINE)
            
            for m in volume_re.finditer(volumes_section):
                volume_name = m.group(2)
                line_num = source[:volumes_start + m.start()].count("\n") + 1
                variables.append({"name": volume_name, "line": line_num})
        
        # Service-level volumes
        volume_mount_re = re.compile(r'^\s{4}volumes:\s*$', re.MULTILINE)
        for m in volume_mount_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            variables.append({"name": f"mount_{line_num}", "line": line_num})
        
        return variables

    def _extract_dependencies(self, source: str, lines: list[str]) -> list[dict]:
        """Extract service dependencies."""
        dependencies = []
        # depends_on: references to other services
        depends_re = re.compile(r'^\s{4}depends_on:\s*$', re.MULTILINE)
        
        for m in depends_re.finditer(source):
            line_start = source[:m.start()].count("\n") + 1
            
            # Find dependent service names
            depends_section = source[m.end():m.end() + 500]
            dep_names = re.findall(r'^\s{6}([a-z_]\w*)[\s:$]', depends_section, re.MULTILINE)
            
            if dep_names:
                dep_str = ", ".join(dep_names)
                dependencies.append({
                    "name": f"depends_on_{line_start}",
                    "line_start": line_start,
                    "line_end": line_start + 1,
                    "source": f"depends_on: [{dep_str}]",
                    "calls": [{"name": dep, "source": dep} for dep in dep_names],
                })
        
        return dependencies
