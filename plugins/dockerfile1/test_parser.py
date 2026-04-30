"""Self-contained tests for dockerfile1 plugin."""
from __future__ import annotations

from plugins.dockerfile1 import DockerfileParser
from apollo.plugins import discover_plugins


class TestDockerfile1PluginDiscovery:
    def test_recognises_dockerfile(self, tmp_path):
        f = tmp_path / "Dockerfile"
        f.write_text("FROM ubuntu\n")
        assert DockerfileParser().can_parse(str(f))

    def test_recognises_dockerfile_lowercase(self, tmp_path):
        f = tmp_path / "dockerfile"
        f.write_text("FROM ubuntu\n")
        assert DockerfileParser().can_parse(str(f))

    def test_rejects_non_dockerfile(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not DockerfileParser().can_parse(str(f))


class TestDockerfile1PluginParsesRealCode:
    def test_parses_minimal_dockerfile(self, tmp_path):
        path = tmp_path / "Dockerfile"
        path.write_text("FROM ubuntu\n")
        result = DockerfileParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_from_images(self, tmp_path):
        path = tmp_path / "Dockerfile"
        path.write_text(
            "FROM ubuntu:20.04\n"
            "FROM python:3.11-slim as builder\n"
        )
        result = DockerfileParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_env_args(self, tmp_path):
        path = tmp_path / "Dockerfile"
        path.write_text(
            "ENV DEBIAN_FRONTEND=noninteractive\n"
            "ARG BUILD_VERSION=1.0\n"
        )
        result = DockerfileParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_extracts_stages(self, tmp_path):
        path = tmp_path / "Dockerfile"
        path.write_text(
            "FROM ubuntu as base\n"
            "RUN apt-get update\n"
            "FROM base as final\n"
            "RUN echo 'done'\n"
        )
        result = DockerfileParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 2

    def test_stage_has_required_keys(self, tmp_path):
        path = tmp_path / "Dockerfile"
        path.write_text("FROM ubuntu as test\nRUN echo hi\n")
        result = DockerfileParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for stage in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in stage


class TestPluginIsDiscovered:
    def test_dockerfile1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, DockerfileParser) for p in plugins), (
            "dockerfile1 plugin missing PLUGIN export in __init__.py"
        )
