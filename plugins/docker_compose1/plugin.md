---
name: docker_compose1
description: Docker Compose plugin for Apollo. Parses docker-compose.yml/compose.yml files to extract services, image references, depends_on edges, volumes, and environment variables.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/docker_compose1
author: Fujio Turner
---

# docker_compose1 Plugin

Parses docker-compose files to extract service and dependency information.

## Extracted Elements

- **Services**: Service definitions with name, image, ports, volumes
- **Images**: Image references from `image:` directives
- **Volumes**: Volume definitions and mounts
- **Dependencies**: Service-to-service dependencies via `depends_on:`

## Graph Relationships

- Service definitions
- Image dependencies
- Service dependency chains
- Volume references between services

## File Pattern

Recognizes files named:
- `docker-compose.yml`
- `docker-compose.yaml`
- `compose.yml`
- `compose.yaml`

## Configuration

No special options. All docker-compose files are indexed when enabled.
