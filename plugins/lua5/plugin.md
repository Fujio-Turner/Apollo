---
name: lua5
description: Lua 5 plugin for Apollo. Parses .lua files to extract functions, tables, require() statements, and module assignments.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/lua5
author: Fujio Turner
---

# lua5 Plugin

Parses Lua source files (`.lua`) to extract structural information.

## Extracted Elements

- **Functions**: `function name(...)` and `local function name(...)` definitions
- **Tables**: `name = { ... }` table assignments (extracted as classes)
- **Imports**: `require()` module imports
- **Variables**: Top-level variable assignments
- **Calls**: Function calls extracted from function bodies

## Graph Relationships

- Function calls within function bodies
- Module dependencies from require() statements
- Table membership (fields)

## File Pattern

Recognizes files with `.lua` extension.

## Configuration

No special options. All Lua files are indexed when enabled.
