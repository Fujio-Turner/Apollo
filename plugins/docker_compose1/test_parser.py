"""Self-contained tests for docker_compose1 plugin."""
from __future__ import annotations

from plugins.docker_compose1 import DockerComposeParser
from apollo.plugins import discover_plugins


class TestDockerCompose1PluginDiscovery:
    def test_recognises_docker_compose_yml(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text("version: '3'\n")
        assert DockerComposeParser().can_parse(str(f))

    def test_recognises_docker_compose_yaml(self, tmp_path):
        f = tmp_path / "docker-compose.yaml"
        f.write_text("version: '3'\n")
        assert DockerComposeParser().can_parse(str(f))

    def test_rejects_other_files(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("hi: world\n")
        assert not DockerComposeParser().can_parse(str(f))


class TestDockerCompose1PluginParsesRealCode:
    def test_parses_minimal_compose(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text("version: '3'\nservices:\n  web:\n    image: nginx\n")
        result = DockerComposeParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_services(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            "version: '3'\n"
            "services:\n"
            "  web:\n"
            "    image: nginx\n"
            "  db:\n"
            "    image: postgres\n"
        )
        result = DockerComposeParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 2

    def test_extracts_images(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            "version: '3'\n"
            "services:\n"
            "  web:\n"
            "    image: nginx:latest\n"
            "  db:\n"
            "    image: postgres:13\n"
        )
        result = DockerComposeParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_service_has_required_keys(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            "version: '3'\n"
            "services:\n"
            "  web:\n"
            "    image: nginx\n"
        )
        result = DockerComposeParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for svc in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in svc


class TestPluginIsDiscovered:
    def test_docker_compose1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, DockerComposeParser) for p in plugins), (
            "docker_compose1 plugin missing PLUGIN export in __init__.py"
        )
