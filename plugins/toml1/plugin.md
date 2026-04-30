---
name: toml1
description: TOML file plugin for Apollo. Parses .toml files to extract tables, keys, and dependency lists from Rust Cargo.toml, Python pyproject.toml, and other TOML configurations.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/toml1
author: Fujio Turner
---

# TOML 1 Plugin

Parser for TOML configuration files (`.toml`). Extracts:

- **Variables**: Top-level keys, table names, configuration parameters
- **Imports**: Dependencies from `[dependencies]`, `[dev-dependencies]`, `[project.dependencies]`
- **Structure**: Tables, key-value pairs, nested structures

Enables queries like:
- "Show all Cargo.toml dependency specifications"
- "Find all pyproject.toml files with a `[tool.poetry]` section"
- "What are the most-used dependencies across TOML files?"
