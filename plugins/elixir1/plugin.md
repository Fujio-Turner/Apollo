---
name: elixir1
description: Elixir plugin for Apollo. Parses .ex/.exs files to extract modules, def/defp functions, import/alias statements, pipe chains, and macro/guard patterns.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/elixir1
author: Fujio Turner
---

# elixir1 Plugin

Parses Elixir source files (`.ex` or `.exs`) to extract structural information.

## Extracted Elements

- **Modules**: `defmodule Name do ... end` definitions
- **Functions**: `def name/arity`, `defp name/arity`, `defmacro name/arity` definitions
- **Imports**: `import`, `alias`, `require` statements with module references
- **Variables**: Module attributes (`@name = value`)
- **Calls**: Function calls and pipe operations (`|>`) extracted from function bodies

## Graph Relationships

- Function definitions within modules
- Module dependencies from imports/alias/require
- Function call chains and pipe operations

## File Pattern

Recognizes files with `.ex` or `.exs` extension.

## Configuration

No special options. All Elixir files are indexed when enabled.
