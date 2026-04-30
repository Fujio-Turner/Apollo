---
name: cmake1
description: CMake plugin for Apollo. Parses CMakeLists.txt and .cmake files to extract targets, functions, variables, includes, and module dependencies.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/cmake1
author: Fujio Turner
---

# cmake1 Plugin

Parses CMake files (CMakeLists.txt, .cmake) to extract build information.

## Extracted Elements

- **Targets**: `add_executable()`, `add_library()`, `add_custom_target()` definitions
- **Dependencies**: `target_link_libraries()` dependencies between targets
- **Includes**: `include()` directives for CMake modules
- **Variables**: `set()` variable definitions

## Graph Relationships

- Target definitions
- Target-to-target dependencies
- CMake module includes
- Linked library references

## File Pattern

Recognizes files named:
- `CMakeLists.txt`
- Plus `.cmake` extension

## Configuration

No special options. All CMake files are indexed when enabled.
