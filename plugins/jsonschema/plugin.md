---
name: jsonschema
description: JSON Schema plugin for Apollo. Parses .schema.json files to extract schema definitions, $ref relationships, type hierarchies, and property specifications for data validation documentation.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/jsonschema
author: Fujio Turner
---

# JSON Schema Plugin

Parser for JSON Schema definition files (`.schema.json`). Extracts:

- **Variables**: Schema title, `$id`, property names, definitions
- **Imports**: `$ref` references between schemas, external schema links
- **Structure**: Type hierarchies, required fields, constraints

Enables queries like:
- "Show all schema definitions in this file"
- "Find all $ref links between schemas"
- "What properties are required in the User schema?"
