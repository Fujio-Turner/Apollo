---
name: shell1
description: Bash/Shell plugin for Apollo. Parses .sh/.bash files to extract function definitions, sourced files (`. file.sh`), called commands, and variables.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/shell1
author: Fujio Turner
---

# shell1 Plugin

Parses shell/bash scripts (`.sh`, `.bash`) to extract structural information.

## Extracted Elements

- **Functions**: `function_name() { ... }` or `function name { ... }` definitions
- **Imports**: `source` and `.` (dot-source) file references
- **Variables**: `NAME=value`, `export NAME=value`, `local NAME=value` assignments
- **Calls**: Command calls extracted from function bodies

## Graph Relationships

- Function definitions
- Sourced file dependencies
- Command execution chains

## File Pattern

Recognizes files with `.sh` or `.bash` extension, plus any executable with bash/sh shebang.

## Configuration

No special options. All shell scripts are indexed when enabled.
