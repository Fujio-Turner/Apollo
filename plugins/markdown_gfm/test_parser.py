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

<!-- TODO: replace placeholder copy before launch -->

# Top Heading

Intro paragraph with an [external link](https://example.com), an
[anchor link](#sub-a), a cross-doc link to [the guide](./guide.md),
and a wiki-link to [[Other Page]] plus [[Aliased|nice name]].

## Sub A {#explicit-id}

Some text and an image: ![logo](/img/logo.png)

> [!WARNING] Heads up
> This is a GFM callout. Be careful.

```python
print("hi")
# TODO: this is inside a code block, should NOT be a comment
[[not a wikilink either]]
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

        # Code-shape keys we don't populate stay empty.
        for key in ("functions", "classes", "variables"):
            assert result[key] == []

        # Frontmatter / title.
        assert result["title"] == "Apollo Markdown Test"
        assert result["frontmatter"]["tags"] == ["demo", "plugin"]

        # Sections — "Sub A {#explicit-id}" should NOT keep the suffix
        # in its name; the anchor should be on its own field.
        names = [s["name"] for s in result["sections"]]
        assert names == ["Top Heading", "Sub A", "Sub B"]
        levels = [s["level"] for s in result["sections"]]
        assert levels == [1, 2, 2]

        anchors = {s["name"]: s["anchor"] for s in result["sections"]}
        assert anchors["Top Heading"] == "top-heading"  # auto-slug
        assert anchors["Sub A"] == "explicit-id"        # explicit kramdown
        assert anchors["Sub B"] == "sub-b"              # auto-slug

        # Code blocks.
        langs = [b["language"] for b in result["code_blocks"]]
        assert "python" in langs

        # Links — external + anchor + image + cross-doc.
        urls = {l["url"] for l in result["links"]}
        assert "https://example.com" in urls
        assert "#sub-a" in urls
        assert "/img/logo.png" in urls
        assert "./guide.md" in urls

        # Wikilinks — both plain and aliased forms.
        wl_targets = {(w["target"], w["alias"]) for w in result["wikilinks"]}
        assert ("Other Page", None) in wl_targets
        assert ("Aliased", "nice name") in wl_targets
        # Wikilink syntax inside a code fence must NOT be picked up.
        assert all(w["target"] != "not a wikilink either" for w in result["wikilinks"])

        # Imports — typed edges for the doc graph.
        imps = {(i["module"], i["kind"]) for i in result["imports"]}
        assert ("./guide.md", "doc") in imps
        assert ("/img/logo.png", "image") in imps
        assert ("Other Page", "wikilink") in imps
        assert ("Aliased", "wikilink") in imps
        # External and anchor links must NOT be promoted to imports.
        assert all(i["module"] != "https://example.com" for i in result["imports"])
        assert all(i["module"] != "#sub-a" for i in result["imports"])

        # Comments — HTML comment with TODO surfaces; the in-fence one does NOT.
        todos = [c for c in result["comments"] if c["tag"] == "TODO"]
        assert any("placeholder" in c["content"] for c in todos)
        assert all("inside a code block" not in c["content"] for c in todos)

        # Callouts — GFM `> [!WARNING]` block.
        assert len(result["callouts"]) >= 1
        co = result["callouts"][0]
        assert co["kind"] == "WARNING"
        assert co["title"] == "Heads up"
        assert "Be careful" in co["content"]

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


class TestMarkdownGfmPluginConfig:
    """Phase 2A: parser receives its merged config and respects ``enabled``."""

    def test_disabled_plugin_can_parse_returns_false(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# hi\n")
        parser = MarkdownParser(config={"enabled": False})
        assert parser.can_parse(str(f)) is False

    def test_extract_links_toggle_off_yields_empty_links(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# T\n\n[ext](https://example.com)\n")
        parser = MarkdownParser(config={"extract_links": False})
        result = parser.parse_file(str(f))
        assert result is not None
        assert result["links"] == []
