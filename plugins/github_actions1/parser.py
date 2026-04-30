"""
plugins.github_actions1 — GitHub Actions workflow plugin for Apollo.

Parses .github/workflows/*.yml files to extract jobs as functions, uses as imports, and env vars.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class GitHubActionsParser(BaseParser):
    """Parse GitHub Actions workflows into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".yml", ".yaml"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".yml", ".yaml"])
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        # Only parse files in .github/workflows/
        path = Path(filepath)
        if ".github" not in path.parts or "workflows" not in path.parts:
            return False
        return path.suffix.lower() in self._extensions

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("failed to read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        # Try YAML parsing if available
        if HAS_YAML:
            try:
                workflow = yaml.safe_load(source)
            except Exception as exc:
                logger.warning("YAML parse error in %s: %s", filepath, exc)
                return self._parse_source_fallback(source, filepath)
        else:
            workflow = self._parse_source_fallback(source, filepath)

        if not isinstance(workflow, dict):
            return None

        functions = []
        imports = []
        variables = []

        # Extract jobs
        jobs = workflow.get("jobs", {})
        if isinstance(jobs, dict):
            for job_name, job_def in jobs.items():
                if isinstance(job_def, dict):
                    functions.append({
                        "name": job_name,
                        "line_start": 0,
                        "line_end": 0,
                        "source": f"job: {job_name}",
                        "docstring": job_def.get("name") or None,
                        "parameters": [],
                        "decorators": [],
                        "calls": [],
                    })

                    # Extract 'uses' from steps
                    steps = job_def.get("steps", [])
                    if isinstance(steps, list):
                        for step in steps:
                            if isinstance(step, dict):
                                uses = step.get("uses")
                                if uses:
                                    imports.append({
                                        "module": uses,
                                        "names": [],
                                        "alias": step.get("name"),
                                        "line": 0,
                                        "level": 0,
                                    })

                                # Extract env variables
                                env = step.get("env")
                                if isinstance(env, dict):
                                    for var_name in env.keys():
                                        variables.append({
                                            "name": var_name,
                                            "line": 0,
                                        })

        # Extract top-level env
        env = workflow.get("env", {})
        if isinstance(env, dict):
            for var_name in env.keys():
                variables.append({
                    "name": var_name,
                    "line": 0,
                })

        return {
            "file": filepath,
            "functions": functions,
            "classes": [],
            "imports": imports,
            "variables": variables,
            "comments": [],
        }

    def _parse_source_fallback(self, source: str, filepath: str) -> dict:
        """Fallback YAML parser using regex when yaml module unavailable."""
        result = {}
        lines = source.splitlines(keepends=False)

        # Simple regex-based extraction
        job_pattern = re.compile(r"^\s*(\w+):\s*$")
        uses_pattern = re.compile(r'uses:\s*["\']?([^\'"]+)["\']?')
        env_pattern = re.compile(r"^\s*(\w+):\s*['\"]?([^'\"]+)['\"]?")

        jobs = {}
        current_job = None

        for line in lines:
            job_match = job_pattern.match(line)
            if job_match and current_job is None:
                current_job = job_match.group(1)
                jobs[current_job] = {"steps": []}

        result["jobs"] = jobs
        return result
