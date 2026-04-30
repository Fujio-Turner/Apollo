---
name: dockerfile1
description: Dockerfile plugin for Apollo. Parses Dockerfile files to extract FROM image references, RUN/COPY/ADD steps, ENV/ARG variables, and multi-stage build relationships.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/dockerfile1
author: Fujio Turner
---

# dockerfile1 Plugin

Parses Dockerfile to extract structural and dependency information.

## Extracted Elements

- **Stages**: Multi-stage build definitions (FROM ... AS stage_name)
- **Operations**: RUN commands and their command calls
- **Imports**: FROM image references with tags
- **Variables**: ENV and ARG declarations

## Graph Relationships

- Image dependencies (FROM statements)
- Build stage definitions and dependencies
- Command execution chains in RUN operations

## File Pattern

Recognizes files named `Dockerfile` or `dockerfile`, plus `.dockerfile` extension.

## Configuration

No special options. All Dockerfiles are indexed when enabled.
