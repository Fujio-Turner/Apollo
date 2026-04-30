---
name: org1
description: Org Mode plugin for Apollo. Parses .org files to extract headings, links, code blocks, properties, and TODO/task items.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/org1
author: Fujio Turner
---

# org1 Plugin

Parses Org mode files (`.org`) to extract document structure and references.

## Extracted Elements

- **Headings**: Document outline with heading levels (*, **, ***, etc.)
- **Links**: `[[link]]` and `[[link][description]]` format links
- **URLs**: Plain http/https URLs
- **Code Blocks**: `#+BEGIN_SRC ... #+END_SRC` code blocks
- **Properties**: `#+PROPERTY:`, `#+TITLE:`, `#+AUTHOR:` metadata
- **Tags**: Heading tags (`:tag1:tag2:`)

## Graph Relationships

- Document structure and heading hierarchy
- Cross-document and external links
- Code block references
- Metadata and properties

## File Pattern

Recognizes files with `.org` extension.

## Configuration

No special options. All Org files are indexed when enabled.
