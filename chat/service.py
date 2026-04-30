"""
AI Chat service — Grok API with internal tool-calling for graph queries.

Flow:
  1. User question → Grok with tool definitions for internal APIs
  2. If Grok calls a tool → execute the internal query → feed results back
  3. Repeat until Grok produces a final text response
  4. Stream the final answer to the client
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Iterator, Optional

import networkx as nx


logger = logging.getLogger(__name__)


def _preview(s: str, n: int = 200) -> str:
    """Single-line, length-capped preview of `s` for log lines."""
    if s is None:
        return ""
    s = str(s).replace("\n", "\\n").replace("\r", "")
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n} chars)"


# Lazy import — falls back to JSON pass-through if `python-toon` isn't
# installed (e.g. someone running on an older venv). We measure the
# JSON→TOON delta and emit it to the trace stream so the user can see
# how much context savings each tool call buys them.
try:
    from toon import encode as _toon_encode  # type: ignore
    _TOON_AVAILABLE = True
except Exception:  # pragma: no cover — only hit when the dep is missing
    _toon_encode = None  # type: ignore
    _TOON_AVAILABLE = False


def _to_toon_for_llm(json_str: str) -> tuple[str, bool]:
    """Convert a JSON tool-result string to TOON for sending to the LLM.

    Returns `(payload, is_toon)`. Falls back to the original JSON when:
      - `python-toon` isn't installed
      - the input isn't parseable JSON
      - the encoder raises (some exotic structures aren't supported)
      - TOON would actually be LARGER than JSON (rare, but happens for
        tiny / heterogeneous payloads)
    """
    if not _TOON_AVAILABLE or _toon_encode is None:
        return json_str, False
    try:
        obj = json.loads(json_str)
    except Exception:
        return json_str, False
    try:
        encoded = _toon_encode(obj)
    except Exception:
        return json_str, False
    if not isinstance(encoded, str) or len(encoded) >= len(json_str):
        return json_str, False
    return encoded, True


SYSTEM_PROMPT = """You are a code and file exploration assistant embedded in Apollo,
an app that indexes local codebases into a knowledge graph.

You have tools to query the user's indexed graph. Use them when the question is about their
files, code, functions, classes, imports, or project structure. For general questions
(greetings, explanations, opinions, non-code topics), just answer directly — no tool needed.

Tool results come back in TOON (Token-Oriented Object Notation) instead of JSON to save
context. TOON uses indentation for nesting and a CSV-like header `[N,]{field1,field2,…}:`
followed by one comma-separated row per item for uniform arrays. For example,
`results[3,]{id,name,type,path,line_start}:` is followed by 3 lines of CSV data, and
each row maps positionally to those fields. Treat it as JSON-equivalent — the data
model is identical; only the wire format is denser.

When you reference a code entity in your answer, wrap its node ID in double brackets
like [[func::path/file.py::name]] so the UI can highlight it in the graph.

ALWAYS format your answers as proper Markdown:
- Use `## Heading` for the main topic and `###` for sub-sections.
- Use bullet lists (`- item`) for enumerations and nested lists for hierarchy.
- Use **bold** for key terms and `inline code` for symbol/function/file names.
- Wrap code in fenced blocks with a language tag, e.g. ```python ... ```.
- Use `>` blockquotes to highlight summaries or important callouts.
- Prefer short paragraphs separated by blank lines over walls of text.

Workflow when the user asks a multi-file/multi-symbol question:
1. Use `search_graph_multi` with synonyms when the topic is fuzzy (e.g. ["couchbase","cblite","lite"]).
2. Use `get_neighbors` (not multiple `get_node` calls) when exploring a cluster of related nodes.
3. **Budget yourself ~3 tool-call rounds.** Prefer ONE `search_graph_multi` over many sequential
   `search_graph` calls. Do not re-issue near-duplicate queries — if you've already searched for
   "work" as a function, do NOT also search for "work function" / "work method" / "work class"
   one-by-one; pass them all to `search_graph_multi` in a single call.
4. **As soon as you can answer the user's actual question with reasonable confidence, call
   `return_result`.** It is far better to ship a `confidence=med` answer than to keep digging
   and time out. Exhausting the round budget yields a worse user experience than a partial
   answer with a clear "could not verify X" caveat.
5. `return_result(summary, files, node_refs)` — do NOT just emit prose.
   - The `summary` field MUST itself be well-formatted Markdown (headings, bullets, code blocks).
   - Pass file paths in `files` and node IDs in `node_refs` — the UI renders them as clickable chips, so do NOT also list them inline in the summary.
   - Set `confidence` to "high", "med", or "low" so the UI can show a status dot.

Workflow when the user asks about THEIR notes, bookmarks, highlights, or
"what did I save / annotate":
1. Call `list_notes` (optionally with `type="note"` or `type="bookmark"`).
   This returns the user's own annotations with their `target` (file path or
   graph node ID), `content`, `tags`, and `created_at`.
