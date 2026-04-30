---
name: yaml1
description: YAML file plugin for Apollo. Parses .yaml/.yml files to extract keys, anchors/aliases, !include directives, and references for configuration documentation.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/yaml1
author: Fujio Turner
---

# YAML 1 Plugin

Parser for YAML files (`.yaml`, `.yml`). Extracts:

- **Variables**: Top-level keys, anchors (`&name`), aliases (`*name`)
- **Imports**: `!include` directives, `$ref` / `ref:` references
- **Structure**: Key hierarchies, aliases for DRY config

Enables queries like:
- "Show all YAML files with a `services:` section"
- "Find all !include directives and their targets"
- "What configuration anchors are defined?"
