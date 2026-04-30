---
name: json1
description: JSON file plugin for Apollo. Parses .json files to extract top-level keys as variables, detects $ref schema references, and analyzes JSON structure for knowledge graph integration.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/json1
author: Fujio Turner
---

# JSON 1 Plugin

Parser for JSON files (`.json`). Extracts:

- **Variables**: Top-level keys in the JSON object
- **Imports**: `$ref` references for schema relationships
- **Structure**: Keys, nesting depth, and schema patterns

Enables queries like:
- "Show all JSON files with a `components` key"
- "Find all schema references across JSON files"
- "What JSON structures define `endpoints`?"
