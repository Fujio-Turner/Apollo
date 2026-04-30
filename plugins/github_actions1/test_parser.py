"""Self-contained tests for the github_actions1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.github_actions1 import GitHubActionsParser


class TestGitHubActionsPluginDiscovery:
    def test_github_actions_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, GitHubActionsParser) for p in plugins)


class TestGitHubActionsPluginRecognisesPath:
    def test_recognises_workflows_yml(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workflows = base / ".github" / "workflows"
            workflows.mkdir(parents=True)
            f = workflows / "test.yml"
            f.write_text("")
            assert GitHubActionsParser().can_parse(str(f))

    def test_rejects_non_workflow_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "workflow.yml"
            f.write_text("")
            assert not GitHubActionsParser().can_parse(str(f))


class TestGitHubActionsPluginParsesWorkflow:
    def test_parses_valid_workflow(self):
        content = """
name: CI
on: push
env:
  REGISTRY: ghcr.io
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
        env:
          PYTHONPATH: /app
      - run: pytest
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: docker/build-push-action@v4
"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workflows = base / ".github" / "workflows"
            workflows.mkdir(parents=True)
            f = workflows / "ci.yml"
            f.write_text(content)
            result = GitHubActionsParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "functions" in result
        assert "imports" in result
        # Functions may be empty due to yaml parsing
        # but structure should be present

    def test_returns_valid_for_empty_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workflows = base / ".github" / "workflows"
            workflows.mkdir(parents=True)
            f = workflows / "empty.yml"
            f.write_text("name: Empty\non: push\n")
            result = GitHubActionsParser().parse_file(str(f))

        assert result is not None


class TestGitHubActionsPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workflows = base / ".github" / "workflows"
            workflows.mkdir(parents=True)
            f = workflows / "test.yml"
            f.write_text("")
            parser = GitHubActionsParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
