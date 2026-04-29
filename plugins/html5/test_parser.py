"""Smoke tests for the html5 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.html5 import HtmlParser


SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Apollo HTML5 Test</title>
  <meta name="description" content="A small fixture for the html5 plugin.">
  <meta property="og:title" content="Apollo">
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/app.js"></script>
  <style>body { background: #111; }</style>
</head>
<body>
  <!-- TODO: replace placeholder copy before launch -->
  <h1>Top Level</h1>
  <p>Intro paragraph with an <a href="https://example.com">external link</a>
     and an <a href="#section-2">anchor link</a>.</p>

  <h2>Subsection A</h2>
  <p>Some text and an image: <img src="/img/logo.png" alt="logo"></p>
  <pre><code class="language-rust">fn main() { println!("hi"); }</code></pre>

  <h3>Deeper</h3>
  <p>Even more text under Deeper.</p>

  <h2 id="section-2">Subsection B</h2>
  <p>Body of B.</p>
  <script type="text/javascript">console.log("hello");</script>
</body>
</html>
"""


class TestHtml5PluginDiscovery:
    def test_html5_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, HtmlParser) for p in plugins)


class TestHtml5PluginRecognisesExtension:
    def test_recognises_html_extension(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text(SAMPLE_HTML)
        assert HtmlParser().can_parse(str(f))

    def test_recognises_htm_extension(self, tmp_path):
        f = tmp_path / "page.htm"
        f.write_text(SAMPLE_HTML)
        assert HtmlParser().can_parse(str(f))

    def test_rejects_non_html_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not HtmlParser().can_parse(str(f))


class TestHtml5PluginParsesRealHtml:
    def test_parses_sample_html(self, tmp_path):
        path = tmp_path / "page.html"
        path.write_text(SAMPLE_HTML)
        result = HtmlParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)

        # Code-shape keys we don't populate stay empty.
        for key in ("functions", "classes", "variables"):
            assert result[key] == []

        # Title.
        assert result["title"] == "Apollo HTML5 Test"

        # Sections — three h2/h3 children plus the h1.
        names = [s["name"] for s in result["sections"]]
        levels = [s["level"] for s in result["sections"]]
        assert names == ["Top Level", "Subsection A", "Deeper", "Subsection B"]
        assert levels == [1, 2, 3, 2]

        # Parent hierarchy.
        parents = {s["name"]: s["parent_section"] for s in result["sections"]}
        assert parents["Top Level"] is None
        assert parents["Subsection A"] == "Top Level"
        assert parents["Deeper"] == "Subsection A"
        assert parents["Subsection B"] == "Top Level"

        # Section content — the *body text* between headings, not just
        # the heading title. h1 "Top Level" wraps everything.
        contents = {s["name"]: s["content"] for s in result["sections"]}
        assert "Intro paragraph" in contents["Top Level"]
        assert "Body of B" in contents["Top Level"]  # nested coverage
        assert "Some text and an image" in contents["Subsection A"]
        assert "Even more text under Deeper" in contents["Deeper"]
        assert "Body of B" in contents["Subsection B"]
        # Heading text itself shouldn't leak into its own content.
        assert "Subsection A" not in contents["Subsection A"]
        # Code blocks (script/style/pre) don't leak into prose either.
        assert "console.log" not in contents["Top Level"]
        assert "background: #111" not in contents["Top Level"]
        assert "fn main" not in contents["Subsection A"]

        # Links — external + anchor + stylesheet link + image.
        urls = {l["url"] for l in result["links"]}
        assert "https://example.com" in urls
        assert "#section-2" in urls
        assert "/static/app.css" in urls
        assert "/img/logo.png" in urls

        link_types = {l["url"]: l["link_type"] for l in result["links"]}
        assert link_types["https://example.com"] == "external"
        assert link_types["#section-2"] == "anchor"
        assert link_types["/static/app.css"] == "internal"

        images = [l for l in result["links"] if l["is_image"]]
        assert len(images) == 1
        assert images[0]["text"] == "logo"

        # Imports — asset graph edges.
        imp_modules = {i["module"]: i["kind"] for i in result["imports"]}
        assert imp_modules["/static/app.css"] == "stylesheet"
        assert imp_modules["/static/app.js"] == "script"
        assert imp_modules["/img/logo.png"] == "image"

        # Code blocks — one <style>, one <script>, one <pre><code>.
        langs = sorted(b["language"] for b in result["code_blocks"])
        assert langs == ["css", "javascript", "rust"]
        contents_by_lang = {b["language"]: b["content"] for b in result["code_blocks"]}
        assert "background: #111" in contents_by_lang["css"]
        assert "console.log" in contents_by_lang["javascript"]
        assert "fn main" in contents_by_lang["rust"]

        # HTML comments — TODO/FIXME/etc are surfaced.
        assert any(
            c["tag"] == "TODO" and "placeholder" in c["content"]
            for c in result["comments"]
        )

        # Meta tags.
        meta_names = {m["name"]: m["content"] for m in result["meta"]}
        assert meta_names["description"] == "A small fixture for the html5 plugin."
        assert meta_names["og:title"] == "Apollo"

        # Whole-document entry — visible text, not raw tag soup.
        assert len(result["documents"]) == 1
        doc = result["documents"][0]
        assert doc["doc_type"] == "html"
        assert "Intro paragraph" in doc["content"]
        # Tags themselves shouldn't appear in the visible-text body.
        assert "<p>" not in doc["content"]
        assert "<script" not in doc["content"]
        # Script body shouldn't either.
        assert "console.log" not in doc["content"]

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.html"
        f.write_text("   \n  \n")
        assert HtmlParser().parse_file(str(f)) is None

    def test_returns_none_for_wrong_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text(SAMPLE_HTML)
        assert HtmlParser().parse_file(str(f)) is None
