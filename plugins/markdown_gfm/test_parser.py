"""Self-contained smoke tests for the markdown_gfm plugin.

These tests live **inside the plugin folder** so removing the plugin is
one ``rm -rf plugins/markdown_gfm/`` away. The exhaustive parser unit
tests (many fixtures, edge cases) still live under ``tests/`` for now —
this file is the discoverability + contract check.
"""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.markdown_gfm import MarkdownParser


SAMPLE_MD = """---
title: Apollo Markdown Test
tags: [demo, plugin]
---

# Top Heading

Intro paragraph with an [external link](https://example.com) and an
[anchor link](#sub-a).

## Sub A

Some text and an image: ![logo](/img/logo.png)

```python
print("hi")
```

| Col 1 | Col 2 |
| ----- | ----- |
| a     | b     |

- [x] done item
- [ ] open item

## Sub B
"""


class TestMarkdownGfmPluginDiscovery:
    def test_markdown_gfm_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, MarkdownParser) for p in plugins)


class TestMarkdownGfmPluginRecognisesExtension:
    def test_recognises_md_extension(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# hi\n")
        assert MarkdownParser().can_parse(str(f))

    def test_recognises_markdown_extension(self, tmp_path):
        f = tmp_path / "doc.markdown"
        f.write_text("# hi\n")
        assert MarkdownParser().can_parse(str(f))

    def test_rejects_non_markdown_extension(self, tmp_path):
        for name in ("page.html", "src.py", "doc.txt"):
            f = tmp_path / name
            f.write_text("x")
            assert not MarkdownParser().can_parse(str(f))


class TestMarkdownGfmPluginParsesRealMarkdown:
    def test_parses_sample_markdown(self, tmp_path):
        path = tmp_path / "doc.md"
        path.write_text(SAMPLE_MD)
        result = MarkdownParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)

        # Required code-shape keys are present and empty.
        for key in ("functions", "classes", "imports", "variables"):
            assert result[key] == []

        # Frontmatter / title.
        assert result["title"] == "Apollo Markdown Test"
        assert result["frontmatter"]["tags"] == ["demo", "plugin"]

        # Sections.
        names = [s["name"] for s in result["sections"]]
        assert names == ["Top Heading", "Sub A", "Sub B"]
        levels = [s["level"] for s in result["sections"]]
        assert levels == [1, 2, 2]

        # Code blocks.
        langs = [b["language"] for b in result["code_blocks"]]
        assert "python" in langs

        # Links — external + anchor + image.
        urls = {l["url"] for l in result["links"]}
        assert "https://example.com" in urls
        assert "#sub-a" in urls
        assert "/img/logo.png" in urls

        # Tables and tasks.
        assert len(result["tables"]) == 1
        assert result["tables"][0]["headers"] == ["Col 1", "Col 2"]
        checked = {t["text"]: t["checked"] for t in result["task_items"]}
        assert checked.get("done item") is True
        assert checked.get("open item") is False

        # Whole-document entry.
        assert len(result["documents"]) == 1
        assert result["documents"][0]["doc_type"] == "markdown"

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("   \n  \n")
        assert MarkdownParser().parse_file(str(f)) is None
