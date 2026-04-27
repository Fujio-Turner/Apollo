# Making an Apollo Language Plugin

Apollo parses source code into a graph. Each programming language (or
file format) is handled by a **plugin**: a small, self-contained module
that knows how to read one kind of file and turn it into a structured
result the rest of Apollo can index, search, and visualize.

This guide shows you how to write a new plugin — for example a
`go.py` for Go, a `php.py` for PHP, a `java.py` for Java, etc.

### Naming convention: include the version or flavor

Languages evolve in incompatible ways and text formats come in flavors,
so plugin filenames should always carry a version or flavor suffix.
That way `python2.py` can live next to `python3.py`, and a future
`python4.py` can be dropped in without renaming anything.

Examples of good plugin folder names:

| Folder                | What it parses                                |
| --------------------- | --------------------------------------------- |
| `python3/`            | Python 3 source files (built-in)              |
| `python2/`            | Python 2 source files                         |
| `markdown_gfm/`       | GitHub Flavored Markdown (built-in)           |
| `markdown_common/`    | Strict CommonMark                             |
| `java17/`             | Java 17                                       |
| `node20/`             | Node.js 20 / modern ECMAScript                |
| `go1/`                | Go 1.x                                        |
| `php8/`               | PHP 8                                         |
| `csharp12/`           | C# 12                                         |
| `swift5/`             | Swift 5                                       |
| `kotlin2/`            | Kotlin 2                                      |
| `html5/`              | HTML5                                         |
| `pdf_pypdf/`          | PDFs via the `pypdf` library                  |

Each plugin should still target **one** version or flavor; if you need
to support multiple, ship multiple plugin folders.

---

## TL;DR

1. Create a new **folder** under `plugins/` named after the language
   and its version/flavor, e.g. `plugins/go1/`, `plugins/java17/`,
   `plugins/markdown_common/`.
2. Inside it, create `parser.py` containing a class that subclasses
   `apollo.parser.base.BaseParser` and implements `can_parse()` and
   `parse_file()`.
3. Inside it, create `__init__.py` that re-exports the class as
   `PLUGIN`:
   ```python
   from .parser import GoParser
   PLUGIN = GoParser
   ```
4. Done. `plugins.discover_plugins()` will pick it up automatically.

No registry to edit. No imports to add elsewhere. Drop the folder in,
restart Apollo, and the new language is supported.

---

## 1. Where plugins live

```
plugins/
├── __init__.py            # discover_plugins() — do not edit
├── markdown_gfm/          # built-in: GitHub Flavored Markdown
│   ├── __init__.py        #   exports PLUGIN
│   └── parser.py          #   the BaseParser implementation
├── python3/               # built-in: Python 3 (AST)
│   ├── __init__.py
│   └── parser.py
└── <your_language>/       # ← your new plugin goes here
    ├── __init__.py
    └── parser.py
```

Each plugin is a **self-contained subpackage**. Everything one plugin
needs — the parser class, helper modules, vendored support code,
per-plugin tests, sample data — lives in its own folder. Nothing one
plugin does can break another, and removing a plugin is one
``rm -rf plugins/<name>/`` away.

> **Single-file plugins also work.** ``plugins/foo.py`` with a
> module-level ``PLUGIN = ...`` is still discovered. Use this only for
> truly trivial plugins (no helpers, no third-party deps); prefer the
> subpackage layout above for anything you'd want to grow later.

Discovery is automatic via `pkgutil.iter_modules()` — you should never
need to touch `plugins/__init__.py`.

---

## 2. The plugin contract

Every plugin's **top-level module** (the subpackage's `__init__.py`,
or the single `.py` file in the legacy single-file layout) **must**
export:

| Name     | Type                | Description                                   |
| -------- | ------------------- | --------------------------------------------- |
| `PLUGIN` | `type[BaseParser]`  | The parser class to instantiate. Required.    |

In the recommended subpackage layout, that means your `__init__.py` is
just two lines:

```python
from .parser import MyParser
PLUGIN = MyParser
```

Everything else — the actual parsing code, helpers, third-party-lib
imports — lives in `parser.py` (or any module beside it).

Every parser class **must** subclass
`apollo.parser.base.BaseParser` and implement:

