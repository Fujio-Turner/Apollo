---
name: sql1
description: SQL plugin for Apollo. Parses .sql files to extract CREATE TABLE/VIEW/FUNCTION/PROCEDURE definitions, column declarations, and table/view references from queries.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/sql1
author: Fujio Turner
---

# sql1 Plugin

Parses SQL files (`.sql`) to extract structural information.

## Extracted Elements

- **Functions**: `CREATE FUNCTION/PROCEDURE` definitions
- **Tables/Views**: `CREATE TABLE` and `CREATE VIEW` definitions
- **Variables**: `DECLARE` variable declarations
- **References**: Table/view references extracted from queries (FROM, JOIN, INTO clauses)

## Graph Relationships

- Function definitions
- Table and view definitions
- Table/view references from queries
- Data dependencies (which tables feed which views/queries)

## File Pattern

Recognizes files with `.sql` extension.

## Configuration

No special options. All SQL files are indexed when enabled.
