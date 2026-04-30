"""Self-contained tests for the terraform1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.terraform1 import TerraformParser


class TestTerraformPluginDiscovery:
    def test_terraform_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, TerraformParser) for p in plugins)


class TestTerraformPluginRecognisesExtension:
    def test_recognises_tf_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "main.tf"
            f.write_text("")
            assert TerraformParser().can_parse(str(f))

    def test_rejects_non_tf_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.yaml"
            f.write_text("")
            assert not TerraformParser().can_parse(str(f))


class TestTerraformPluginParsesTerraform:
    def test_parses_valid_terraform(self):
        content = """
variable "region" {
  type = string
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

data "aws_ami" "ubuntu" {
  most_recent = true
}

module "vpc" {
  source = "./modules/vpc"
  region = var.region
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "main.tf"
            f.write_text(content)
            result = TerraformParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "functions" in result
        assert "variables" in result
        assert "imports" in result
        assert len(result["functions"]) > 0
        assert len(result["variables"]) > 0

    def test_returns_valid_for_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "empty.tf"
            f.write_text("")
            result = TerraformParser().parse_file(str(f))

        assert result is not None
        assert "functions" in result


class TestTerraformPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "main.tf"
            f.write_text("")
            parser = TerraformParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