2. For each annotation pointing at a graph node, you may call `get_node` or
   `get_neighbors` to bring in relationship context (callers, callees,
   imports, defining file). For file targets, use `get_file_section`.
3. Cite annotation IDs (e.g. `an::a1b2c3...`) and the target node IDs in
   your final `return_result` so the UI can chip them.

Workflow when debugging a specific file or unfamiliar source:
1. Call `file_stats(path)` first — it's cheap and tells you size, line count, md5, function/class counts, and top-level imports.
2. If the user is unsure which file is involved, use `project_search(pattern)` to grep across the indexed project. Default context is 5 lines before/after each match.
3. Drill in with `get_function_source(path, name)` or `get_file_section(path, start, end)` to read targeted slices INSTEAD of asking the user to paste the file. These tools are read-only — you can never modify files; just describe what should change and where.
4. Always cite line numbers as `path:line_no` so the user can jump to them.

Be concise but thorough."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_graph",
            "description": "Search the knowledge graph by keyword or semantic similarity. Returns matching nodes (functions, classes, files, etc.) with their type, path, and line number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — a function name, class name, keyword, or natural-language description.",
                    },
                    "top": {
                        "type": "integer",
                        "description": "Max results to return (default 10).",
                    },
                    "type": {
                        "type": "string",
                        "description": "Optional node type filter: function, class, method, file, directory, variable, import, section, code_block, link, table, task_item.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node",
            "description": "Get full details of a specific node by its ID, including source code, metadata, and all incoming/outgoing edges (callers, callees, imports, definitions).",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The node ID, e.g. 'func::src/utils.py::my_function' or 'class::models.py::User'.",
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": "Get summary statistics about the indexed graph: total nodes, total edges, counts by node type and edge type.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wordcloud",
            "description": (
                "Get the most strongly-connected entity names in the graph, weighted "
                "by graph strength (sum of in+out degree across nodes sharing a name). "
                "Use this to understand which symbols are 'hubs' in the codebase — "
                "high-strength names are likely to be impacted by, or to impact, "
                "changes elsewhere. Returns up to `limit` items, each with `name`, "
                "`strength` (sum of degrees), and `count` (number of nodes sharing "
                "that name). Use `mode='all'` only when you need the long tail "
                "(weakly-connected helpers and singletons)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["strong", "relevant", "all"],
                        "description": (
                            "strong=top 30 hubs (default); relevant=top 100 with "
                            "strength>=2; all=everything (capped at 500)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional cap on returned items (defaults to the mode's natural cap).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_graph_multi",
            "description": "Run multiple graph searches in parallel and return a single deduped, score-merged result list. Use this to cast a wide net with synonyms (e.g. ['couchbase','cblite','lite']) instead of issuing N sequential search_graph calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of search strings to run.",
                    },
                    "top": {
                        "type": "integer",
                        "description": "Max results per query (default 10). The merged output may be larger.",
                    },
                    "type": {
                        "type": "string",
                        "description": "Optional node type filter applied to every sub-query.",
                    },
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_neighbors",
            "description": "BFS-walk the knowledge graph from a node. Use this (instead of multiple get_node calls) to explore callers/callees/importers in one round-trip.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Starting node ID.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "BFS depth (default 1).",
                    },
                    "edge_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional edge-type filter, e.g. ['calls','imports','defines'].",
                    },
                    "direction": {
                        "type": "string",
                        "description": "'in' = predecessors, 'out' = successors, 'both' = both (default).",
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_stats",
            "description": "Cheap structural summary of a file (size, line count, md5, language, function/class counts, top-level imports). Read-only. Call this BEFORE asking the user to paste source — you can usually answer without ever seeing the bytes.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute or project-relative file path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_section",
            "description": "Return an inclusive 1-indexed line range from a file. Read-only. Hard cap 800 lines per call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "expected_md5": {"type": "string", "description": "Optional. If provided and the file has changed, the call returns 409 with the new md5."},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_function_source",
            "description": "AST-extract the full source of a function, method, or class by name. `name` can be `foo`, `MyClass.foo`, or `MyClass`. Includes decorators and docstring. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "name": {"type": "string"},
                    "expected_md5": {"type": "string"},
                },
                "required": ["path", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "Grep within a single file. Returns matches with N lines of context before/after. Read-only. Cap 200 matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "context": {"type": "integer", "description": "Lines of context before AND after each match (default 5)."},
                    "regex": {"type": "boolean", "description": "Treat pattern as regex (default true)."},
                    "expected_md5": {"type": "string"},
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_search",
            "description": "Grep across the indexed project. Returns matches with file/line/context. Read-only. Cap 500 matches or 200KB total. Use this when the user is unsure which file contains the issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "root": {"type": "string", "description": "Optional sub-directory; defaults to the indexed root."},
                    "context": {"type": "integer", "description": "Lines of context (default 5)."},
                    "file_glob": {"type": "string", "description": "Comma-separated globs (default '*.py'). E.g. '*.py,*.md'."},
                    "regex": {"type": "boolean", "description": "Treat pattern as regex (default true)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List the user's own annotations (highlights, NOTES, BOOKMARKS, tags) attached to files or graph nodes in the current project. Use this any time the user asks about THEIR notes, bookmarks, highlights, recent annotations, or 'what did I save'. Newest first. Each item has a `target` linking to either a file path or a graph node ID, which can then be expanded with `get_node` / `get_neighbors` for relationship context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["highlight", "bookmark", "note", "tag"],
                        "description": "Optional type filter. Omit to list all annotations.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max items to return (default 25).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notes_by_target",
            "description": "Find every annotation attached to a single file or graph node. Useful for joining the user's notes/bookmarks to graph context — e.g. fetch every note attached to a node returned by `search_graph`. Provide exactly one of `file` or `node`.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Graph node ID, e.g. `func::src/main.py::main`.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notes_by_tag",
            "description": "Find every annotation carrying a given tag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                },
                "required": ["tag"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_result",
            "description": "FINAL ANSWER. Call this exactly once when you have enough information to answer the user. Bounds the tool-call loop and supplies structured citations the UI renders as clickable chips. After calling this, do NOT emit any further prose.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Markdown answer for the user.",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths relevant to the answer (rendered as clickable chips).",
                    },
                    "node_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Node IDs referenced (rendered as clickable chips).",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "med", "low"],
                        "description": "Self-rated confidence in the answer.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]

# Max tool-call rounds before we strip `tools=` and force the model to
# write a final text answer. Bumped from 5 → 8 because Grok tends to
# explore aggressively (esp. for "which X is most used" style questions
# that fan out across search_graph / get_neighbors / get_node) and 5
# rounds was tipping requests into `rounds_exhausted` with no answer.
_MAX_TOOL_ROUNDS = 8


class ChatService:
    """Manages the chat pipeline: user question → LLM (with tools) → response.

    The active LLM provider (xAI / OpenAI / Gemini / Anthropic / Llama-via-Groq)
    and its model are resolved at request time from the settings file via the
    `settings_provider` callback so that changes in the Settings page take
    effect without restarting the server.
    """

    def __init__(
        self,
        graph: nx.DiGraph,
        search=None,
        embedder=None,
        model: str | None = None,
        root_dir: str | None = None,
        settings_provider=None,
        project_manager=None,
    ):
        from apollo.chat.providers import get_provider, DEFAULT_PROVIDER

        self.graph = graph
        self.search = search
        self.embedder = embedder
        self.root_dir = root_dir
        # Optional ProjectManager — used to resolve the AnnotationManager
        # for the user's notes / bookmarks tools. May be None when chat is
        # constructed before a project is open.
        self._project_manager = project_manager
        # `settings_provider` is a zero-arg callable returning the parsed
        # settings dict. Injected by web.server.create_app so the service
        # always sees the latest active_provider / model selection.
        self._settings_provider = settings_provider
        # Cached client keyed by (provider_id, api_key) — invalidated whenever
        # the active provider or its key changes.
        self._client = None
        self._client_key: tuple[str, str] | None = None
        self._query = None  # lazy GraphQuery

        # Back-compat: callers that pass an explicit `model=` still work.
        # Otherwise use the active provider's default.
        if model:
            self.model = model
        else:
            self.model = get_provider(DEFAULT_PROVIDER)["default_model"]

    # ── Active provider resolution ─────────────────────────────────

    def _active(self) -> tuple[str, str]:
        """Return (provider_id, model) from settings, with sensible fallbacks."""
        from apollo.chat.providers import (
            DEFAULT_PROVIDER,
            PROVIDERS,
            get_provider,
        )

        settings = {}
        if self._settings_provider:
            try:
                settings = self._settings_provider() or {}
            except Exception:
                settings = {}

        chat_cfg = settings.get("chat", {}) or {}
        pid = chat_cfg.get("active_provider") or DEFAULT_PROVIDER
        if pid not in PROVIDERS:
            pid = DEFAULT_PROVIDER

        providers_cfg = chat_cfg.get("providers", {}) or {}
        model = (providers_cfg.get(pid, {}) or {}).get("model")
        if not model:
            # Legacy single-model setting
            model = chat_cfg.get("default_model") or get_provider(pid)["default_model"]
        return pid, model

    @property
    def active_provider(self) -> str:
        return self._active()[0]

    @property
    def active_model(self) -> str:
        return self._active()[1]

    @property
    def available(self) -> bool:
        from apollo.chat.providers import env_key
        pid, _ = self._active()
        return bool(os.environ.get(env_key(pid)))

    def reset_client(self) -> None:
        """Force the next call to rebuild the OpenAI client (new key/provider)."""
        self._client = None
        self._client_key = None

    def _get_client(self):
        from apollo.chat.providers import get_provider, env_key

        pid, _ = self._active()
        api_key = os.environ.get(env_key(pid))
        if not api_key:
            raise RuntimeError(
                f"{env_key(pid)} environment variable is not set "
                f"(active provider: {pid})"
            )

        cache_key = (pid, api_key)
        if self._client is None or self._client_key != cache_key:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key, base_url=get_provider(pid)["base_url"])
            self._client_key = cache_key
        return self._client

    def _get_query(self):
        if self._query is None:
            from apollo.graph.query import GraphQuery
            self._query = GraphQuery(self.graph)
        return self._query

    def _get_annotation_manager(self):
        """Return an `AnnotationManager` for the active project, or None.

        Returns None when there is no project open or no project_manager
        was injected — caller should treat that as "no annotations".
        """
        pm = self._project_manager
        if pm is None:
            return None
        if not getattr(pm, "manifest", None) or not getattr(pm, "root_dir", None):
            return None
        try:
            from apollo.projects.annotations import AnnotationManager
            return AnnotationManager(
                project_root=pm.root_dir,
                project_id=pm.manifest.project_id,
            )
        except Exception:
            return None

    # ── Tool execution ─────────────────────────────────────────────

    def _exec_tool(self, name: str, args: dict) -> str:
        """Execute an internal tool and return JSON result."""
        t0 = time.time()
        logger.info(
            "tool.call name=%s args=%s",
            name,
            _preview(json.dumps(args, default=str), 400),
        )
        try:
            result = self._exec_tool_impl(name, args)
        except Exception as e:
            logger.exception("tool.error name=%s after=%.2fs", name, time.time() - t0)
            return json.dumps({"error": f"tool {name} crashed: {e}"})
        dt = time.time() - t0
        logger.info(
            "tool.return name=%s bytes=%d dt=%.2fs preview=%s",
            name,
            len(result),
            dt,
            _preview(result, 200),
        )
        return result

    def _exec_tool_impl(self, name: str, args: dict) -> str:
        """Internal: actual tool dispatch (wrapped by `_exec_tool` for logging)."""
        q = self._get_query()

        if name == "search_graph":
            query_text = args.get("query", "")
            top = args.get("top", 10)
            type_filter = args.get("type")

            # Try semantic search first, fall back to name matching
            if self.search and hasattr(self.search, "has_embeddings") and self.search.has_embeddings():
                results = self.search.search(query_text, top_k=top, node_type=type_filter)
            else:
                results = q.find(query_text, node_type=type_filter)[:top]

            trimmed = []
            for r in results:
                trimmed.append({
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "path": r.get("path"),
                    "line_start": r.get("line_start"),
                })
            return json.dumps({"results": trimmed}, default=str)

        elif name == "get_node":
            node_id = args.get("node_id", "")
            if node_id not in self.graph:
                return json.dumps({"error": f"Node not found: {node_id}"})

            data = {k: v for k, v in self.graph.nodes[node_id].items() if k != "embedding"}

            edges_in = []
            for pred in self.graph.predecessors(node_id):
                edata = dict(self.graph.edges[pred, node_id])
                edges_in.append({"source": pred, "type": edata.get("type", "")})

            edges_out = []
            for succ in self.graph.successors(node_id):
                edata = dict(self.graph.edges[node_id, succ])
                edges_out.append({"target": succ, "type": edata.get("type", "")})

            # Truncate source to avoid blowing context
            source = data.get("source", "")
            if len(source) > 2000:
                source = source[:2000] + "\n... (truncated)"
                data = dict(data)
                data["source"] = source

            return json.dumps({"id": node_id, **data, "edges_in": edges_in, "edges_out": edges_out}, default=str)

        elif name == "get_stats":
            return json.dumps(q.stats(), default=str)

        elif name == "search_graph_multi":
            queries = args.get("queries") or []
            top = args.get("top", 10)
            type_filter = args.get("type")
            merged: dict[str, dict] = {}
            scores: dict[str, float] = {}
            has_sem = bool(self.search and hasattr(self.search, "has_embeddings") and self.search.has_embeddings())
            for qstr in queries:
                if not qstr:
                    continue
                if has_sem:
                    rows = self.search.search(qstr, top_k=top, node_type=type_filter)
                else:
                    rows = q.find(qstr, node_type=type_filter)[:top]
                for r in rows:
                    rid = r.get("id")
                    if not rid:
                        continue
                    s = float(r.get("score", 0.0) or 0.0)
                    if rid not in merged:
                        merged[rid] = {
                            "id": rid,
                            "name": r.get("name"),
                            "type": r.get("type"),
                            "path": r.get("path"),
                            "line_start": r.get("line_start"),
                            "matched_queries": [qstr],
                        }
                        scores[rid] = s
                    else:
                        merged[rid]["matched_queries"].append(qstr)
                        scores[rid] = max(scores[rid], s)
            ordered = sorted(
                merged.values(),
                key=lambda r: (scores.get(r["id"], 0.0), len(r["matched_queries"])),
                reverse=True,
            )
            return json.dumps({"results": ordered, "queries": queries}, default=str)

        elif name == "get_neighbors":
            node_id = args.get("node_id", "")
            depth = int(args.get("depth", 1) or 1)
            edge_types = args.get("edge_types")
            direction = args.get("direction", "both") or "both"
            if node_id not in self.graph:
                return json.dumps({"error": f"Node not found: {node_id}"})
            rows = q.neighbors(node_id, depth=depth, edge_types=edge_types, direction=direction)
            trimmed = [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "path": r.get("path"),
                    "line_start": r.get("line_start"),
                    "depth": r.get("depth"),
                }
                for r in rows
            ]
            return json.dumps({"node_id": node_id, "neighbors": trimmed}, default=str)

        elif name in ("file_stats", "get_file_section", "get_function_source", "file_search", "project_search"):
            from apollo import file_inspect
            try:
                if name == "file_stats":
                    return json.dumps(file_inspect.file_stats(self.graph, self.root_dir, args["path"]), default=str)
                if name == "get_file_section":
                    return json.dumps(file_inspect.get_file_section(
                        self.graph, self.root_dir,
                        args["path"], int(args["start_line"]), int(args["end_line"]),
                        expected_md5=args.get("expected_md5"),
                    ), default=str)
                if name == "get_function_source":
                    return json.dumps(file_inspect.get_function_source(
                        self.graph, self.root_dir,
                        args["path"], args["name"],
                        expected_md5=args.get("expected_md5"),
                    ), default=str)
                if name == "file_search":
                    return json.dumps(file_inspect.file_search(
                        self.graph, self.root_dir,
                        args["path"], args["pattern"],
                        context=int(args.get("context", 5) or 5),
                        regex=bool(args.get("regex", True)),
                        expected_md5=args.get("expected_md5"),
                    ), default=str)
                if name == "project_search":
                    return json.dumps(file_inspect.project_search(
                        self.graph, self.root_dir,
                        args["pattern"],
                        root=args.get("root"),
                        context=int(args.get("context", 5) or 5),
                        file_glob=args.get("file_glob", "*.py") or "*.py",
                        regex=bool(args.get("regex", True)),
                    ), default=str)
            except file_inspect.FileChangedError as e:
                return json.dumps({"error": str(e), "expected_md5": e.expected, "actual_md5": e.actual, "status": 409})
            except file_inspect.FileAccessError as e:
                return json.dumps({"error": str(e), "status": e.status_code})

        elif name in ("list_notes", "notes_by_target", "notes_by_tag"):
            mgr = self._get_annotation_manager()
            if mgr is None:
                return json.dumps({
                    "annotations": [],
                    "note": "No project is open; the user has no annotations yet.",
                })
            try:
                if name == "list_notes":
                    items = mgr.list_all()
                    type_filter = args.get("type")
                    if type_filter:
                        items = [a for a in items if a.type == type_filter]
                    items = sorted(items, key=lambda a: a.created_at, reverse=True)
                    limit = int(args.get("limit", 25) or 25)
                    items = items[:limit]
                elif name == "notes_by_target":
                    file = args.get("file")
                    node = args.get("node")
                    if not file and not node:
                        return json.dumps({"error": "Provide either `file` or `node`."})
                    items = mgr.find_by_target_file(file) if file else mgr.find_by_target_node(node)
                else:  # notes_by_tag
                    tag = args.get("tag")
                    if not tag:
                        return json.dumps({"error": "`tag` is required."})
                    items = mgr.find_by_tag(tag)
                return json.dumps(
                    {"annotations": [a.to_dict() for a in items]},
                    default=str,
                )
            except Exception as e:
                return json.dumps({"error": f"annotation lookup failed: {e}"})

        elif name == "get_wordcloud":
            from collections import defaultdict
            exclude = {"directory", "file", "import"}
            mode = (args.get("mode") or "strong").lower()
            strengths: dict[str, float] = defaultdict(float)
            counts: dict[str, int] = defaultdict(int)
            for nid, data in self.graph.nodes(data=True):
                if data.get("type", "") in exclude:
                    continue
                n = data.get("name", "")
                if not n:
                    continue
                try:
                    deg = self.graph.degree(nid)
                except Exception:
                    deg = 0
                strengths[n] += deg
                counts[n] += 1
            items = [
                {"name": n, "strength": float(strengths[n]), "count": counts[n]}
                for n in strengths
            ]
            items.sort(key=lambda x: x["strength"], reverse=True)
            total = len(items)
            if mode == "all":
                cap = 500
                items = items[:cap]
                min_strength = 0
            elif mode == "relevant":
                cap = 100
                items = [i for i in items if i["strength"] >= 2][:cap]
                min_strength = 2
            else:
                mode = "strong"
                cap = 30
                items = [i for i in items if i["strength"] >= 2][:cap]
                min_strength = 2
            requested_limit = args.get("limit")
            if isinstance(requested_limit, int) and requested_limit > 0:
                items = items[:requested_limit]
            return json.dumps({
                "items": items,
                "total": total,
                "shown": len(items),
                "mode": mode,
                "min_strength": min_strength,
            })

        return json.dumps({"error": f"Unknown tool: {name}"})

    # ── Chat methods ───────────────────────────────────────────────

    @staticmethod
    def _format_return_result(args: dict) -> str:
        """Render a `return_result` tool-call payload as the assistant's final markdown.

        Files / node_refs / confidence are emitted as raw HTML markers (`<div class="rr-...">`)
        so the frontend can render them as clickable chips with a confidence dot.
        The `summary` is left as Markdown; the frontend renders it through `marked.parse`
        which preserves the HTML blocks verbatim.
        """
        summary = (args.get("summary") or "").rstrip()
        files = args.get("files") or []
        node_refs = args.get("node_refs") or []
        confidence = (args.get("confidence") or "").lower()

        parts: list[str] = []
        if summary:
            parts.append(summary)

        if files:
            chips_html = "".join(
                f'<span class="rr-chip" data-rr-file="{f}">📄 {f}</span>'
                for f in files
            )
            parts.append(
                f'\n\n<div class="rr-section-label">Files</div>'
                f'<div class="rr-chips">{chips_html}</div>'
            )

        if node_refs:
            chips_html = "".join(
                f'<span class="rr-chip" data-rr-node="{nid}">🔗 {nid.split("::")[-1]}</span>'
                for nid in node_refs
            )
            parts.append(
                f'\n\n<div class="rr-section-label">Refs</div>'
                f'<div class="rr-chips">{chips_html}</div>'
            )

        if confidence in ("high", "med", "low"):
            parts.append(
                f'\n\n<div class="rr-confidence {confidence}">Confidence: {confidence}</div>'
            )

        return "".join(parts)

    def chat(
        self,
        message: str,
        history: list[dict] | None = None,
        context_node_id: str | None = None,
        model: str | None = None,
    ) -> str:
        """Send a chat message and return the full response (non-streaming)."""
        messages = self._build_messages(message, history, context_node_id)
        client = self._get_client()
        use_model = model or self.active_model
        rid = uuid.uuid4().hex[:8]
        t_start = time.time()
        logger.info(
            "chat.request id=%s mode=blocking provider=%s model=%s history=%d "
            "ctx=%s msg=%s",
            rid, self.active_provider, use_model, len(history or []),
            context_node_id, _preview(message, 300),
        )

        for round_idx in range(_MAX_TOOL_ROUNDS):
            t_round = time.time()
            response = client.chat.completions.create(
                model=use_model, messages=messages, tools=TOOLS,
            )
            choice = response.choices[0]
            logger.info(
                "chat.round id=%s round=%d finish=%s dt=%.2fs tool_calls=%d",
                rid, round_idx, choice.finish_reason, time.time() - t_round,
                len(choice.message.tool_calls or []) if choice.message else 0,
            )

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                # Check for `return_result` — terminate immediately.
                for tc in choice.message.tool_calls:
                    if tc.function.name == "return_result":
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        logger.info(
                            "chat.done id=%s reason=return_result total_dt=%.2fs",
                            rid, time.time() - t_start,
                        )
                        return self._format_return_result(args)

                messages.append(choice.message)
                for tc in choice.message.tool_calls:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    result = self._exec_tool(tc.function.name, args)
                    payload, is_toon = _to_toon_for_llm(result)
                    if is_toon:
                        logger.info(
                            "chat.toon id=%s tool=%s json=%dB toon=%dB saved=%.1f%%",
                            rid, tc.function.name, len(result), len(payload),
                            100 * (1 - len(payload) / max(len(result), 1)),
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": payload,
                    })
                continue

            content = choice.message.content or ""
            logger.info(
                "chat.done id=%s reason=text total_dt=%.2fs bytes=%d",
                rid, time.time() - t_start, len(content),
            )
            return content

        # Exhausted rounds — force a response without tools. The synthetic
        # user message mirrors the streaming path: without it the model often
        # returns an empty reply because its prior turn ended with tool_calls.
        logger.warning("chat.rounds_exhausted id=%s — forcing final completion", rid)
        messages.append({
            "role": "user",
            "content": (
                "You have used all available tool-call rounds. Do NOT request "
                "more tools. Based ONLY on the information already gathered "
                "above, write a complete final answer to my original question "
                "in proper Markdown. If the data is insufficient, say so "
                "explicitly and explain what's missing."
            ),
        })
        response = client.chat.completions.create(model=use_model, messages=messages)
        content = response.choices[0].message.content or ""
        logger.info(
            "chat.done id=%s reason=rounds_exhausted total_dt=%.2fs bytes=%d",
            rid, time.time() - t_start, len(content),
        )
        return content

    def chat_stream(
        self,
        message: str,
        history: list[dict] | None = None,
        context_node_id: str | None = None,
        model: str | None = None,
    ) -> Iterator[dict]:
        """Send a chat message and yield response events.

        Yields a stream of dicts. Two event kinds:
          {"type": "text", "content": "..."}        — final-answer token
          {"type": "step", "phase": "...", ...}     — pipeline trace event
                                                       (for the UI's "Show trace"
                                                       panel and for log debugging)

        Handles the tool-calling loop internally (non-streamed), then
        streams the final text response to the client.
        """
        messages = self._build_messages(message, history, context_node_id)
        client = self._get_client()
        use_model = model or self.active_model
        rid = uuid.uuid4().hex[:8]
        t_start = time.time()
        logger.info(
            "chat.request id=%s mode=stream provider=%s model=%s history=%d "
            "ctx=%s msg=%s",
            rid, self.active_provider, use_model, len(history or []),
            context_node_id, _preview(message, 300),
        )
        yield {
            "type": "step",
            "phase": "request",
            "rid": rid,
            "provider": self.active_provider,
            "model": use_model,
            "history_len": len(history or []),
            "context_node": context_node_id,
        }

        # Tool-calling loop (non-streamed so we can process tool calls)
        last_round_finish = None
        for round_idx in range(_MAX_TOOL_ROUNDS):
            t_round = time.time()
            try:
                response = client.chat.completions.create(
                    model=use_model, messages=messages, tools=TOOLS,
                )
            except Exception as e:
                logger.exception(
                    "chat.error id=%s round=%d phase=tools total_dt=%.2fs",
                    rid, round_idx, time.time() - t_start,
                )
                yield {
                    "type": "step",
                    "phase": "error",
                    "rid": rid,
                    "where": "tools",
                    "round": round_idx,
                    "message": str(e),
                }
                raise
            choice = response.choices[0]
            last_round_finish = choice.finish_reason
            tool_call_count = len(choice.message.tool_calls or []) if choice.message else 0
            logger.info(
                "chat.round id=%s round=%d finish=%s dt=%.2fs tool_calls=%d",
                rid, round_idx, choice.finish_reason, time.time() - t_round,
                tool_call_count,
            )
            yield {
                "type": "step",
                "phase": "round",
                "rid": rid,
                "round": round_idx,
                "finish": choice.finish_reason,
                "dt": round(time.time() - t_round, 3),
                "tool_calls": tool_call_count,
            }

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                # Check for `return_result` — terminate the loop and yield the
                # formatted answer directly instead of asking for another completion.
                for tc in choice.message.tool_calls:
                    if tc.function.name == "return_result":
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        rendered = self._format_return_result(args)
                        logger.info(
                            "chat.done id=%s reason=return_result total_dt=%.2fs bytes=%d",
                            rid, time.time() - t_start, len(rendered),
                        )
                        yield {
                            "type": "step",
                            "phase": "return_result",
                            "rid": rid,
                            "files": args.get("files") or [],
                            "node_refs": args.get("node_refs") or [],
                            "confidence": args.get("confidence") or "",
                            "total_dt": round(time.time() - t_start, 3),
                        }
                        yield {"type": "text", "content": rendered}
                        yield {
                            "type": "step",
                            "phase": "done",
                            "rid": rid,
                            "reason": "return_result",
                            "total_dt": round(time.time() - t_start, 3),
                            "bytes": len(rendered),
                        }
                        return

                messages.append(choice.message)
                for tc in choice.message.tool_calls:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    yield {
                        "type": "step",
                        "phase": "tool_call",
                        "rid": rid,
                        "name": tc.function.name,
                        "args_preview": _preview(json.dumps(args, default=str), 300),
                    }
                    t_tool = time.time()
                    result = self._exec_tool(tc.function.name, args)
                    payload, is_toon = _to_toon_for_llm(result)
                    if is_toon:
                        logger.info(
                            "chat.toon id=%s tool=%s json=%dB toon=%dB saved=%.1f%%",
                            rid, tc.function.name, len(result), len(payload),
                            100 * (1 - len(payload) / max(len(result), 1)),
                        )
                    yield {
                        "type": "step",
                        "phase": "tool_return",
                        "rid": rid,
                        "name": tc.function.name,
                        "bytes": len(result),
                        "dt": round(time.time() - t_tool, 3),
                        "preview": _preview(result, 240),
                        "toon_bytes": len(payload) if is_toon else None,
                        "toon_saved_pct": (
                            round(100 * (1 - len(payload) / max(len(result), 1)), 1)
                            if is_toon else None
                        ),
                    }
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": payload,
                    })
                continue

            # No tool calls — done with pre-processing, now stream the final answer
            break
        else:
            logger.warning(
                "chat.rounds_exhausted id=%s last_finish=%s — falling through to stream",
                rid, last_round_finish,
            )
            yield {
                "type": "step",
                "phase": "rounds_exhausted",
                "rid": rid,
                "last_finish": last_round_finish,
            }
            # Without an explicit nudge the model often emits 0 tokens here:
            # its prior turn ended with `tool_calls`, and now there are no
            # tools available, so it has nothing to "say". Append a user
            # message ordering it to summarize from what it already gathered.
            messages.append({
                "role": "user",
                "content": (
                    "You have used all available tool-call rounds. Do NOT request "
                    "more tools. Based ONLY on the information already gathered "
                    "above, write a complete final answer to my original question "
                    "in proper Markdown. If the data is insufficient, say so "
                    "explicitly and explain what's missing."
                ),
            })

        # Stream the final response
        logger.info(
            "chat.stream_begin id=%s elapsed=%.2fs", rid, time.time() - t_start,
        )
        yield {
            "type": "step",
            "phase": "stream_begin",
            "rid": rid,
            "elapsed": round(time.time() - t_start, 3),
        }
        token_count = 0
        byte_count = 0
        t_stream = time.time()
        try:
            stream = client.chat.completions.create(
                model=use_model, messages=messages, stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    token_count += 1
                    byte_count += len(delta.content)
                    yield {"type": "text", "content": delta.content}
        except Exception as e:
            logger.exception(
                "chat.error id=%s phase=stream tokens=%d bytes=%d total_dt=%.2fs",
                rid, token_count, byte_count, time.time() - t_start,
            )
            yield {
                "type": "step",
                "phase": "error",
                "rid": rid,
                "where": "stream",
                "tokens": token_count,
                "bytes": byte_count,
                "message": str(e),
            }
            raise
        logger.info(
            "chat.done id=%s reason=stream tokens=%d bytes=%d stream_dt=%.2fs total_dt=%.2fs",
            rid, token_count, byte_count,
            time.time() - t_stream, time.time() - t_start,
        )
        yield {
            "type": "step",
            "phase": "done",
            "rid": rid,
            "reason": "stream",
            "tokens": token_count,
            "bytes": byte_count,
            "stream_dt": round(time.time() - t_stream, 3),
            "total_dt": round(time.time() - t_start, 3),
        }

    def generate_image(self, prompt: str, model: str = "grok-imagine-image", n: int = 1, size: str = "1024x1024") -> list[str]:
        """Generate image(s) using Grok's image API. Returns list of base64-encoded images."""
        client = self._get_client()
        response = client.images.generate(
            model=model,
            prompt=prompt,
            n=n,
            response_format="b64_json",
        )
        return [img.b64_json for img in response.data]

    def _build_messages(
        self,
        message: str,
        history: list[dict] | None,
        context_node_id: str | None,
    ) -> list[dict]:
        """Build the messages list with system prompt, optional context hint, and history."""
        system_content = SYSTEM_PROMPT

        # If a node is selected in the graph, mention it so Grok can use get_node
        if context_node_id:
            system_content += f"\n\nThe user currently has node '{context_node_id}' selected in the graph. Use get_node to look it up if relevant."

        messages = [{"role": "system", "content": system_content}]

        if history:
            for entry in history:
                messages.append({
                    "role": entry.get("role", "user"),
                    "content": entry.get("content", ""),
                })

        messages.append({"role": "user", "content": message})
        return messages
