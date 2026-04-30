# Making an Apollo Language Plugin

Apollo parses source code into a **knowledge graph**. Each programming
language (or file format) is handled by a **plugin**: a small,
self-contained module that knows how to read one kind of file and turn
it into a structured result the rest of Apollo can index, search, and
visualize.

This guide shows you how to write a new plugin — for example a
`go.py` for Go, a `php.py` for PHP, a `java.py` for Java, etc.

---

## 0. Why plugins exist: the goal is **relationships**, not entities

Read [`docs/DESIGN.md`](../docs/DESIGN.md) first. The point of Apollo
is **not** to list the functions/classes/imports inside each file —
that's just the raw material. The product is the **knowledge graph**
of the relationships *between* those entities, surfaced visually and
made queryable by people and AI:

| Edge type    | Meaning                                  | Source field a plugin must emit                              |
|--------------|------------------------------------------|--------------------------------------------------------------|
| `defines`    | A file/class defines an entity           | `functions[]`, `classes[]`, `variables[]`, `methods[]`       |
| `calls`      | A function calls another function        | `functions[].calls[]` (call sites with names + line)         |
| `imports`    | A file imports a module / symbol         | `imports[]`                                                  |
| `references` | A function reads / writes a variable     | call/name analysis inside `functions[].calls[]` & body walks |
| `inherits`   | A class extends / implements another     | `classes[].bases[]`                                          |
| `contains`   | A directory contains a file              | (handled by builder, not the plugin)                         |
| `tests`      | A test function exercises a target       | `functions[].is_test = True` + matching name                 |

If your plugin only emits names, the graph has nodes floating in
space — no `calls` edges, no `inherits` edges, no callers/callees,
no "show me everything that touches `SMTP_HOST`" queries, no semantic
search of method bodies. **A plugin that doesn't surface
relationships is a stub, not a working plugin.** The reference
implementation is [`plugins/python3/`](../plugins/python3/parser.py) —
study it before writing any new plugin.

### 0.1 Two flavors of plugin, same goal

#### A. **Programming-language plugins** (Python, Go, Java, JS, PHP, Rust, C#, Kotlin, Swift, …)

Despite syntactic differences, every programming language shares the
same handful of universal building blocks. A plugin's job is to map
those blocks onto Apollo's entity & edge schema:

| Universal concept              | What you emit                                                                                |
|--------------------------------|----------------------------------------------------------------------------------------------|
| **Constant / module variable** | one entry in `variables[]` with `name`, `value`, `line`, `annotation`                       |
| **Local variable / parameter** | per-function `params[]` (name, default, annotation, kind) — *not* a top-level node          |
| **Function / method**          | one entry in `functions[]` *or* (for methods) inside `classes[].methods[]`                  |
| **Function body / loops / if-else** | extract `calls[]` (every callsite — name + args + line), `complexity` (loop+branch count), `loc` |
| **Function calls another function** | each callsite goes in that function's `calls[]` so the builder can draw a `calls` edge |
| **Class / struct / interface** | one entry in `classes[]` with `bases[]`, `methods[]`, `class_vars[]`                        |
| **Inheritance / `extends` / `implements`** | the parent type names go in `classes[].bases[]` so the builder can draw an `inherits` edge |
| **Import / require / use / include** | one entry in `imports[]` with `module`, `names[]`, `alias`, `line`, `level`           |
| **Decorators / annotations / attributes** | `decorators[]` on the function/method/class                                          |
| **Docstring / leading comment** | `docstring` field on the function/class/method (powers semantic search & AI chat)            |
| **TODO / FIXME / NOTE comments** | `comments[]` with `tag`, `text`, `line`                                                     |
| **Async / generator / nested / test** | `is_async`, `is_generator`, `is_nested`, `is_test` flags                                |
| **Magic strings (SQL, URL, regex)** | `strings[]` with `kind` (`sql` / `url` / `regex`) — these become connection points too |

Mechanically, the plugin's parser walks the AST (or CST, or — for
languages without a Python AST library — a token / tree-sitter / regex
parser) **once** and fills out the result dict. The richer the
extraction, the richer the graph.

> **Why "loops" and "if/else" matter even though they aren't nodes.**
> They feed the `complexity` score on each function and are walked to
> find the calls/references buried inside them. A plugin that only
> looks at the top of a function body and ignores the nested control
> flow will miss most of the `calls` edges.

#### B. **Document / asset / non-code plugins** (Markdown, HTML, PDF, AsciiDoc, RST, JSON Schema, OpenAPI, images, …)

Document formats *don't* have functions or classes, so the
relationship surface is harder to mine — but the goal is the same:
**find connections that can become edges in the graph**. A document
plugin that just dumps file text into a single node is barely better
than the generic `TextFileParser`. Strive for one or more of:

