---
name: makefile1
description: Makefile plugin for Apollo. Parses Makefile/GNUmakefile/.mk files to extract targets, prerequisites, recipes, variables, and include directives.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/makefile1
author: Fujio Turner
---

# makefile1 Plugin

Parses Makefile to extract targets, dependencies, and recipes.

## Extracted Elements

- **Targets**: Make targets (`target: prerequisites`)
- **Dependencies**: Prerequisites (other targets that must build first)
- **Includes**: Include directives for other makefiles
- **Variables**: Variable assignments (VAR = value)
- **Recipes**: Commands associated with targets

## Graph Relationships

- Target definitions
- Target-to-target dependencies (prerequisites)
- Makefile includes

## File Pattern

Recognizes files named:
- `Makefile`
- `makefile`
- `GNUmakefile`
- Plus `.mk` extension

## Configuration

No special options. All makefiles are indexed when enabled.