```python
class BaseParser(abc.ABC):
    @abc.abstractmethod
    def can_parse(self, filepath: str) -> bool: ...

    @abc.abstractmethod
    def parse_file(self, filepath: str) -> dict | None: ...

    # Optional override — defaults to re-reading from disk.
    def parse_source(self, source: str, filepath: str) -> dict | None: ...
```

### `can_parse(filepath)`

Return `True` if this plugin should handle the file. Almost always this
is just an extension check:

```python
from pathlib import Path

def can_parse(self, filepath: str) -> bool:
    return Path(filepath).suffix.lower() in {".go"}
```

### `parse_file(filepath)`

Read the file, parse it, and return a dict with the standard shape
described below. Return `None` if the file cannot be parsed (syntax
error, too large, wrong encoding, etc.) — the caller will then fall back
to the generic text indexer.

### `parse_source(source, filepath)` (optional but recommended)

When Apollo already has the file contents in memory it will call this
method instead, so you can avoid a redundant disk read. If you skip it,
the base class will simply call `parse_file()` again.

---

## 3. The standard result shape

`parse_file` / `parse_source` must return a `dict` with **at least**
these keys (use empty lists when a key is not applicable):

```python
{
    "file":      str,         # absolute path to the source file
    "functions": list[dict],  # top-level + nested functions
    "classes":   list[dict],  # classes / structs / interfaces
    "imports":   list[dict],  # import / require / include statements
    "variables": list[dict],  # module-level variables / constants
}
```

You may add language-specific keys alongside these (e.g. `traits`,
`macros`, `goroutines`, `decorators`). Apollo will store and surface
them in the graph viewer without further changes.

### Recommended dict shapes

These are the conventions used by the built-in plugins. Follow them
unless your language genuinely needs something different.

**Function / method:**
```python
{
    "name": "handleRequest",
    "line_start": 42,
    "line_end": 87,
    "loc": 46,
    "source": "func handleRequest(...) {...}",
    "docstring": "Handle an incoming HTTP request.",
    "args": ["w", "r"],            # bare parameter names
    "params": [                    # rich parameter info
        {"name": "w", "annotation": "http.ResponseWriter",
         "default": None, "kind": "arg"},
    ],
    "return_annotation": "error",
    "decorators": [],
    "calls": [                     # callsites inside this function
        {"name": "log.Printf", "args": ["\"hi\""], "line": 50},
    ],
    "complexity": 4,               # cyclomatic complexity (optional)
    "is_async": False,
    "is_nested": False,
    "is_test": False,
}
```

**Class / struct:**
```python
{
    "name": "Server",
    "line_start": 10,
    "line_end": 95,
    "source": "type Server struct {...}",
    "docstring": "HTTP server wrapper.",
    "bases": ["BaseHandler"],      # parents / embedded types
    "methods": [ ...function dicts... ],
    "decorators": [],
    "class_vars": [
        {"name": "addr", "annotation": "string", "value": "\":8080\"",
         "line": 11},
    ],
}
```

**Import:**
```python
{
    "module": "net/http",
    "names":  [],                  # for `from x import a, b`
    "alias":  None,
    "line":   3,
    "level":  0,                   # relative-import depth
}
```

**Variable:**
```python
{
    "name": "VERSION",
    "value": "\"1.0.0\"",
    "annotation": "string",
    "line": 1,
}
```

If your language has nothing meaningful to put under one of the
required keys, use an empty list — **do not omit the key**.

---

## 4. A minimal example: `plugins/go1/`

Create the folder layout:

```
plugins/go1/
├── __init__.py
└── parser.py
```

**`plugins/go1/parser.py`** — the actual parser:

```python
"""Tiny example Go plugin — extension match only, no real parsing yet."""
from __future__ import annotations

from pathlib import Path

from apollo.parser.base import BaseParser

_GO_EXTENSIONS = frozenset({".go"})


class GoParser(BaseParser):
    def can_parse(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in _GO_EXTENSIONS

    def parse_file(self, filepath: str) -> dict | None:
        try:
            source = Path(filepath).read_text(encoding="utf-8",
                                              errors="replace")
        except (OSError, IOError):
            return None
        return self.parse_source(source, filepath)

    def parse_source(self, source: str, filepath: str) -> dict | None:
        if not source.strip():
            return None
        # TODO: real parsing — for now we just register the file.
        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": [],
            "variables": [],
        }
```

**`plugins/go1/__init__.py`** — the discovery entry point:

