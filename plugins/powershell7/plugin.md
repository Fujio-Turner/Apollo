---
name: powershell7
description: PowerShell 7 plugin for Apollo. Parses .ps1 files to extract function definitions, dot-sourced scripts, cmdlet calls, module imports, and parameter declarations.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/powershell7
author: Fujio Turner
---

# powershell7 Plugin

Parses PowerShell scripts (`.ps1`) to extract structural information.

## Extracted Elements

- **Functions**: `function name { ... }` definitions with parameters
- **Imports**: Dot-source statements (`. .\file.ps1`)
- **Variables**: `$name = value` assignments
- **Calls**: Cmdlet calls (Get-ChildItem, New-Item, etc.) extracted from function bodies

## Graph Relationships

- Function definitions
- Sourced script dependencies
- Cmdlet execution chains

## File Pattern

Recognizes files with `.ps1` extension.

## Configuration

No special options. All PowerShell files are indexed when enabled.
