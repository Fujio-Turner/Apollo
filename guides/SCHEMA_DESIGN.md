# Schema Design Guide

This project uses [JSON Schema (2020-12)](https://json-schema.org/) to formally define every JSON document structure in Graph Search. All schema files live in the **`schema/`** directory at the project root.

---

## Quick Reference

Open `schema/index.html` in a browser to browse all available schemas interactively.

| Schema File | Describes | Used By |
|---|---|---|
| `graph.schema.json` | Top-level persisted graph document (`{ nodes, edges }`) | `storage/json_store.py` — save/load |
| `node.schema.json` | A single graph node (file, function, class, etc.) | `graph/builder.py` — node creation |
| `edge.schema.json` | A single directed edge between two nodes | `graph/builder.py` — edge creation |
| `spatial.schema.json` | Spatial coordinates `(x, y, z, face)` | `spatial.py` — coordinate mapper |
| `parser-output.schema.json` | Parser return value for a single file | `parser/python_parser.py`, `parser/treesitter_parser.py` |
| `search-result.schema.json` | `/api/search` response payload | `web/server.py` |
| `node-detail.schema.json` | `/api/node/{id}` response payload | `web/server.py` |
| `chat-thread.schema.json` | A saved AI chat conversation thread | `chat/history.py` — create/load/persist |

---

## Why JSON Schema?

1. **Single source of truth** — the schema files document every field, its type, and whether it is required.
2. **Validation** — schemas can be used at test time to assert that serialized output matches the contract.
3. **Tooling** — editors, linters, and CI can validate JSON data against the schemas automatically.
4. **Communication** — anyone looking at the project can understand the data shapes without reading Python code.

---

## Schema Conventions

### File Naming

All schema files use the pattern `<name>.schema.json` and live in `schema/`.

### Draft Version

Every schema must include:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema"
}
```

### `$id`

Each schema has a canonical `$id` under `https://graph-search.local/schema/`. This is a logical identifier, not a real URL. It allows schemas to reference each other with `$ref`:

```json
{ "$ref": "spatial.schema.json" }
```

### Required vs Optional

- Use `"required"` to list fields that **must** be present on every instance.
- Fields that only appear on certain node types (e.g. `bases` on class nodes, `params` on function nodes) are **not** listed in `required` — they are optional properties.

### Nullable Fields

For fields that may be `null`, use a type array:

```json
{ "type": ["string", "null"] }
```

---

## Schema Overview

### `graph.schema.json` — Top-Level Document

The JSON file written by `JsonStore.save()` and read by `JsonStore.load()`. This is the root document.

```
{
  "nodes": [ ...node objects... ],
  "edges": [ ...edge objects... ]
}
```

### `node.schema.json` — Graph Node

Every node has `id`, `type`, and `name`. Additional fields depend on `type`:

| Node Type | Key Fields |
|---|---|
| `directory` | `path` |
| `file` | `path`, `file_md5`, `module_docstring`, `patterns` |
| `function` | `path`, `line_start`, `line_end`, `source`, `args`, `params`, `return_annotation`, `decorators`, `source_md5`, `docstring`, `is_async`, `is_nested`, `is_test`, `signature_hash`, `complexity`, `loc`, `context_managers`, `exceptions` |
| `class` | `path`, `line_start`, `line_end`, `source`, `bases`, `decorators`, `docstring`, `class_vars`, `is_dataclass`, `is_namedtuple` |
| `method` | `path`, `line_start`, `line_end`, `parent_class`, `args`, `params`, `return_annotation`, `decorators`, `docstring`, `signature_hash`, `complexity`, `loc`, `context_managers`, `exceptions` |
| `variable` | `path`, `line`, `value`, `annotation` |
| `import` | `path`, `module`, `names`, `alias`, `line`, `level`, `type_checking` |
| `document` | `path`, `doc_type`, `line_start`, `line_end`, `source`, `frontmatter`, `title` |
| `section` | `path`, `level`, `line_start`, `line_end`, `source`, `parent_section` |
| `code_block` | `path`, `language`, `line_start`, `line_end`, `source` |
| `link` | `path`, `url`, `line`, `link_type`, `is_image` |
| `table` | `path`, `headers`, `rows`, `line_start`, `line_end` |
| `task_item` | `path`, `text`, `checked`, `line` |
| `comment` | `path`, `line`, `tag`, `text` |
| `string` | `path`, `line`, `kind`, `value` |

Optional on any node: `embedding` (vector), `spatial` (coordinates).

### `edge.schema.json` — Graph Edge

Every edge has `source`, `target`, and `type`. Edge types: `contains`, `defines`, `calls`, `imports`, `inherits`, `references`, `tests`. The `calls` edge type may also carry `call_args` and `call_line`.

### `spatial.schema.json` — Spatial Coordinates

Four fields: `x` (0–360°), `y` (0–360°), `z` (0–1.0), `face` (1–6).

### `parser-output.schema.json` — Parser Output

The dict returned by `PythonParser.parse()` or `TreeSitterParser.parse()`:

```
{
  "file": "/absolute/path.py",
  "functions": [ ... ],
  "classes": [ ... ],
  "imports": [ ... ],
  "variables": [ ... ],
  "documents": [ ... ]    // optional, non-code files only
}
```

### `search-result.schema.json` — Search API Response

```
{
  "results": [
    { "id": "...", "name": "...", "type": "...", "path": "...", "line_start": 10, "score": 0.87 }
  ]
}
```

### `node-detail.schema.json` — Node Detail API Response

Same fields as a node, plus `edges_in` and `edges_out` arrays.

---

## Viewing Schemas

Open `schema/index.html` in any browser — it fetches each `*.schema.json` file and renders it as an expandable, syntax-highlighted card. No build step or server required.

---

## Adding a New Schema

1. Create `schema/<name>.schema.json` with `$schema` and `$id` set.
2. Add the filename to the `SCHEMA_FILES` array in `schema/index.html`.
3. Update this guide's Quick Reference table.
4. If the schema references another, use `{ "$ref": "<other>.schema.json" }`.

---

## Validating Data Against Schemas

Install a JSON Schema validator (already available in the project venv):

```bash
pip install jsonschema
```

Example validation in Python:

```python
import json
from jsonschema import validate

with open("schema/graph.schema.json") as f:
    schema = json.load(f)

with open(".graph_search/graph.json") as f:
    data = json.load(f)

validate(instance=data, schema=schema)
```

Or from the command line:

```bash
python -m jsonschema -i .graph_search/graph.json schema/graph.schema.json
```
