---
name: html5
description: HTML5 plugin for Apollo. Uses the Python standard library `html.parser` (zero third-party deps) to extract `<title>`, h1–h6 sections with body text and parent/child hierarchy, asset import edges (`<script src>`, `<link rel=stylesheet>`, `<img>`, `<iframe>`, `<source>`), inline `<script>` / `<style>` / `<pre><code class="language-…">` code blocks, anchor + image links, `<meta>` tags, and tagged `<!-- TODO/FIXME -->` HTML comments from `.html` / `.htm` / `.xhtml` files.
version: 1.1.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/html5
author: Fujio Turner
---

# HTML5 Plugin

Built-in plugin. Targets `.html`, `.htm`, and `.xhtml` files using the
HTML5 dialect (the stdlib `html.parser` is forgiving enough to handle
most real-world HTML, including soup-y / non-strict markup).

## What it extracts

| Result key     | Source HTML                                                                       |
| -------------- | --------------------------------------------------------------------------------- |
| `title`        | `<title>` element                                                                 |
| `sections`     | `<h1>`–`<h6>` with parent/child hierarchy *and the body text between headings*    |
| `imports`      | `<script src>`, `<link rel=stylesheet/preload/icon>`, `<img>`, `<iframe>`, `<source>` / `<video>` / `<audio>` (the asset graph) |
| `links`        | `<a href>`, `<link href>`, `<img src>`                                            |
| `code_blocks`  | `<script>` (JS), `<style>` (CSS), and `<pre><code class="language-…">` blocks      |
| `meta`         | `<meta name\|property\|http-equiv content>`                                       |
| `comments`     | Tagged HTML comments — `<!-- TODO/FIXME/NOTE/HACK/XXX … -->`                      |
| `documents`    | Whole-file entry whose `content` is the **visible text** (no tag soup) for embeddings |

## Why stdlib over BeautifulSoup / lxml?

`html.parser` is part of the standard library, so this plugin has
**zero third-party dependencies** and works on any Python install — no
extra `pip install`, no compiled C extensions. It is also a clean
reference for the "structured markup parsed with built-ins" pattern,
sitting alongside `markdown_gfm` (third-party AST) and `pdf_pypdf`
(third-party binary) as the third style of plugin.

## Why `imports` for HTML?

Apollo's graph builder draws cross-file edges from each language's
`imports`. Treating `<script src>`, `<link rel=stylesheet>`, `<img>`,
and friends as imports means an HTML page becomes a first-class node
in that same graph: you can see at a glance which CSS, JS, image, and
media files a page pulls in — exactly the way you'd see Python module
dependencies.

## Why visible-text in `documents` instead of raw HTML?

The embeddings pipeline indexes `documents[0].content`. Embedding raw
HTML wastes tokens on `<div class="...">` boilerplate and rarely helps
retrieval; the rendered, user-visible text is what semantic search
actually needs to match on. Raw HTML is still on disk and still
parsed for structured fields — only the embedding payload is cleaned.

This file is the plugin's **manifest**. The header above (everything
between the `---` markers) is parsed by Apollo to populate the
**Settings → Plugins** tab. The body below is free-form documentation.
