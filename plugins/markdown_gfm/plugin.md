---
name: markdown_gfm
description: GitHub Flavored Markdown plugin for Apollo. Uses `mistune` (with the `table` and `task_lists` extensions) plus `python-frontmatter` to extract sections (with body content, parent/child hierarchy, and anchor ids), code blocks, links, cross-doc imports, `[[wikilinks]]`, GFM/MkDocs/Pandoc callouts, tagged TODO/FIXME comments, tables, task items, and YAML/TOML frontmatter from `.md` / `.markdown` files.
version: 1.1.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/markdown_gfm
author: Fujio Turner
---

# Markdown (GFM) Plugin

Built-in reference plugin. Targets `.md` / `.markdown` files using the
GitHub Flavored Markdown dialect.

## What it extracts

| Result key     | Source markdown                                                                       |
| -------------- | ------------------------------------------------------------------------------------- |
| `title`        | Frontmatter `title:` if present, otherwise the first H1                               |
| `frontmatter`  | YAML / TOML frontmatter block (parsed via `python-frontmatter`)                       |
| `sections`     | `#`–`######` headings with **body content**, parent/child hierarchy, and `anchor` ids |
| `imports`      | Cross-doc edges: relative links to `.md`, image / asset references, and `[[wikilinks]]` |
| `links`        | All inline links + images (`[text](url)` / `![alt](src)`)                             |
| `wikilinks`    | `[[target]]` / `[[target\|alias]]` (Obsidian / Foam / GitHub wikis)                    |
| `code_blocks`  | Fenced + indented code blocks, with language info-string                              |
| `callouts`     | GFM `> [!NOTE]`, MkDocs `!!! warning`, Pandoc `:::note … :::` admonitions             |
| `comments`     | Tagged `<!-- TODO/FIXME/NOTE/HACK/XXX … -->` and `> TODO:` blockquote markers          |
| `tables`       | GFM pipe tables (headers + rows)                                                      |
| `task_items`   | `- [x]` / `- [ ]` checkbox items                                                      |
| `documents`    | Whole-file entry for the embeddings pipeline                                          |

## Why `imports` for markdown?

Apollo's graph builder draws cross-file edges from each language's
`imports`. Promoting relative links to other `.md` files, image and
asset references, and `[[wikilinks]]` into `imports` (each tagged with
a `kind` of `doc` / `image` / `stylesheet` / `script` / `asset` /
`wikilink`) means a Markdown file becomes a first-class node in that
graph: README → architecture.md → guides/foo.md edges show up the way
you'd see Python module dependencies. External `http(s)://` links and
same-page anchors are deliberately *not* imports — they're noise for
the cross-file graph.

## Why callouts and wikilinks?

Both are markdown-native idioms that mistune doesn't structure for us
but that carry an outsized share of the *important* signal in real
docs:

* **Callouts / admonitions** are how docs flag breaking changes,
  security warnings, gotchas, and tips. Indexing them lets you query
  "every WARNING across the docs" or weight callout text more heavily
  in search.
* **Wikilinks** are the primary linking syntax in Obsidian, Foam, Bear,
  GitHub wikis, and many personal-knowledge-base setups. Without them,
  the doc graph for those projects looks empty.

## Heading anchors

Each section carries an `anchor` id. We honour explicit
`## Foo {#custom-id}` (kramdown / pandoc) when present, and otherwise
auto-slug the heading text using GitHub's lowercase-with-hyphens rule.
This lets the graph layer resolve `[link](other.md#some-id)` cross-doc
anchors against actual headings.

## Why mistune over markdown-it / commonmark.py?

mistune produces a simple list-of-dicts AST, has solid GFM coverage via
its `table` and `task_lists` plugins, and is fast — a good fit for the
"structured markup parsed with a third-party AST library" reference
plugin, sitting alongside `html5` (stdlib parser) and `pdf_pypdf`
(third-party binary) as the second style of plugin.

This file is the plugin's **manifest**. The header above (everything
between the `---` markers) is parsed by Apollo to populate the
**Settings → Plugins** tab. The body below is free-form documentation.
