"""Self-contained tests for the k8s_manifest1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.k8s_manifest1 import K8sManifestParser


class TestK8sPluginDiscovery:
    def test_k8s_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, K8sManifestParser) for p in plugins)


class TestK8sPluginRecognisesFiles:
    def test_recognises_k8s_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "k8s_deployment.yml"
            f.write_text("")
            assert K8sManifestParser().can_parse(str(f))

    def test_recognises_manifest_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "manifest.yaml"
            f.write_text("")
            assert K8sManifestParser().can_parse(str(f))

    def test_rejects_random_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.yml"
            f.write_text("")
            assert not K8sManifestParser().can_parse(str(f))


class TestK8sPluginParsesManifests:
    def test_parses_deployment(self):
        content = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: default
spec:
  replicas: 3
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: app
        image: my-app:1.0
        env:
        - name: LOG_LEVEL
          value: INFO
        - name: DB_HOST
          value: postgres
---
apiVersion: v1
kind: Service
metadata:
  name: my-app-svc
spec:
  ports:
  - port: 80
    targetPort: 8080
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "k8s_deployment.yaml"
            f.write_text(content)
            result = K8sManifestParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_returns_valid_for_configmap(self):
        content = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  APP_NAME: myapp
  DEBUG: "false"
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "k8s_configmap.yml"
            f.write_text(content)
            result = K8sManifestParser().parse_file(str(f))

        assert result is not None
        assert "variables" in result


class TestK8sPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "k8s_manifest.yml"
            f.write_text("")
            parser = K8sManifestParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
