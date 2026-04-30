---
name: openapi3
description: OpenAPI 3 plugin for Apollo. Parses OpenAPI 3.x YAML/JSON specs to extract endpoints, schemas, parameters, and $ref relationships for API documentation integration.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/openapi3
author: Fujio Turner
---

# OpenAPI 3 Plugin

Parser for OpenAPI 3.x specification files (`.yaml`, `.yml`, `.json` with `openapi` or `swagger` in filename).
Extracts:

- **Variables**: API title, endpoints (paths), schema definitions
- **Imports**: `$ref` schema references, components links
- **Structure**: Server info, security schemes, request/response models

Enables queries like:
- "Show all API endpoints across specs"
- "Find schema definitions and their references"
- "What parameters are used in the `/users` endpoint?"
