"""
plugins.k8s_manifest1 — Kubernetes manifest plugin for Apollo.

Parses Kubernetes YAML manifests to extract Deployments/Pods/Services as classes,
containers as methods, environment variables, and image references as imports.
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


class K8sManifestParser(BaseParser):
    """Parse Kubernetes manifests into Apollo's standard result dict."""

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
        # Check if it's likely a k8s manifest
        path = Path(filepath)
        if path.suffix.lower() not in self._extensions:
            return False
        # Heuristic: "k8s" in path, or "manifest" in name
        path_str = str(path).lower()
        return "k8s" in path_str or "manifest" in path_str or "kubernetes" in path_str

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("failed to read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        # Try YAML parsing
        if HAS_YAML:
            try:
                documents = list(yaml.safe_load_all(source))
            except Exception as exc:
                logger.warning("YAML parse error in %s: %s", filepath, exc)
                documents = []
        else:
            documents = []

        classes = []
        variables = []
        imports = []
        methods_by_class = {}

        for doc in documents:
            if not isinstance(doc, dict):
                continue

            kind = doc.get("kind", "")
            metadata = doc.get("metadata", {})
            name = metadata.get("name", kind.lower())

            spec = doc.get("spec", {})

            # Deployments, StatefulSets, DaemonSets → classes
            if kind in ["Deployment", "StatefulSet", "DaemonSet", "Pod", "Service"]:
                classes.append({
                    "name": f"{kind}:{name}",
                    "line_start": 0,
                    "line_end": 0,
                    "source": f"{kind} {name}",
                    "methods": [],
                    "docstring": metadata.get("namespace"),
                })

                # Extract containers
                template = spec.get("template", {})
                template_spec = template.get("spec", {})
                containers = template_spec.get("containers", [])

                if not containers and kind == "Pod":
                    containers = spec.get("containers", [])

                for container in containers:
                    if not isinstance(container, dict):
                        continue

                    container_name = container.get("name", "default")
                    image = container.get("image", "")

                    if image:
                        imports.append({
                            "module": image,
                            "names": [],
                            "alias": container_name,
                            "line": 0,
                            "level": 0,
                        })

                    # Extract environment variables
                    env = container.get("env", [])
                    for env_var in env:
                        if isinstance(env_var, dict):
                            var_name = env_var.get("name", "")
                            if var_name:
                                variables.append({
                                    "name": var_name,
                                    "line": 0,
                                })

            elif kind == "ConfigMap":
                # ConfigMap data → variables
                data = doc.get("data", {})
                for key in data.keys():
                    variables.append({
                        "name": key,
                        "line": 0,
                    })

            elif kind == "Secret":
                # Secret data → variables
                data = doc.get("data", {})
                for key in data.keys():
                    variables.append({
                        "name": key,
                        "line": 0,
                    })

        return {
            "file": filepath,
            "functions": [],
            "classes": classes,
            "imports": imports,
            "variables": variables,
            "comments": [],
        }
