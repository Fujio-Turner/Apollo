"""
plugins.dockerfile1 — Dockerfile plugin for Apollo
==================================================

Parses Dockerfile into Apollo's structured result dict.

Extracts:
- FROM image references
- COPY/ADD file references
- RUN commands as operations
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class DockerfileParser(BaseParser):
    """Parse Dockerfile."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": ["Dockerfile", ".dockerfile"],
        "filenames": ["Dockerfile", "dockerfile"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [])
        )
        self._filenames = frozenset(
            self.config.get("filenames") or ["Dockerfile", "dockerfile"]
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        path = Path(filepath)
        # Check filename
        if path.name in self._filenames:
            return True
        # Check extension
        return path.suffix.lower() in self._extensions

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse Dockerfile source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_runs(source, lines),
            "classes": self._extract_stages(source, lines),
            "imports": self._extract_from_images(source, lines),
            "variables": self._extract_env_args(source, lines),
        }

    def _extract_from_images(self, source: str, lines: list[str]) -> list[dict]:
        """Extract FROM image references."""
        imports = []
        # FROM image:tag or FROM image
        from_re = re.compile(r'^FROM\s+([^\s:]+)(?::([^\s]+))?(?:\s+as\s+(\w+))?', re.MULTILINE | re.IGNORECASE)
        
        for m in from_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            image = m.group(1)
            tag = m.group(2) or "latest"
            alias = m.group(3)
            
            imports.append({
                "module": f"{image}:{tag}",
                "names": [],
                "alias": alias,
                "line": line_num,
            })
        
        return imports

    def _extract_stages(self, source: str, lines: list[str]) -> list[dict]:
        """Extract multi-stage build stages."""
        stages = []
        # FROM image AS stage_name or just FROM image
        stage_re = re.compile(r'^FROM\s+[^\s]+(?:\s+as\s+(\w+))?', re.MULTILINE | re.IGNORECASE)
        
        stage_num = 0
        for m in stage_re.finditer(source):
            stage_name = m.group(1) or f"stage{stage_num}"
            line_start = source[:m.start()].count("\n") + 1
            
            # Find end of stage (next FROM or end of file)
            next_from = re.search(r'^FROM\s+', source[m.end():], re.MULTILINE | re.IGNORECASE)
            if next_from:
                line_end = source[:m.end() + next_from.start()].count("\n") + 1
            else:
                line_end = len(lines)
            
            stage_lines = lines[line_start - 1:line_end]
            stage_source = "\n".join(stage_lines)
            
            stages.append({
                "name": stage_name,
                "line_start": line_start,
                "line_end": line_end,
                "source": stage_source,
                "methods": [],
                "bases": [],
            })
            
            stage_num += 1
        
        return stages

    def _extract_runs(self, source: str, lines: list[str]) -> list[dict]:
        """Extract RUN commands as operations."""
        operations = []
        # RUN command
        run_re = re.compile(r'^RUN\s+(.+?)(?=\n(?:FROM|RUN|COPY|ADD|EXPOSE|CMD|WORKDIR|ENV|ARG|LABEL)|\Z)', re.MULTILINE | re.IGNORECASE | re.DOTALL)
        
        run_num = 0
        for m in run_re.finditer(source):
            line_start = source[:m.start()].count("\n") + 1
            line_end = source[:m.end()].count("\n") + 1
            
            command = m.group(1).strip()
            # Collapse multiline commands
            command = re.sub(r'\s+', ' ', command)
            
            if line_start <= len(lines) and line_end <= len(lines):
                run_lines = lines[line_start - 1:line_end]
                run_source = "\n".join(run_lines)
                
                operations.append({
                    "name": f"run_{run_num}",
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": run_source,
                    "calls": self._extract_commands(command),
                })
            
            run_num += 1
        
        return operations

    def _extract_env_args(self, source: str, lines: list[str]) -> list[dict]:
        """Extract ENV and ARG declarations."""
        variables = []
        # ENV NAME=value or ARG NAME=value or ARG NAME
        var_re = re.compile(r'^(?:ENV|ARG)\s+([A-Za-z_]\w*)(?:\s*=)?', re.MULTILINE | re.IGNORECASE)
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        
        return variables

    def _extract_commands(self, command_str: str) -> list[dict]:
        """Extract command names from a RUN command."""
        calls = []
        # Get the first word (the actual command)
        words = command_str.split()
        if words:
            cmd = words[0]
            # Skip shell operators and special syntax
            if cmd not in {"&&", "||", ";", "|", "(", ")", "if", "then", "else", "fi"}:
                calls.append({"name": cmd, "source": cmd})
        
        return calls
