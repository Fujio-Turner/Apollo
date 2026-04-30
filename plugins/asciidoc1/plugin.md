---
name: asciidoc1
description: AsciiDoc plugin for Apollo. Parses .adoc/.asciidoc files to extract sections, includes, cross-references, links, code blocks, and document attributes.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/asciidoc1
author: Fujio Turner
---

# asciidoc1 Plugin

Parses AsciiDoc files (`.adoc`, `.asciidoc`) to extract document structure and references.

## Extracted Elements

- **Sections**: Document headings at various levels (= == === etc.)
- **Includes**: `include::file[]` directives for file includes
- **Cross-references**: `xref:id[text]` internal cross-references
- **Links**: `link:url[text]` external links
- **Code Blocks**: `[source,language]` code block content
- **Attributes**: `:attribute: value` document attribute definitions

## Graph Relationships

- Document sections and hierarchy
- Cross-document includes
- Linked references
- Cross-reference relationships

## File Pattern

Recognizes files with `.adoc` or `.asciidoc` extension.

## Configuration

No special options. All AsciiDoc files are indexed when enabled.
