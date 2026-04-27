# Apollo Logging Standard

This guide is the single source of truth for **how Apollo emits diagnostic
output** — across the core (`apollo/`), the web server (`web/`), the CLI
(`main.py`), the file watcher / re-index service, and every plugin under
`plugins/`.

The goal is simple: **one consistent way to log, everywhere.** No more
ad-hoc `print()` statements scattered through libraries, no more
`logging.getLogger("…")` strings made up on the spot, no surprise output
in production.

---

## TL;DR

1. **Libraries log, CLIs print.**
   Anything inside a package (`apollo/`, `graph/`, `parser/`, `plugins/`,
   `web/server.py` request handlers) uses the `logging` module.
   Only `main.py` (CLI entrypoint) and explicit progress reporters are
   allowed to use `print()`.
2. **Always:** `logger = logging.getLogger(__name__)` at the top of the
   module. Never hard-code a logger name string.
3. **Five levels, used as documented in [§ 4](#4-log-levels).** Anything
   noisier than `INFO` belongs at `DEBUG`.
4. **Lazy formatting:**
   `logger.info("indexed %d files in %.2fs", n, dt)` — **not**
   `logger.info(f"indexed {n} files in {dt:.2f}s")`.
5. **Use `logger.exception()` inside `except` blocks** to include the
   traceback automatically.
6. **No secrets in logs.** API keys, full document contents, and user
   PII are never logged. See [§ 7](#7-what-never-to-log).
7. **Configure once at the entrypoint** via
   `apollo.logging_config.configure_logging()`; library code never calls
   `logging.basicConfig`.

---

## 1. Why a project-wide standard?

Apollo today has three styles co-existing:

- **`print()` everywhere** (≈110 call-sites in `main.py`, the indexer,
  some plugins, `web/server.py`, …) — fine for humans on a terminal,
  invisible in containers, can't be filtered by severity, and clobbers
  test output.
- **Module-level `logger = logging.getLogger(__name__)`** (already used
  in `apollo/reindex_service.py`, `watcher.py`).
- **Inline `import logging; logging.getLogger("…").warning(…)`** in
  `web/server.py` request handlers — works, but the logger name is
  hand-typed, easy to drift out of sync with the module path.

Standardising lets us:
- Filter by severity (`APOLLO_LOG_LEVEL=DEBUG`) and by subsystem
  (`apollo.reindex_service`, `plugins.python3`).
- Re-route output (file, stderr, JSON for log shippers, …) without
  touching call-sites.
- Capture / assert log messages in tests with `caplog`.
- Avoid surprising user-facing output from library code.

---

## 2. Module setup (the only line you ever copy)

At the top of **every Python module that needs to emit diagnostics**:

```python
import logging

logger = logging.getLogger(__name__)
```

`__name__` resolves to the dotted module path
(`apollo.reindex_service`, `plugins.python3.parser`,
`web.server`, …). Apollo relies on this naming so users can filter
whole subtrees:

```bash
APOLLO_LOG_LEVEL=DEBUG APOLLO_LOG_FILTER=plugins.* apollo serve
```

> **Do not** write `logging.getLogger("apollo.api")`,
> `logging.getLogger("server")`, `logging.warning(...)` (root logger),
> etc. Those bypass the module hierarchy and break filtering.

---

## 3. CLI vs. library output

| Caller                                  | Mechanism                                       | Stream            |
| --------------------------------------- | ----------------------------------------------- | ----------------- |
| **CLI command** in `main.py`            | `print(...)` for results; `print(..., file=sys.stderr)` for fatal errors before exit | stdout / stderr   |
| **Library / server / plugin / parser**  | `logger.<level>(...)`                           | configured handlers (stderr by default, optionally a file) |
| **Long-running progress** (indexer, capture) | `apollo.progress.report(...)` helper (which **also** calls `logger.info`) | stdout (TTY) + log handlers |

The simple rule: **if the user invoked `python main.py …`, output goes
through `print`; if any other code path produced it, it goes through
`logger`**. The web server is "any other code path" — never `print`
inside a request handler.

### `main.py` example

```python
def cmd_index(args):
    logger.info("indexing %s", args.target_dir)        # diagnostic
    if not Path(args.target_dir).is_dir():
        print(f"Error: '{args.target_dir}' is not a directory",
              file=sys.stderr)
        sys.exit(1)
    ...
    print(f"✓ Indexed {n} files in {dt:.2f}s")          # user-facing result
```

The emoji-prefixed status lines (`✓`, `⚠️`, `🔍`) that DESIGN.md calls
"Terminal progress logging" are **CLI surface, not log records.** They
stay as `print()` in `main.py`. Internally, the same code paths emit
matching `logger.info()` records so headless runs (web server, tests,
container) still see the events.

---

## 4. Log levels

Use the standard Python levels, with these semantics:

| Level      | When to use                                                               | Examples                                                                                         |
| ---------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `DEBUG`    | Verbose tracing for developers. **Off by default.**                        | `parsed 42 ast nodes from %s`, `cache hit for %s`                                               |
| `INFO`     | Normal lifecycle events a user would want to see in `--verbose`.          | `indexed 137 files in 4.2s`, `reindex sweep started`, `plugin %s loaded`                        |
| `WARNING`  | Something is wrong but Apollo recovered. The user might want to act.       | `plugin manifest missing: plugins/foo/plugin.md`, `falling back to text indexer for %s`         |
| `ERROR`    | An operation failed. The request / file / job did not complete.            | `failed to parse %s: %s`, `chat provider %s returned 500`                                       |
| `CRITICAL` | The process cannot continue. Reserve for genuine show-stoppers.            | `cannot open settings file (read-only fs); aborting startup`                                    |

Default level when no env var is set: **`INFO`** (so users see lifecycle
events but not internals).

### "Should this be `WARNING` or `ERROR`?"

Ask: *did the user's request still succeed?* If yes → `WARNING` (or
`INFO`). If no → `ERROR`.

---

## 5. Formatting rules

### 5.1 Lazy `%`-formatting (mandatory)

```python
# ✅ Good — args evaluated only if the level is enabled.
logger.debug("found %d candidates for %s", len(matches), query)

# ❌ Bad — f-string interpolates every time, even at INFO+.
logger.debug(f"found {len(matches)} candidates for {query}")
```

This matters most for `DEBUG` calls inside hot loops.

### 5.2 No newlines, no ANSI colour codes

The handler chain decides the output format. Don't bake `\n`, ANSI
escape codes, or terminal box-drawing into the message string — they
break log shippers, JSON output, and CI consoles.

### 5.3 One event per call

```python
# ✅ Good
logger.info("plugin %s registered", plugin.name)

# ❌ Bad — multiple loosely-related facts in one record.
logger.info("plugin %s registered, version %s, sha256 %s, took %.2fms",
            plugin.name, plugin.version, sha, dt)
```

Either split into multiple records or use the `extra=` kwarg for
structured fields.

### 5.4 Tracebacks come for free

```python
try:
    parser.parse_file(path)
except Exception:
    # logger.exception() == logger.error() + automatic traceback.
    logger.exception("parser %s failed on %s", parser.name, path)
```

Never `f"{e}"` an exception by hand and then drop the traceback.

---

## 6. Where each subsystem logs

| Subsystem                   | Logger name(s)                              | Notes                                                  |
| --------------------------- | ------------------------------------------- | ------------------------------------------------------ |
| Core graph builder          | `apollo.graph.builder`                      | `INFO` per phase (parse / link / save).                |
| Incremental re-index        | `apollo.reindex_service`, `graph.incremental` | Already conformant; keep as-is.                        |
| File watcher                | `watcher`                                   | Already conformant.                                    |
| Web server (FastAPI app)    | `web.server`, plus per-blueprint sub-loggers | Use `logger.exception()` in `except` inside handlers.  |
| Project / settings manager  | `apollo.projects.manager`, `apollo.projects.settings` | Today prints `Warning: …`; convert to `logger.warning`. |
| Plugins                     | `plugins.<name>` and `plugins.<name>.parser` | One logger per module via `__name__`.                  |
| CLI                         | `main` + `print()` for user-facing output    | `print` is allowed *only* here.                        |

---

## 7. What never to log

Hard rules:

- **No API keys, OAuth tokens, or other credentials.** The chat layer
  must mask them (the existing `_mask_key` in `web/server.py` is the
  reference implementation).
- **No full file contents.** Log file size, sha, line count — never the
  body. This includes `parse_source(source, …)` arguments.
- **No user prompt / chat content at `INFO` or above.** Bodies may be
  logged at `DEBUG` only, and only when explicitly opted-in.
- **No raw stack traces in `WARNING`.** If you have a traceback you
  want to capture, it's an `ERROR` (or `EXCEPTION`).

If you're unsure, log a short identifier (file path, request id, model
id) instead of the data itself.

