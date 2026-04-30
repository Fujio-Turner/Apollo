---
name: rst1
description: reStructuredText plugin for Apollo. Parses .rst files to extract sections, directives, internal/external links, code blocks, and Sphinx cross-references.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/rst1
author: Fujio Turner
---

# rst1 Plugin

Parses reStructuredText files (`.rst`) to extract document structure and references.

## Extracted Elements

- **Sections**: Document headings and their hierarchy
- **References**: Internal references (`:ref:`), external links, URLs
- **Code Blocks**: Indented code blocks following `::`
- **Directives**: RST directives (.. directive::)

## Graph Relationships

- Document sections and hierarchy
- Cross-document references
- External link references
- Code block references

## File Pattern

Recognizes files with `.rst` extension.

## Configuration

No special options. All RST files are indexed when enabled.
