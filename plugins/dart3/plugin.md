---
name: dart3
description: Dart 3 plugin for Apollo. Parses .dart files to extract classes, methods, functions, imports/exports, and call sites with null-safety and async markers.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/dart3
author: Fujio Turner
---

# dart3 Plugin

Parses Dart source files (`.dart`) to extract structural information.

## Extracted Elements

- **Classes**: `class Name` definitions with extends/implements relationships
- **Functions**: Top-level function definitions
- **Imports**: `import` and `export` statements with package references
- **Variables**: Top-level `var`, `final`, `const` declarations
- **Methods**: Instance and static methods within classes
- **Calls**: Function and method calls extracted from function/method bodies

## Graph Relationships

- Class inheritance via extends/implements
- Function calls within functions
- Module dependencies from imports

## File Pattern

Recognizes files with `.dart` extension.

## Configuration

No special options. All Dart files are indexed when enabled.
