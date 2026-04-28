---
name: html5
description: HTML5 plugin for Apollo. Uses the Python standard library `html.parser` (zero third-party deps) to extract `<title>`, h1–h6 sections with parent/child hierarchy, anchor and image links, inline `<script>` / `<style>` code blocks, and `<meta>` tags from `.html` / `.htm` / `.xhtml` files.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/html5
author: Fujio Turner
---

# HTML5 Plugin

Built-in plugin. Targets `.html`, `.htm`, and `.xhtml` files using the
HTML5 dialect (the stdlib `html.parser` is forgiving enough to handle
most real-world HTML, including soup-y / non-strict markup).

## What it extracts

| Result key     | Source HTML                                   |
| -------------- | --------------------------------------------- |
| `title`        | `<title>` element                             |
| `sections`     | `<h1>`–`<h6>` with parent/child hierarchy     |
| `links`        | `<a href>`, `<link href>`, `<img src>`        |
| `code_blocks`  | `<script>` (JS), `<style>` (CSS)              |
| `meta`         | `<meta name|property|http-equiv content>`     |
| `documents`    | Whole-file entry for the embeddings pipeline  |

## Why stdlib over BeautifulSoup / lxml?

`html.parser` is part of the standard library, so this plugin has
**zero third-party dependencies** and works on any Python install — no
extra `pip install`, no compiled C extensions. It is also a clean
reference for the "structured markup parsed with built-ins" pattern,
sitting alongside `markdown_gfm` (third-party AST) and `pdf_pypdf`
(third-party binary) as the third style of plugin.

This file is the plugin's **manifest**. The header above (everything
between the `---` markers) is parsed by Apollo to populate the
**Settings → Plugins** tab. The body below is free-form documentation.
