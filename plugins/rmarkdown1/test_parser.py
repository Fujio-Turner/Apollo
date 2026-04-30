"""Self-contained tests for the rmarkdown1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.rmarkdown1 import RMarkdownParser


class TestRMarkdownPluginDiscovery:
    def test_rmarkdown_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, RMarkdownParser) for p in plugins)


class TestRMarkdownPluginRecognisesExtension:
    def test_recognises_rmd_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "doc.Rmd"
            f.write_text("")
            assert RMarkdownParser().can_parse(str(f))

    def test_rejects_non_rmd_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("note.md", "page.html", "doc.txt"):
                f = Path(tmp) / name
                f.write_text("")
                assert not RMarkdownParser().can_parse(str(f))


class TestRMarkdownPluginParsesDocuments:
    def test_parses_valid_rmarkdown(self):
        content = """---
title: Test
---

# Introduction

```{r}
library(ggplot2)
x <- 42
```

More text

```{r}
y <- x * 2
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.Rmd"
            f.write_text(content)
            result = RMarkdownParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "functions" in result
        assert "imports" in result
        assert "variables" in result
        assert len(result["functions"]) > 0
        assert len(result["imports"]) > 0

    def test_returns_valid_for_minimal_file(self):
        content = "# Title\nNo code chunks here."
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.Rmd"
            f.write_text(content)
            result = RMarkdownParser().parse_file(str(f))

        assert result is not None
        assert "functions" in result
        assert "imports" in result


class TestRMarkdownPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m.Rmd"
            f.write_text("")
            parser = RMarkdownParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False

    def test_default_config_keeps_can_parse_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m.Rmd"
            f.write_text("")
            assert RMarkdownParser().can_parse(str(f)) is True