| Connection signal                         | What you emit                                                          | Resulting edge / node                          |
|-------------------------------------------|------------------------------------------------------------------------|------------------------------------------------|
| **Internal hyperlinks** (`[x](./other.md)`, `<a href="../guides/y.md">`, `href="/img/foo.png"`) | one entry in `links[]` with `target`, `kind: internal`, `line` | `link::…` node + future `references` edge to the target file when resolution succeeds |
| **External hyperlinks** (`https://…`)     | `links[]` with `kind: external` — still useful for "what does this site link out to?" queries | `link::…` node                                 |
| **Anchor / heading references** (`[x](#section-id)`) | `links[]` with `kind: anchor`, `target_anchor: "section-id"`     | `link::…` node + `references` edge to the local section |
| **Image / asset references** (`![alt](/img/x.png)`, `<img src="…">`) | `links[]` with `kind: image`, `target` is the asset path | links docs to the binary assets they embed |
| **Code blocks with a language tag**       | `code_blocks[]` with `language`, `content`, `line_start`               | enables cross-format search ("show me all `bash` snippets in our docs") |
| **Frontmatter / metadata** (`title`, `tags`, `author`, `date`) | `frontmatter` dict + `tags[]`                                | tag-based clustering, author/date filtering    |
| **Headings / sections** (h1–h6)           | `sections[]` with `level`, `name`, `line_start`, `line_end`            | hierarchical `section` nodes — enables "jump to section" + section-level embedding |
| **Tables** (when they encode structured data) | `tables[]` with `headers[]` and `rows[][]`                          | structured data search                         |
| **Task items** (`- [ ]` / `- [x]`)        | `task_items[]` with `text`, `checked`, `line`                          | progress tracking + linkable todo nodes        |
| **Embedded references to code symbols** (e.g. `\`MailService\`` mentions in a doc) | (advanced) `mentions[]` with the symbol name | the builder can resolve to a `references` edge from doc → code |

**The honest caveat the user raised:** internal links are great when
they hit *another file Apollo has indexed*, but they often dead-end
on truly internal references the file doesn't expose (e.g. a PDF
table-of-contents entry that points to an internal byte offset).
That's fine — emit them anyway as `link` nodes. Even unresolved
links are useful: they show up in the graph, they're searchable, and
when the linked file *is* later added to the index the existing edge
resolves automatically. **Do not skip emitting a link just because
its target isn't currently in the index.**

> The two reference document plugins are
> [`plugins/markdown_gfm/`](../plugins/markdown_gfm/parser.py) and
> [`plugins/html5/`](../plugins/html5/parser.py). Both follow this
> pattern: walk the AST → emit sections, links, code blocks, tables,
> task items, frontmatter.

### 0.2 The minimum bar a plugin must clear

Whether your plugin is for a programming language or a document
format, the result dict you return **must** be consumable by
[`graph/builder.py`](../graph/builder.py) without raising. That means
every entry you emit has to carry the keys the builder reads. The
builder treats these as **required**, not optional:

| Entity            | Required keys                                                                                            |
|-------------------|----------------------------------------------------------------------------------------------------------|
| `functions[]`     | `name`, `line_start`, `line_end`, `source` (everything else has a sensible default in the builder)       |
| `classes[]`       | `name`, `line_start`, `line_end`, `source`, `bases` (list — empty is OK), `methods` (list — empty is OK) |
| `classes[].methods[]` | same required keys as `functions[]`                                                                   |
| `imports[]`       | `module` (the rest read defensively via `.get()`)                                                        |
| `variables[]`     | `name`, `line` (the builder dereferences `var["line"]` directly)                                         |

If you can't compute a real `line_start` / `line_end` (e.g. for a
non-line-based format), set them to `0` — but never omit them.
Emitting `{"name": "main"}` with no line info is **not** a valid
function entry; it will crash the indexer.

> **Self-test before you ship.** Run your plugin's `test_parser.py`
> *and* index a small fixture project that contains real files of
> your language with `python main.py index <fixture-dir>`. If the
> indexer raises `KeyError` on any of the keys above, the plugin
> isn't done.