---

## 8. Configuration & entrypoints

Apollo ships a single helper, `apollo.logging_config.configure_logging`,
that all entrypoints call exactly once at startup:

```python
# apollo/logging_config.py
import logging
import os
import sys


_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Set up Apollo's root loggers. Idempotent — safe to call twice."""
    level = (level or os.environ.get("APOLLO_LOG_LEVEL", "INFO")).upper()
    root = logging.getLogger()
    if getattr(root, "_apollo_configured", False):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT))
    root.addHandler(handler)
    root.setLevel(level)
    # Quiet down noisy third-party loggers we don't own.
    for noisy in ("watchdog", "urllib3", "httpx", "uvicorn.access"):
        logging.getLogger(noisy).setLevel("WARNING")
    root._apollo_configured = True
```

Call sites:

```python
# main.py (CLI)
from apollo.logging_config import configure_logging
configure_logging()                       # honours APOLLO_LOG_LEVEL

# web/server.py (FastAPI factory)
from apollo.logging_config import configure_logging
configure_logging()                       # before app = FastAPI(...)
```

### Environment variables

| Variable                          | Default | Effect                                                   |
| --------------------------------- | ------- | -------------------------------------------------------- |
| `APOLLO_LOG_LEVEL`                | `INFO`  | Root level. One of `DEBUG INFO WARNING ERROR CRITICAL`.  |
| `APOLLO_LOG_FILE`                 | unset   | If set, also tee logs to this path (rotating handler — see [§ 9](#9-log-rotation--sizing)). |
| `APOLLO_LOG_JSON`                 | `0`     | If `1`, switch the formatter to JSON (one record/line).  |
| `APOLLO_LOG_MAX_SIZE_MB`          | `100`   | Max size (in MB) of an individual log file before rollover. |
| `APOLLO_LOG_MAX_AGE_DAYS`         | `7`     | Days to retain rotated log files. Older files are pruned. |
| `APOLLO_LOG_ROTATED_TOTAL_MB`     | `1024`  | Total size cap (in MB) for *all* rotated files combined. Oldest are deleted first. |

Library code **never** reads these directly — they belong to the
configurator only.

---

## 9. Log rotation & sizing

When `APOLLO_LOG_FILE` is set, `configure_logging()` attaches a
**managed rotating file handler** alongside the stderr stream handler.
The design follows three independent caps, inspired by Couchbase Sync
Gateway's logging (and Apollo's sibling project, PouchPipes):

| Setting (env var)                | Default  | What it bounds                                           |
| -------------------------------- | -------- | -------------------------------------------------------- |
| `APOLLO_LOG_MAX_SIZE_MB`         | `100` MB | Single-file size before the active log rolls over.       |
| `APOLLO_LOG_MAX_AGE_DAYS`        | `7` days | Age at which any rotated file is eligible for deletion.  |
| `APOLLO_LOG_ROTATED_TOTAL_MB`    | `1024` MB| Total disk budget for **all** rotated files combined.    |

The three caps are evaluated independently after every rollover:

1. Active file reaches `max_size` → renamed to
   `<name>.YYYYMMDD-HHMMSS.log` and a fresh active file is opened.
2. Any rotated file older than `max_age` days → deleted.
3. If the sum of all rotated files still exceeds
   `rotated_logs_size_limit`, the **oldest** rotated files are deleted
   until the budget is satisfied.

The active log file itself is never deleted — only rotated copies.

### Reference implementation

```python
# apollo/logging_config.py (excerpt)
import logging
import logging.handlers
import os
import time
from pathlib import Path


class ManagedRotatingFileHandler(logging.handlers.BaseRotatingHandler):
    """
    Size-based rollover with two extra caps applied after each rotation:
      - max_age_days: prune rotated files older than this.
      - total_mb_cap: keep the sum of all rotated files under this budget.
    The active file is never pruned, only renamed on rollover.
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int,
        max_age_days: int,
        total_mb_cap: int,
        encoding: str = "utf-8",
    ):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(filename, mode="a", encoding=encoding, delay=False)
        self._max_bytes = max_bytes
        self._max_age_seconds = max_age_days * 86_400
        self._total_bytes_cap = total_mb_cap * 1024 * 1024

    def shouldRollover(self, record) -> bool:
        if self._max_bytes <= 0 or self.stream is None:
            return False
        msg = self.format(record) + self.terminator
        self.stream.seek(0, 2)  # end of file
        return self.stream.tell() + len(msg.encode(self.encoding)) >= self._max_bytes

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        rotated = f"{self.baseFilename}.{time.strftime('%Y%m%d-%H%M%S')}.log"
        try:
            os.replace(self.baseFilename, rotated)
        except FileNotFoundError:
            pass
        self.stream = self._open()
        self._enforce_caps()

    def _enforce_caps(self) -> None:
        base = Path(self.baseFilename)
        rotated = sorted(
            base.parent.glob(f"{base.name}.*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        now = time.time()

        # 1. Age-based pruning.
        if self._max_age_seconds > 0:
            for p in list(rotated):
                if now - p.stat().st_mtime > self._max_age_seconds:
                    p.unlink(missing_ok=True)
                    rotated.remove(p)

        # 2. Total-size budget (delete oldest first).
        if self._total_bytes_cap > 0:
            total = sum(p.stat().st_size for p in rotated)
            for p in rotated:
                if total <= self._total_bytes_cap:
                    break
                total -= p.stat().st_size
                p.unlink(missing_ok=True)


def _attach_rotating_file_handler(root: logging.Logger, formatter) -> None:
    path = os.environ.get("APOLLO_LOG_FILE")
    if not path:
        return
    handler = ManagedRotatingFileHandler(
        filename=path,
        max_bytes=int(os.environ.get("APOLLO_LOG_MAX_SIZE_MB", "100")) * 1024 * 1024,
        max_age_days=int(os.environ.get("APOLLO_LOG_MAX_AGE_DAYS", "7")),
        total_mb_cap=int(os.environ.get("APOLLO_LOG_ROTATED_TOTAL_MB", "1024")),
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
```

`configure_logging()` calls `_attach_rotating_file_handler(root, formatter)`
right after attaching the stderr handler, so file output is purely
additive — stderr remains the canonical stream for containers and CI.

### Operational notes

- **Log directory.** Default for Apollo is `.apollo/logs/apollo.log`.
  The directory is created on first write; never check it into git
  (already covered by `.apollo/` being gitignored).
- **Concurrent processes.** The CLI, `web/server.py`, and the watcher
  may all be running at once. Each process opens its own file handle;
  if they share `APOLLO_LOG_FILE`, expect interleaved rollovers. For
  production deployments, set distinct paths per process
  (`apollo-cli.log`, `apollo-web.log`, `apollo-watcher.log`) or rely on
  an external log shipper instead.
- **Disk-full safety.** The handler honours `total_mb_cap` *after* a
  rollover, so a single oversized record can briefly push the budget
  over the line. Size budgets are eventually-consistent, not hard caps.
- **Rotated filename format.** `<base>.<YYYYMMDD-HHMMSS>.log` keeps
  files lexically sortable by rotation time, which matches the
  oldest-first deletion logic.
- **JSON mode.** `APOLLO_LOG_JSON=1` only swaps the *formatter*; the
  rotation handler is unchanged. JSON records are still subject to all
  three size caps.

### Quick recipes

```bash
# Local dev: small files, keep a week, 200 MB total
APOLLO_LOG_FILE=.apollo/logs/apollo.log \
APOLLO_LOG_MAX_SIZE_MB=10 \
APOLLO_LOG_MAX_AGE_DAYS=7 \
APOLLO_LOG_ROTATED_TOTAL_MB=200 \
python main.py serve

# Production: 100 MB files, 30 days, 5 GB cap, JSON for log shipper
APOLLO_LOG_FILE=/var/log/apollo/apollo.log \
APOLLO_LOG_JSON=1 \
APOLLO_LOG_MAX_SIZE_MB=100 \
APOLLO_LOG_MAX_AGE_DAYS=30 \
APOLLO_LOG_ROTATED_TOTAL_MB=5120 \
uvicorn web.server:app
```

---

## 10. Plugin authors (read this!)

A plugin is a regular Python package living under `plugins/<name>/`. The
same rules apply: `logger = logging.getLogger(__name__)` at the top of
every module, never `print()`.

```python
# plugins/go1/parser.py
import logging
from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)   # → "plugins.go1.parser"


class GoParser(BaseParser):
    def parse_file(self, filepath: str):
        try:
            ...
        except SyntaxError:
            logger.warning("syntax error in %s; falling back to text", filepath)
            return None
        except Exception:
            logger.exception("unexpected failure parsing %s", filepath)
            return None
```

Why this matters: users running Apollo against thousands of files want
to see *which plugin* skipped *which file* without enabling debug-level
firehose. Module-scoped loggers make
`APOLLO_LOG_LEVEL=DEBUG` selectable per plugin.

---

## 11. Tests

Use pytest's built-in `caplog` fixture. Don't capture `stdout` for log
assertions — that only works while we still have stray `print()`s.

```python
def test_plugin_warns_on_missing_manifest(caplog):
    with caplog.at_level("WARNING", logger="apollo.projects.settings"):
        detect_installed_plugins()
    assert any("plugin manifest missing" in r.message
               for r in caplog.records)
```

For the few CLI commands that legitimately `print`, capture `capsys`.

---

## 12. Migration plan

The codebase is mid-migration. New code MUST follow this guide; existing
code is converted incrementally. Suggested order, smallest first:

1. ✅ `apollo/reindex_service.py`, `watcher.py` — already conformant.
2. ⚠️ `web/server.py` — replace inline `import logging` and
   `logging.warning(...)` calls with a module-level
   `logger = logging.getLogger(__name__)`.
3. ⚠️ `apollo/projects/settings.py`, `apollo/projects/manager.py` —
   convert `print(f"Warning: …")` to `logger.warning(…)`.
4. ⚠️ Plugins (`plugins/python3/parser.py`,
   `plugins/markdown_gfm/parser.py`, `plugins/pdf_pypdf/parser.py`) —
   add module loggers, swap any `print(...)` for `logger.<level>(...)`.
5. ⚠️ `main.py` — keep `print` for user-facing output and stderr error
   reports, but add `configure_logging()` at entry and a
   `logger.info("CLI: %s", args.command)` line per command.
6. Plain-text `print()` left in tests, demos, and `_dev_only/` is fine.

Track progress as commits with a `log:` prefix:

```
log: convert apollo/projects/settings.py to module logger
log: web/server.py – replace inline logger names
log: plugin python3 – use module logger, drop two prints
```

---

## 13. Cheat sheet

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("loop %d/%d", i, n)                          # dev tracing
logger.info("indexed %d files in %.2fs", n, dt)           # lifecycle
logger.warning("plugin manifest missing for %s", name)    # recovered
logger.error("chat provider %s returned 500", provider)   # request failed
try:
    risky()
except Exception:
    logger.exception("risky() failed for %s", item)       # ERROR + traceback
```

```bash
# Run with verbose output
APOLLO_LOG_LEVEL=DEBUG python main.py index .

# Run with JSON-formatted logs to a file
APOLLO_LOG_LEVEL=INFO APOLLO_LOG_JSON=1 \
APOLLO_LOG_FILE=.apollo/server.log \
uvicorn web.server:app
```

That's the entire standard. Anything not covered here, default to
"behave like `apollo/reindex_service.py`."
