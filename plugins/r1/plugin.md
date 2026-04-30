---
name: r1
description: R plugin for Apollo. Parses .R/.r files to extract functions defined via <- and =, library()/require() calls, and assignments.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/r1
author: Fujio Turner
---

# r1 Plugin

Parses R language source files (`.R` or `.r`) to extract structural information.

## Extracted Elements

- **Functions**: `name <- function(...)` definitions with source and call sites
- **Imports**: `library()` and `require()` statements
- **Variables**: Top-level variable assignments using `<-` or `=`
- **Calls**: Function calls extracted from function bodies

## Graph Relationships

- Function calls within function bodies
- Import dependencies from library/require statements

## File Pattern

Recognizes files with `.R` or `.r` extension.

## Configuration

No special options. All R files are indexed when enabled.