---

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
4. Inside it, create `plugin.md` — the **plugin manifest** with a
   YAML front-matter block declaring `description`, `version`, `url`,
   and `author` (see [§ 2.5](#25-the-plugin-manifest-pluginmd)).
5. Inside it, create `config.json` — the **plugin runtime config**
   declaring at minimum `"enabled": true` plus any plugin-specific
   knobs you want users to tweak from **Settings → Plugins**
   (see [§ 2.6](#26-the-plugin-config-configjson)).
6. Inside it, create `test_parser.py` — the **per-plugin smoke test**
   covering discovery, extension matching, and one happy-path parse
   (see [§ 6](#6-testing-your-plugin)).
7. Inside `parser.py`, follow the project-wide logging standard in
   [`guides/LOGGING.md`](LOGGING.md) — at minimum,
   `logger = logging.getLogger(__name__)` at module top, lazy
   `%`-formatted log calls, no `print()`, and `logger.warning(...)`
   (with `exc_info=True` when useful) inside any `except` that
   swallows an error. See [§ 5.2](#52-logging-use-the-project-standard).
8. Done. `plugins.discover_plugins()` will pick it up automatically
   and Apollo's **Settings → Plugins** tab will show the manifest
   metadata, the editable config, and a SHA-256 hash of `parser.py`.

No registry to edit. No imports to add elsewhere. Drop the folder in,
restart Apollo, and the new language is supported.

---

## 1. Where plugins live

```
plugins/
├── __init__.py            # discover_plugins() — do not edit
├── markdown_gfm/          # built-in: GitHub Flavored Markdown
│   ├── __init__.py        #   exports PLUGIN
│   ├── parser.py          #   the BaseParser implementation
│   ├── plugin.md          #   manifest (description / version / url / author)
│   ├── config.json        #   runtime config (enabled + knobs)
│   └── test_parser.py     #   per-plugin smoke test (pytest)
├── python3/               # built-in: Python 3 (AST)
│   ├── __init__.py
│   ├── parser.py
│   ├── plugin.md
│   ├── config.json
│   └── test_parser.py
└── <your_language>/       # ← your new plugin goes here
    ├── __init__.py
    ├── parser.py
    ├── plugin.md
    ├── config.json
    └── test_parser.py
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

### 2.5. The plugin manifest (`plugin.md`)

Every subpackage plugin **must** ship a `plugin.md` file alongside
`parser.py`. It is a regular Markdown document with a YAML
**front-matter** block at the top that Apollo parses (via the existing
`python-frontmatter` dependency) to populate the
**Settings → Plugins** tab.

Single-file plugins (`plugins/foo.py`) put their manifest next to the
file as `plugins/foo.plugin.md`.

#### Required format

```markdown
---
name: go1
description: Go 1.x source-file plugin for Apollo. Parses .go files and
  extracts packages, functions, structs, interfaces, and imports.
version: 1.0.0
url: https://github.com/your-org/apollo-go-plugin
author: Jane Doe / Acme Corp
---

# Go 1.x Plugin

Optional human-readable documentation goes in the body. Anything below
the closing `---` is ignored by the manifest parser; it's purely for
humans browsing the source tree.
```

#### Field reference

| Key           | Required | Description                                                                                                  |
| ------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| `name`        | optional | Display name. Defaults to the folder name when omitted.                                                      |
| `description` | **yes**  | One-paragraph summary shown in the Plugins tab. Plain text or short inline Markdown.                          |
| `version`     | **yes**  | Semver-style version string (e.g. `1.0.0`, `2.3.1-beta`). Shown as a badge.                                  |
| `url`         | **yes**  | Project / source / docs URL. Rendered as a link in the Plugins tab.                                          |
| `author`      | **yes**  | Person, team, or company that maintains the plugin.                                                          |

Anything else in the front-matter block is ignored, so feel free to
keep your own keys (e.g. `license`, `tags`, `homepage`) — they just
won't appear in the UI.

#### Hash of `parser.py` (auto-computed)

Apollo computes a **SHA-256 hash of `parser.py`** every time it loads
the settings file and exposes it on each plugin entry as `sha256`. The
**Plugins** tab shows it on hover of the version badge so users can
verify that the on-disk parser matches what they expect — useful for
audits and supply-chain checks.

> **Why SHA-256, not SHA-1 or MD5?** Both SHA-1 and MD5 have known
> practical collision attacks (SHAttered, 2017; MD5 collisions known
> since 2004) and are no longer recommended for integrity
> verification. SHA-256 is the modern standard used by `pip`, `npm`,
> Homebrew, Linux distros, code signing, and Git's newer object
> hashing. You do **not** put the hash in `plugin.md` yourself — it
> is always derived from the live file.

#### What happens if `plugin.md` is missing or malformed

The plugin still loads (so existing installations don't break), but
the Plugins tab will render its description / version / url / author
as empty / "no description". New plugins should always ship a valid
manifest.

---

### 2.6. The plugin config (`config.json`)

Alongside `plugin.md`, every subpackage plugin **must** ship a
`config.json` in the same folder. This is the plugin's **runtime
configuration**: a JSON object holding the knobs Apollo users can flip
from **Settings → Plugins** without editing source.

Single-file plugins (`plugins/foo.py`) put their config next to the
file as `plugins/foo.config.json`.

#### Required format

The only required key is `enabled`, a boolean that controls whether
the plugin participates in indexing. A newly installed plugin **must**
default to `"enabled": true` so it works out of the box.

```json
{
  "enabled": true
}
```

Beyond `enabled`, you are free to add any plugin-specific options that
make sense for your parser. Suggested conventions for built-in knobs:

| Key                     | Type             | Purpose                                              |
| ----------------------- | ---------------- | ---------------------------------------------------- |
| `enabled`               | `bool`           | **Required.** Skip parsing entirely when `false`.    |
| `extensions`            | `list[str]`      | File extensions this plugin claims (lower-case).     |
| `max_file_size_bytes`   | `int`            | Skip files larger than this. `0` / absent = no cap.  |
| `extract_<thing>`       | `bool`           | Toggle for an optional extraction pass.              |
| `comment_tags`          | `list[str]`      | Tags surfaced from `# TODO`, `<!-- FIXME -->`, etc.  |
| `ignore_dirs`           | `list[str]`      | **Per-language directory ignores.** Folder *names* the indexer should skip when this plugin is enabled (e.g. `venv`, `site-packages`, `node_modules`). |
| `ignore_files`          | `list[str]`      | Glob patterns for files to skip (e.g. `"*.pyc"`).    |
| `ignore_dir_markers`    | `list[str]`      | Marker filenames inside a directory that mark the whole directory as ignorable (e.g. `pyvenv.cfg` flags arbitrary virtualenv folders even with non-standard names). |

#### Describe each knob with a `_<key>` sibling

Plugins are sorta stand-alone — once installed, the only thing the user
sees in **Settings → Plugins** is your `config.json`. To make the UI
self-documenting, **every runtime key should ship a sibling key
prefixed with `_` whose value is a human-readable description**. The
Settings UI renders that description as the form field's label /
tooltip; the loader strips all `_<key>` siblings out of the merged
runtime dict so your parser never sees them as data.

```json
{
  "enabled": true,
  "_enabled": "Master switch — when false, this plugin is skipped during indexing.",
  "max_file_size_bytes": 1048576,
  "_max_file_size_bytes": "Skip files larger than this many bytes (default 1 MB)."
}
```

Rules:

- The sibling key is `_` + the runtime key (`extract_links` →
  `_extract_links`).
- Description values are strings.
- Description siblings are **read-only** — `PATCH /api/settings/
  plugins/<name>/config` rejects any body whose keys start with `_`.
- If a description is missing, the UI falls back to showing the bare
  key. This is fine for back-compat, but every shipped plugin in the
  repo should provide one for every knob.

#### Per-language directory ignores (very important)

Different programming languages put third-party / generated code in
different places. Indexing those folders can multiply node and edge
counts by 100× or more without adding any signal — they are not the
user's source code.

Each plugin must declare **its own** ignore list. Apollo merges the
union of every *enabled* plugin's `ignore_dirs` / `ignore_files` /
`ignore_dir_markers` into the indexer's effective skip set. Disabling
a plugin from **Settings → Plugins** also removes its ignores, so a
project that doesn't use a given language doesn't pay for its noise
filters.

Recommended ignore lists by language:

| Language     | `ignore_dirs` (typical)                                                                                    | `ignore_dir_markers`     |
| ------------ | ---------------------------------------------------------------------------------------------------------- | ------------------------ |
| Python 3     | `venv`, `.venv`, `env`, `.env`, `virtualenv`, `site-packages`, `dist-packages`, `.eggs`, `.tox`, `.nox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `__pypackages__`, `__pycache__` | `pyvenv.cfg`, `conda-meta` |
| Node / TS    | `node_modules`, `bower_components`, `.next`, `.nuxt`, `.svelte-kit`                                        | —                        |
| Go           | `vendor`                                                                                                   | `go.mod`* (only as include marker) |
| Rust         | `target`                                                                                                   | `Cargo.lock`* (only as include marker) |
| Java / Kotlin | `target`, `build`, `out`, `.gradle`                                                                       | —                        |
| HTML / docs  | `_site`, `public`, `.jekyll-cache`, `_book`, `.docusaurus`                                                 | —                        |

> **Apollo internals stay in the core builder.** Folders such as
> `.git`, `_apollo`, `.apollo`, and `_apollo_web` are skipped
> unconditionally by the graph builder regardless of which plugins
> are enabled. Plugins should not list these.

##### Example: the Python 3 plugin's ignore declaration

```json
{
  "enabled": true,
  "extensions": [".py"],
  "ignore_dirs": [
    "venv", ".venv", "env", ".env", "virtualenv",
    "site-packages", "dist-packages",
    ".eggs", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "__pypackages__", "__pycache__"
  ],
  "ignore_files": ["*.pyc", "*.pyo", "*.pyd", "*.egg-info"],
  "ignore_dir_markers": ["pyvenv.cfg", "conda-meta"]
}
```

When the user opens a Python project, the indexer sees the python3
plugin is enabled, pulls its `ignore_dirs` into the skip set, and
walks the tree without descending into any `venv/` or
`site-packages/` it encounters. Toggling python3 *off* in **Settings →
Plugins** removes those ignores too — useful when you do *want* to
audit a vendored copy of `site-packages`.

#### Examples from the built-in plugins

`plugins/python3/config.json`:

```json
{
  "enabled": true,
  "extensions": [".py"],
  "extract_comments": true,
  "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
  "extract_strings": true,
  "extract_type_checking_imports": true,
  "detect_patterns": true
}
```

`plugins/markdown_gfm/config.json`:

```json
{
  "enabled": true,
  "extensions": [".md", ".markdown"],
  "max_file_size_bytes": 1048576,
  "extract_callouts": true,
  "extract_tables": true,
  "extract_task_items": true,
  "extract_wikilinks": true
}
```

`plugins/pdf_pypdf/config.json`:

```json
{
  "enabled": true,
  "extensions": [".pdf"],
  "max_file_size_bytes": 52428800,
  "extract_outline": true,
  "extract_metadata": true,
  "decrypt_with_empty_password": true
}
```

#### Reading the config from your parser

Apollo loads each plugin's `config.json` at startup and merges any
overrides from the global `data/settings.json`. The resulting dict is
passed to the parser instance — so your `__init__` should accept (and
default) a `config` argument:

```python
class GoParser(BaseParser):
    DEFAULT_CONFIG = {
        "enabled": True,
        "extensions": [".go"],
        "max_file_size_bytes": 5_000_000,
    }

    def __init__(self, config: dict | None = None):
        merged = {**self.DEFAULT_CONFIG, **(config or {})}
        self.config = merged

    def can_parse(self, filepath):
        if not self.config.get("enabled", True):
            return False
        ext = Path(filepath).suffix.lower()
        return ext in self.config.get("extensions", [])
```

`enabled: false` should make `can_parse()` return `False` so the
graph builder simply skips the plugin without re-indexing anything.

#### What happens if `config.json` is missing or malformed

The plugin still loads with `enabled: true` and an empty options dict
(so existing installations don't break), but the Plugins tab won't
expose any knobs to users. New plugins should always ship a valid
`config.json`.

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

> **The shape below is not "nice to have".** See § 0.2 — the builder
> dereferences `func["line_start"]`, `cls["bases"]`, `cls["methods"]`,
> `var["line"]` etc. *directly*. Emitting `{"name": "x"}` will crash
> the indexer. The "required" rows below are required.

### Recommended dict shapes

These are the conventions used by the built-in plugins. Rows marked
**required** must be present (the builder reads them with `[...]`,
not `.get(...)`). Other rows are recommended — the more you fill in,
the richer the graph and the better callers/callees, semantic search,
and AI-chat answers behave.

**Function / method:**
```python
{
    # ─── required (builder reads directly) ───────────────────────────
    "name":       "handleRequest",
    "line_start": 42,
    "line_end":   87,
    "source":     "func handleRequest(...) {...}",
    # ─── recommended (drives graph edges & semantic features) ────────
    "loc":         46,
    "docstring":   "Handle an incoming HTTP request.",
    "args":        ["w", "r"],            # bare parameter names
    "params": [                           # rich parameter info
        {"name": "w", "annotation": "http.ResponseWriter",
         "default": None, "kind": "arg"},
    ],
    "return_annotation": "error",
    "decorators":  [],
    "calls": [                            # ★ drives `calls` edges
        {"name": "log.Printf", "args": ["\"hi\""], "line": 50},
    ],
    "complexity":  4,                     # cyclomatic complexity
    "is_async":    False,
    "is_nested":   False,
    "is_test":     False,                 # ★ enables `tests` edges
    "context_managers": [],
    "exceptions":       [],
}
```

**Class / struct:**
```python
{
    # ─── required ────────────────────────────────────────────────────
    "name":       "Server",
    "line_start": 10,
    "line_end":   95,
    "source":     "type Server struct {...}",
    "bases":      ["BaseHandler"],        # ★ drives `inherits` edges (empty list OK)
    "methods":    [ ...function dicts... ],  # ★ each is a `defines` edge from class
    # ─── recommended ─────────────────────────────────────────────────
    "docstring":   "HTTP server wrapper.",
    "decorators":  [],
    "class_vars": [
        {"name": "addr", "annotation": "string", "value": "\":8080\"",
         "line": 11},
    ],
    "is_dataclass":  False,
    "is_namedtuple": False,
}
```

**Import:**
```python
{
    "module": "net/http",                 # required
    # ─── recommended ─────────────────────────────────────────────────
    "names":  [],                         # for `from x import a, b`
    "alias":  None,
    "line":   3,
    "level":  0,                          # relative-import depth
}
```

**Variable:**
```python
{
    "name": "VERSION",                    # required
    "line": 1,                            # required
    "value":      "\"1.0.0\"",            # recommended
    "annotation": "string",
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
├── parser.py
└── plugin.md
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

**`plugins/go1/plugin.md`** — the manifest shown in
**Settings → Plugins**:

```markdown
---
name: go1
description: Go 1.x source-file plugin for Apollo. Parses .go files
  and extracts packages, functions, structs, interfaces, and imports.
version: 0.1.0
url: https://github.com/your-org/apollo-go-plugin
author: Your Name
---

# Go 1.x Plugin

Free-form description goes here.
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

- **`plugins/go1/`, `plugins/java17/`, `plugins/javascript1/`,
  `plugins/node20/`, `plugins/php8/`** — all use the **regex pattern**
  described below in § 5.1. Good templates for any language with no
  installable Python-callable AST.

Read whichever one is closer to what you're building, then adapt it.

### 5.1 No Python AST? Use the regex pattern

If there is no installable Python parser for your language and you don't
want to ship a tree-sitter binary, you can still write a working plugin
with carefully scoped regexes — the bundled Go/Java/JS/Node/PHP plugins
do exactly this. The pattern that turns regex matches into the **same
result-dict shape** the AST plugins produce has three small pieces:

#### 1. Compute line numbers from byte offsets

Regex matches give you a character offset; the builder needs 1-based
line numbers. One helper handles every callsite:

```python
def _line_at(source: str, pos: int) -> int:
    """Return the 1-based line number of byte offset *pos* in *source*."""
    return source.count("\n", 0, pos) + 1
```

Call it with `m.start()` to get `line_start` and with the closing-brace
offset (see step 2) to get `line_end`. For per-call lines inside a
function body, do the same with the body-relative offset plus the
body's starting line.

#### 2. Find the matching `}` for `line_end` and `source`

`line_end` and `source` (the full text of the function/class) require
walking from the opening `{` to its matching `}`. A small scanner that
respects strings and `// ... */` / `/* ... */` comments is enough:

```python
def _find_matching_brace(source: str, open_pos: int) -> int:
    """Index of the ``}`` matching the ``{`` at *open_pos*; tolerant
    of strings and comments so braces inside literals don't fool it."""
    depth = 0
    i = open_pos
    n = len(source)
    in_str = None         # quote char we're inside, or None
    in_line_comment = False
    in_block_comment = False
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1; continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2; continue
            i += 1; continue
        if in_str:
            if ch == "\\":
                i += 2; continue
            if ch == in_str:
                in_str = None
            i += 1; continue
        if ch == "/" and nxt == "/":
            in_line_comment = True; i += 2; continue
        if ch == "/" and nxt == "*":
            in_block_comment = True; i += 2; continue
        if ch in ('"', "'", "`"):
            in_str = ch; i += 1; continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1   # truncated source: never crash, return *some* range
```

With those two helpers, every function/class match becomes:

```python
header_start = m.start()
open_brace   = m.end() - 1            # the '{' the regex captured
close_brace  = _find_matching_brace(source, open_brace)
line_start   = _line_at(source, header_start)
line_end     = _line_at(source, close_brace)
src_slice    = source[header_start : close_brace + 1]
body         = source[open_brace + 1 : close_brace]
```

#### 3. Methods go *inside* `classes[].methods[]`, not in `functions[]`

The graph builder draws a `defines` edge from class → method by walking
`classes[i]["methods"]`. Methods that leak into the top-level
`functions[]` list become orphan nodes with no parent class. The fix
is to scan a class body for method-shaped matches and attach them:

```python
def _extract_class(self, source, m):
    open_brace  = m.end() - 1
    close_brace = _find_matching_brace(source, open_brace)
    body_start  = open_brace + 1

    # Methods only — finditer is bounded to the class body.
    methods = []
    for mm in METHOD_RE.finditer(source, body_start, close_brace):
        m_open  = mm.end() - 1
        m_close = _find_matching_brace(source, m_open)
        if m_close > close_brace:
            continue                       # spilled out of the class
        methods.append({
            "name": mm.group("name"),
            "line_start": _line_at(source, mm.start()),
            "line_end":   _line_at(source, m_close),
            "source":     source[mm.start() : m_close + 1],
            "calls":      _extract_calls(source[m_open+1 : m_close],
                                         _line_at(source, m_open + 1)),
        })

    return {
        "name": m.group("name"),
        "line_start": _line_at(source, m.start()),
        "line_end":   _line_at(source, close_brace),
        "source":     source[m.start() : close_brace + 1],
        "bases":      _split_extends(m.group("extends")),
        "methods":    methods,
    }
```

The same walk lets you collect `bases` from the `extends` /
`implements` clause and produce `inherits` edges in the graph — without
those, your plugin emits class nodes with no inheritance relationships,
which violates the goal in § 0.

#### 4. Extract `calls[]` so the graph gets `calls` edges

For every function/method body, scan for `ident(` callsites and emit
them — the cross-file resolver in `graph/builder.py` does the rest:

```python
_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w\.]*)\s*\(")
_LANG_KEYWORDS = frozenset({"if", "for", "while", "switch", "return", ...})

def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out, seen = [], set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        if name.split(".")[0] in _LANG_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out
```

A regex-based plugin will miss some constructs a real AST would catch —
that's fine. **Emit what you can** so the graph has *some* `calls`
edges. A future tree-sitter or real-AST plugin can ship later as a
separate folder (e.g. `go_treesitter/`) and supersede this one.

#### Heads-up: `ignore_dir_markers` means *skip*, not *include*

The `ignore_dir_markers` config field lists sentinel **filenames** that
mark a directory as **ignorable**. It is what catches non-standard
virtualenvs (`pyvenv.cfg`) or conda envs (`conda-meta`).

Do **not** put a project-root marker like `package.json`, `composer.json`,
`go.mod`, or `pom.xml` in `ignore_dir_markers` — those files mark
directories the plugin should *index*, not skip. Putting them here
silently disables the plugin for every real project.

### 5.2 Logging: use the project standard

The single source of truth for diagnostics in Apollo is
[`guides/LOGGING.md`](LOGGING.md). **Plugins are not exempt** — every
parser module you ship under `plugins/<name>/` MUST follow it.

The minimum a plugin must do:

```python
# plugins/<name>/parser.py
from __future__ import annotations

import logging

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)   # → "plugins.<name>.parser"


class MyParser(BaseParser):
    def parse_file(self, filepath: str) -> dict | None:
        try:
            source = open(filepath, encoding="utf-8").read()
        except (OSError, IOError) as exc:
            # Recoverable — Apollo skips the file and continues.
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        try:
            return self.parse_source(source, filepath)
        except Exception:
            # Unexpected failure: include the traceback automatically.
            logger.exception("parser %s failed on %s",
                             type(self).__name__, filepath)
            return None
```

Hard rules (cribbed from [`LOGGING.md`](LOGGING.md), repeated here so
plugin authors don't miss them):

- `logger = logging.getLogger(__name__)` at the **top** of every
  parser/helper module. Never hard-code the logger name string. Apollo
  filters by dotted module path — `APOLLO_LOG_LEVEL=DEBUG
  APOLLO_LOG_FILTER=plugins.<name>.*` only works if you use `__name__`.
- **No `print()`** anywhere in plugin code (docstring example snippets
  inside `"""…"""` are fine — they're not executed).
- **Lazy `%`-formatting:** `logger.warning("could not read %s: %s",
  path, exc)`, *not* `logger.warning(f"could not read {path}: {exc}")`.
  f-strings interpolate even when the level is suppressed.
- **`logger.exception(...)` inside `except`** when you want the
  traceback; otherwise `logger.warning(..., exc_info=True)` for a
  recovered failure.
- **Pick the right level** (§ 4 of `LOGGING.md`): a file we couldn't
  read or parse but that didn't abort the index is `WARNING`; a
  genuine bug is `ERROR` / `logger.exception`. Raw tracing inside a
  hot loop is `DEBUG`.
- **Never log secrets, full file contents, or user prompt text** at
  `INFO` or above. Log a path or hash, not the body.

Apollo's CI does not yet enforce these mechanically, but the plugin
audit script (`pytest plugins/ -q` plus a grep for `print(`,
`f"…"` inside `logger.*`, and bare `except:`) is the bar a new plugin
has to clear before merge.

---

## 6. Testing your plugin

Every plugin **must** ship a `test_parser.py` **inside its own folder**.
This keeps tests self-contained alongside the code they exercise:
deleting the plugin (``rm -rf plugins/<name>/``) deletes its tests in
the same step, and third-party plugin authors don't have to know about
a separate `tests/` directory.

### Required: `plugins/<name>/test_parser.py`

At minimum the file should cover three things:

1. **Discovery** — the plugin is picked up by
   ``apollo.plugins.discover_plugins()``.
2. **Extension matching** — ``can_parse()`` accepts the right
   extensions and rejects everything else.
3. **One happy-path parse** — a small inline fixture file is parsed
   into a result dict with the standard shape.

Example (`plugins/go1/test_parser.py`):

```python
"""Self-contained smoke tests for the go1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.go1 import GoParser  # re-exported by plugins/go1/__init__.py


class TestGo1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, GoParser) for p in plugins)


class TestGo1PluginRecognisesExtension:
    def test_recognises_go_extension(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main\n")
        assert GoParser().can_parse(str(f))

    def test_rejects_non_go_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not GoParser().can_parse(str(f))


class TestGo1PluginParsesRealGo:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "main.go"
        path.write_text("package main\n\nfunc main() {}\n")
        result = GoParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # All five required keys must be present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result
```

> **Per-plugin fixtures.** Inline the data your tests need (small
> strings, ``tmp_path.write_text(...)``). Don't depend on
> ``tests/conftest.py`` fixtures — pytest's `conftest.py` discovery is
> directory-scoped, and your goal is a plugin folder that works
> standalone. If a plugin really needs shared fixtures, add a
> ``plugins/<name>/conftest.py`` next to ``test_parser.py``.

### Discovery

`pytest.ini` lists both `tests` and `plugins` under `testpaths`, so a
plain `pytest` run finds plugin tests automatically. To run **only**
your plugin's tests:

```bash
pytest plugins/go1/ -q
```

To run all plugin tests:

```bash
pytest plugins/ -q
```

### Why not `tests/`?

Keeping plugin tests in a separate top-level directory leaks plugin
concerns into the rest of the repo:

- Removing a plugin leaves dead test files behind in `tests/`.
- Third-party plugin authors have to ship two folders.
- Browsing `plugins/<name>/` doesn't show how the plugin is verified.

Cross-cutting tests that span multiple plugins / the graph builder /
the API still belong in `tests/` — that's where the shared
`conftest.py` lives. The rule of thumb: **tests that exercise
exactly one plugin go in that plugin's folder; tests that exercise
multiple components go in `tests/`.**

### Tests do **not** run at app runtime

`test_parser.py` is a pytest-only file — Apollo never imports it when
the app starts, when you index a folder, or when you run the API. It
only runs in development / CI when you invoke `pytest` explicitly.

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
- [ ] Folder contains `plugin.md` with a valid YAML front-matter block
      providing **`description`, `version`, `url`, `author`**
      (see [§ 2.5](#25-the-plugin-manifest-pluginmd)).
- [ ] Folder contains `config.json` with at minimum `"enabled": true`
      plus any plugin-specific knobs
      (see [§ 2.6](#26-the-plugin-config-configjson)).
- [ ] Folder contains `test_parser.py` covering discovery, extension
      matching, and one happy-path parse
      (see [§ 6](#6-testing-your-plugin)).
- [ ] Parser class subclasses `apollo.parser.base.BaseParser`.
- [ ] `can_parse()` returns `True` only for files this plugin handles
      **and** returns `False` when `self.config["enabled"]` is False.
- [ ] `parse_file()` returns the standard dict (or `None`).
- [ ] All five required keys (`file`, `functions`, `classes`,
      `imports`, `variables`) are always present.
- [ ] Every entry in `functions[]` and `classes[]` carries the
      builder-required keys from § 0.2 (`name`, `line_start`,
      `line_end`, `source`; classes also `bases`, `methods`).
- [ ] Every entry in `variables[]` carries `name` and `line`.
- [ ] **Relationships are extracted, not just entities** (§ 0): for a
      programming-language plugin, `functions[].calls[]` and
      `classes[].bases[]` are populated when the source has them; for
      a document plugin, `links[]` / `sections[]` / `code_blocks[]` /
      `frontmatter` are populated when the file has them.
- [ ] Methods live inside `classes[].methods[]`, **not** in the
      top-level `functions[]` list (otherwise they show up as orphan
      nodes with no parent class — see § 5.1.3).
- [ ] If your plugin is regex-based (no Python AST), it uses the
      line-tracking and brace-matching helpers from § 5.1 so every
      function/class entry carries a real `line_start` / `line_end` /
      `source`.
- [ ] `ignore_dir_markers` (if set) only contains sentinel files that
      mark directories to **skip** (e.g. `pyvenv.cfg`), never
      project-root markers like `package.json` or `composer.json`
      (see § 5.1).
- [ ] Indexing a small fixture project of your language's files via
      `python main.py index <fixture-dir>` runs to completion without
      `KeyError` and produces a graph with > 0 `calls` / `inherits` /
      `references` edges where the source warrants them.
- [ ] Any third-party deps are in
      `plugins/<name>/requirements.txt` and imported lazily inside the
      parser class.
- [ ] **Logging follows [`guides/LOGGING.md`](LOGGING.md)** (§ 5.2):
      `logger = logging.getLogger(__name__)` at module top, no
      `print()` in plugin code, lazy `%`-formatted log calls (no
      f-strings inside `logger.*`), no bare `except:`, and every
      `except` that swallows the error logs at least a `WARNING` so
      users can see which files were skipped.
- [ ] `pytest plugins/<name>/ -q` is green.
- [ ] `pytest -q` (full suite) is green.
- [ ] **Settings → Plugins** tab shows your plugin with the expected
      description, version, URL, and author after a reload.