```python
"""Go 1.x plugin package."""
from .parser import GoParser

PLUGIN = GoParser

__all__ = ["GoParser", "PLUGIN"]
```

That's the entire plugin. Drop the `go1/` folder under `plugins/` and
Apollo will start treating `*.go` files as Go.

### Need a third-party library? (e.g. `go-parser`, `pypdf`, `tree-sitter-go`)

Because each plugin is its own folder, you can import any third-party
package directly in `parser.py` (or in a sibling helper module) without
worrying about polluting other plugins. Two patterns:

1. **PyPI dep + lazy import** (recommended for optional features):

   ```python
   # plugins/pdf_pypdf/parser.py
   class PdfParser(BaseParser):
       def can_parse(self, filepath):
           if Path(filepath).suffix.lower() != ".pdf":
               return False
           try:
               import pypdf  # noqa: F401
           except ImportError:
               return False  # plugin self-disables if dep missing
           return True

       def parse_file(self, filepath):
           import pypdf  # lazy — Apollo stays importable without pypdf
           ...
   ```

   Drop a `plugins/pdf_pypdf/requirements.txt` next to `parser.py` so
   users can `pip install -r plugins/pdf_pypdf/requirements.txt` to
   enable the plugin.

2. **Vendored / split into helpers**: put helpers in
   `plugins/<name>/extractors.py`, `plugins/<name>/_vendor/...`, etc.
   and import them with relative imports (`from .extractors import X`).

---

## 5. Patterns you can copy

The two built-in plugins are deliberately written as references:

- **`plugins/python3/`** — uses the standard library `ast` module.
  Good template for any language that has a Python-callable parser
  (LibCST, `ast`, `tokenize`, `tree_sitter_languages`, `javalang`,
  `phply`, `esprima`, etc.). Shows how to extract functions, classes,
  methods, decorators, complexity, calls, exceptions, comments,
  context managers, and framework patterns.

- **`plugins/markdown_gfm/`** — uses `mistune` to build an AST and walks
  it for sections, code blocks, links, tables, task items, and
  frontmatter. Good template for any structured-text or config format
  (HTML, AsciiDoc, reStructuredText, YAML schemas, etc.).

Read whichever one is closer to what you're building, then adapt it.

---

## 6. Testing your plugin

Add a quick smoke test under `tests/`:

```python
# tests/test_go1_parser.py
from apollo.plugins import discover_plugins
from plugins.go1 import GoParser  # re-exported by plugins/go1/__init__.py


def test_go_plugin_is_discovered():
    plugins = discover_plugins()
    assert any(isinstance(p, GoParser) for p in plugins)


def test_go_plugin_recognises_extension(tmp_path):
    f = tmp_path / "main.go"
    f.write_text("package main\n")
    parser = GoParser()
    assert parser.can_parse(str(f))
    result = parser.parse_file(str(f))
    assert result is not None
    assert result["file"] == str(f)
```

Run the suite:

```bash
pytest tests/test_go1_parser.py -q
```

---

## 7. Plugging the new parser into the indexer

`discover_plugins()` returns parsers in alphabetical order. The
`GraphBuilder` walks them in order and uses the first one whose
`can_parse()` returns `True`. If you want a specific ordering (for
example, a tree-sitter parser ahead of a regex fallback), pass an
explicit list to `GraphBuilder(parsers=[...])` from your call site
(see `main.py::_build_parsers`). Otherwise, no wiring is needed.

---

## 8. Checklist

Before you commit a new plugin:

- [ ] Plugin lives in its own folder
      `plugins/<language><version_or_flavor>/` (e.g. `plugins/go1/`,
      `plugins/markdown_common/`).
- [ ] Folder contains `parser.py` with the `BaseParser` subclass.
- [ ] Folder contains `__init__.py` that does
      `from .parser import YourParser` and `PLUGIN = YourParser`.
- [ ] Parser class subclasses `apollo.parser.base.BaseParser`.
- [ ] `can_parse()` returns `True` only for files this plugin handles.
- [ ] `parse_file()` returns the standard dict (or `None`).
- [ ] All five required keys (`file`, `functions`, `classes`,
      `imports`, `variables`) are always present.
- [ ] Any third-party deps are in
      `plugins/<name>/requirements.txt` and imported lazily inside the
      parser class.
- [ ] At least one smoke test under `tests/`.
- [ ] `pytest -q` is green.
