---
name: scala3
description: Scala 3 plugin for Apollo. Parses .scala files to extract classes, objects, traits, methods, import statements, and call sites.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/scala3
author: Fujio Turner
---

# scala3 Plugin

Parses Scala 3 source files (`.scala`) to extract structural information.

## Extracted Elements

- **Functions**: `def name(...)` definitions with source and call sites
- **Classes**: `class`, `object`, `trait`, `case class` definitions
- **Imports**: `import x.y.z` statements  
- **Variables**: Top-level `val` and `var` assignments

## Graph Relationships

- Function calls extracted from function bodies
- Class hierarchies via extends/with keywords (bases)
- Methods attached to their defining classes

## File Pattern

Recognizes files with `.scala` extension.

## Configuration

No special options. All Scala files are indexed when enabled.
