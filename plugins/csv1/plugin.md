---
name: csv1
description: CSV file plugin for Apollo. Parses .csv files to extract headers, row counts, and structured table data for data documentation and dataset analysis.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/csv1
author: Fujio Turner
---

# CSV 1 Plugin

Parser for comma-separated values files (`.csv`). Extracts:

- **Variables**: Column headers, row count metadata
- **Structure**: Table shape, data field names
- **Metadata**: File size, delimiter detection, encoding

Enables queries like:
- "Show all CSV files and their columns"
- "Find datasets with a `user_id` field"
- "What tables have more than 1000 rows?"
