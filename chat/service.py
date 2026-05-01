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
import re
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


# Boilerplate request payload (system prompt + tool catalog) is loaded from
# `ai/chat_request.json` so it can be tuned without touching Python code.
# The file holds the *static* parts of every chat completion request — the
# model, conversation history, and user message are layered on at call time.
#
# Versioning convention (matches the `_comment` field inside the JSON
# files themselves):
#   chat_request.json    — ACTIVE payload (always edit this one)
#   chat_request_v1.json — original snapshot, pre-tuning
#   chat_request_v2.json — snapshot after PLAN_MORE_LOCAL_AI_FUNCTIONS
#                           phases 1-4 (14 new internal functions)
#   chat_request_v3.json — pre-Phase-8 snapshot
#
# Historical note: an earlier revision of this loader pointed at
# `chat_request_v2.json` as the ACTIVE file, which silently froze the
# catalog at the v2 snapshot and hid the three Phase-8 tools
# (`outline_file`, `list_declarations`, `find_symbol_usages`) from the
# LLM even after they were added. That's why the §8.13 benchmark trace
# kept showing `project_search` calls with kitchen-sink regexes — the
# replacement tools were never in the request payload. Fixed: the
# loader now follows the comment in the JSON file itself and treats
# `chat_request.json` as authoritative.
_AI_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ai",
)
_REQUEST_TEMPLATE_PATH = os.path.join(_AI_DIR, "chat_request.json")
# Fallback to the most recent snapshot if chat_request.json was deleted
# locally — keeps tests / fresh checkouts working.
if not os.path.exists(_REQUEST_TEMPLATE_PATH):
    _REQUEST_TEMPLATE_PATH = os.path.join(_AI_DIR, "chat_request_v3.json")


def _load_request_template() -> dict:
    with open(_REQUEST_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_REQUEST_TEMPLATE = _load_request_template()


def _extract_system_prompt(template: dict) -> str:
    for msg in template.get("messages", []):
        if msg.get("role") == "system":
            return msg.get("content", "")
    return ""


SYSTEM_PROMPT = _extract_system_prompt(_REQUEST_TEMPLATE)

TOOLS = _REQUEST_TEMPLATE.get("tools", [])

# ── Context-aware tool-list filtering (Phase 8 §8.13 follow-up) ─────────
#
# When the user names a specific file, the model still defaults to
# `file_search` / `project_search` with a kitchen-sink regex even after
# every prompt-only tightening in PLAN_MORE_LOCAL_AI_FUNCTIONS.md §8.13.
# The only lever left is to remove those tools from the catalog for
# file-named requests so the model is forced to pick `outline_file` /
# `list_declarations` / `find_symbol_usages`.
#
# This is purely the LLM-tools view; the underlying HTTP endpoints
# (`/api/file/search`, `/api/project/search`) and `_exec_tool` dispatch
# are unchanged so existing HTTP consumers and other tool callers still
# work. The OpenAPI document marks both endpoints as deprecated for the
# file-named-question pathway and points integrators at the three
# replacement endpoints.
_GREP_TOOL_NAMES = ("file_search", "project_search")

# A "file-named question" is one where the user message contains a token
# that looks like a filename — either a path with a slash, or a bare
# `name.ext` where `ext` is a known source/markup extension. Backticks
# and quotes are common framing; the regex doesn't require them but the
# extension allow-list keeps us from firing on prose like "see fig. 1".
_FILE_EXT_ALLOWLIST = (
    "py|js|ts|jsx|tsx|html|htm|css|scss|md|markdown|json|yaml|yml|toml|"
    "xml|svg|vue|svelte|go|rs|java|kt|swift|c|cc|cpp|cxx|h|hpp|hh|m|mm|"
    "sh|bash|zsh|sql|rb|php|lua|pl|r|scala|dart|ex|exs|erl|elm|fs|fsx|"
    "ipynb|cfg|ini|conf|env|lock|txt|csv|tsv|proto|graphql|gql"
)
_FILE_NAMED_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:[\w./\\-]+/)?[\w.\-]+\.(?:" + _FILE_EXT_ALLOWLIST + r")\b",
    re.IGNORECASE,
)


def _is_file_named_question(message: str) -> bool:
    """True when the user message names a specific file by path or `name.ext`.

    Used to drop `file_search` / `project_search` from the tool catalog
    so the model is forced toward `outline_file` / `list_declarations` /
    `find_symbol_usages` (see PLAN_MORE_LOCAL_AI_FUNCTIONS.md §8.13).
    """
    if not message:
        return False
    return _FILE_NAMED_RE.search(message) is not None


def _select_tools(message: str) -> list[dict]:
    """Return the tools[] catalog filtered for the current question.

    For file-named questions the grep tools are removed so the model
    cannot fall back to a kitchen-sink regex as its first call.
    """
    if not _is_file_named_question(message):
        return TOOLS
    filtered = [
        t for t in TOOLS
        if (t.get("function") or {}).get("name") not in _GREP_TOOL_NAMES
    ]
    return filtered


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

    # ── Project root resolution ────────────────────────────────────

    def _current_root_dir(self) -> str | None:
        """Return the root directory of the currently-open project.

        Resolution order:

        1. ``ProjectManager.root_dir`` if a project is open. This is the
           live value that follows the user's "Open Folder" actions, so
           tools always target the project they're looking at.
        2. ``self.root_dir`` — the value cached at construction time
           (e.g. from the server's ``--watch-dir`` flag). Used when no
           project is open through the manager.

        Without this fallback, opening a project via the UI never
        propagated to ``ChatService`` and ``project_search`` aborted
        with ``"No root configured; pass root explicitly."``.
        """
        pm = self._project_manager
        if pm is not None:
            try:
                pm_root = getattr(pm, "root_dir", None)
                if pm_root:
                    return str(pm_root)
            except Exception:
                pass
        return self.root_dir

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

        elif name in ("file_stats", "get_file_section", "get_function_source",
                      "file_search", "project_search",
                      "list_declarations", "find_symbol_usages", "outline_file"):
            from apollo import file_inspect
            # Prefer the live ProjectManager root over the cached
            # ``self.root_dir`` so file tools always target the
            # currently-open project. Without this fallback,
            # ``project_search`` failed with "No root configured" whenever
            # the chat service was constructed before a project was
            # opened (e.g. server launched without --watch-dir, then user
            # opens a folder via the UI).
            root = self._current_root_dir()
            try:
                if name == "file_stats":
                    return json.dumps(file_inspect.file_stats(self.graph, root, args["path"]), default=str)
                if name == "get_file_section":
                    return json.dumps(file_inspect.get_file_section(
                        self.graph, root,
                        args["path"], int(args["start_line"]), int(args["end_line"]),
                        expected_md5=args.get("expected_md5"),
                    ), default=str)
                if name == "get_function_source":
                    return json.dumps(file_inspect.get_function_source(
                        self.graph, root,
                        args["path"], args["name"],
                        expected_md5=args.get("expected_md5"),
                    ), default=str)
                if name == "file_search":
                    return json.dumps(file_inspect.file_search(
                        self.graph, root,
                        args["path"], args["pattern"],
                        context=int(args.get("context", 5) or 5),
                        regex=bool(args.get("regex", True)),
                        expected_md5=args.get("expected_md5"),
                    ), default=str)
                if name == "project_search":
                    return json.dumps(file_inspect.project_search(
                        self.graph, root,
                        args["pattern"],
                        root=args.get("root"),
                        context=int(args.get("context", 5) or 5),
                        file_glob=args.get("file_glob", "*.py") or "*.py",
                        regex=bool(args.get("regex", True)),
                    ), default=str)
                if name == "list_declarations":
                    from apollo.chat import local_tools
                    return json.dumps(local_tools.list_declarations(
                        self.graph, root,
                        args["path"],
                        kinds=args.get("kinds"),
                        limit=int(args.get("limit", 200) or 200),
                    ), default=str)
                if name == "find_symbol_usages":
                    from apollo.chat import local_tools
                    return json.dumps(local_tools.find_symbol_usages(
                        self.graph, root,
                        args["path"],
                        symbol=args.get("symbol"),
                        symbols=args.get("symbols"),
                        kinds=args.get("kinds"),
                    ), default=str)
                if name == "outline_file":
                    from apollo.chat import local_tools
                    return json.dumps(local_tools.outline_file(
                        self.graph, root,
                        args["path"],
                        depth=int(args.get("depth", 2) or 2),
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

        elif name == "batch_get_nodes":
            from apollo.chat import local_tools
            ids = args.get("node_ids") or []
            include_source = bool(args.get("include_source", True))
            include_edges = bool(args.get("include_edges", True))
            return json.dumps(local_tools.batch_get_nodes(
                self.graph, ids,
                include_source=include_source,
                include_edges=include_edges,
            ), default=str)

        elif name == "batch_file_sections":
            from apollo.chat import local_tools
            ranges = args.get("ranges") or []
            return json.dumps(local_tools.batch_file_sections(
                self.graph, self.root_dir, ranges,
            ), default=str)

        elif name == "get_directory_tree":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_directory_tree(
                self.graph,
                root=args.get("root", ".") or ".",
                depth=int(args.get("depth", 3) or 3),
                glob=args.get("glob") or None,
                include_dirs=bool(args.get("include_dirs", True)),
            ), default=str)

        elif name == "project_stats_detailed":
            from apollo.chat import local_tools
            return json.dumps(local_tools.project_stats_detailed(
                self.graph,
                top_n=int(args.get("top_n", 20) or 20),
                group=args.get("group", "dir") or "dir",
            ), default=str)

        elif name == "get_paths_between":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_paths_between(
                self.graph,
                start_node_id=args.get("start_node_id", ""),
                end_node_id=args.get("end_node_id", ""),
                max_length=int(args.get("max_length", 5) or 5),
                max_paths=int(args.get("max_paths", 5) or 5),
                edge_types=args.get("edge_types"),
                shortest_only=bool(args.get("shortest_only", False)),
            ), default=str)

        elif name == "get_subgraph":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_subgraph(
                self.graph,
                seed_node_ids=args.get("seed_node_ids") or [],
                depth=int(args.get("depth", 1) or 1),
                edge_types=args.get("edge_types"),
                max_nodes=int(args.get("max_nodes", 200) or 200),
            ), default=str)

        elif name == "get_inheritance_tree":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_inheritance_tree(
                self.graph,
                class_node_id=args.get("class_node_id", ""),
                include_methods=bool(args.get("include_methods", False)),
            ), default=str)

        elif name == "get_transitive_imports":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_transitive_imports(
                self.graph,
                file_node_id=args.get("file_node_id", ""),
                direction=args.get("direction", "in") or "in",
                max_depth=int(args.get("max_depth", 5) or 5),
            ), default=str)

        elif name == "get_code_metrics":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_code_metrics(
                self.graph,
                node_ids=args.get("node_ids"),
                top_n=int(args.get("top_n", 20) or 20),
                sort_by=args.get("sort_by", "complexity") or "complexity",
            ), default=str)

        elif name == "search_graph_by_signature":
            from apollo.chat import local_tools
            return json.dumps(local_tools.search_graph_by_signature(
                self.graph,
                param_names=args.get("param_names"),
                param_annotations=args.get("param_annotations"),
                signature_hash=args.get("signature_hash"),
                fuzzy=bool(args.get("fuzzy", False)),
                top=int(args.get("top", 20) or 20),
            ), default=str)

        elif name == "find_test_correspondents":
            from apollo.chat import local_tools
            return json.dumps(local_tools.find_test_correspondents(
                self.graph,
                node_id=args.get("node_id", ""),
                include_heuristic=bool(args.get("include_heuristic", True)),
            ), default=str)

        elif name == "detect_entry_points":
            from apollo.chat import local_tools
            return json.dumps(local_tools.detect_entry_points(
                self.graph,
                kinds=args.get("kinds"),
            ), default=str)

        elif name == "get_git_context":
            from apollo.chat import local_tools
            return json.dumps(local_tools.get_git_context(
                self.graph, self.root_dir,
                path=args.get("path", ""),
                name=args.get("name"),
                line_start=args.get("line_start"),
                line_end=args.get("line_end"),
                limit=int(args.get("limit", 10) or 10),
            ), default=str)

        elif name == "search_notes_fulltext":
            from apollo.chat import local_tools
            mgr = self._get_annotation_manager()
            return json.dumps(local_tools.search_notes_fulltext(
                mgr,
                query=args.get("query", ""),
                type_filter=args.get("type"),
                top=int(args.get("top", 10) or 10),
            ), default=str)

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
        active_tools = _select_tools(message)
        logger.info(
            "chat.request id=%s mode=blocking provider=%s model=%s history=%d "
            "ctx=%s tools=%d file_named=%s msg=%s",
            rid, self.active_provider, use_model, len(history or []),
            context_node_id, len(active_tools),
            _is_file_named_question(message), _preview(message, 300),
        )

        for round_idx in range(_MAX_TOOL_ROUNDS):
            t_round = time.time()
            response = client.chat.completions.create(
                model=use_model, messages=messages, tools=active_tools,
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
        active_tools = _select_tools(message)
        file_named = _is_file_named_question(message)
        logger.info(
            "chat.request id=%s mode=stream provider=%s model=%s history=%d "
            "ctx=%s tools=%d file_named=%s msg=%s",
            rid, self.active_provider, use_model, len(history or []),
            context_node_id, len(active_tools), file_named,
            _preview(message, 300),
        )
        yield {
            "type": "step",
            "phase": "request",
            "rid": rid,
            "provider": self.active_provider,
            "model": use_model,
            "history_len": len(history or []),
            "context_node": context_node_id,
            "tools_count": len(active_tools),
            "file_named": file_named,
        }

        # Tool-calling loop (non-streamed so we can process tool calls)
        last_round_finish = None
        for round_idx in range(_MAX_TOOL_ROUNDS):
            t_round = time.time()
            try:
                response = client.chat.completions.create(
                    model=use_model, messages=messages, tools=active_tools,
                )
            except Exception as e:
                logger.exception(
                    "chat.error id=%s round=%d phase=tools total_dt=%.2fs",
                    rid, round_idx, time.time() - t_start,
                )
                # Classify so the UI can show a useful daisyUI toast instead
                # of dumping the raw provider stack-trace text on the user.
                err_cls = type(e).__name__
                provider_label = self.active_provider
                if err_cls in ("APIConnectionError", "APITimeoutError",
                               "ConnectError", "ConnectTimeout", "ReadTimeout"):
                    error_kind = "connection_error"
                    user_message = (
                        f"Cannot reach {provider_label} provider — "
                        "check your internet connection and try again."
                    )
                elif err_cls in ("AuthenticationError", "PermissionDeniedError"):
                    error_kind = "auth_error"
                    user_message = (
                        f"{provider_label} rejected the API key — "
                        "verify the credentials in your environment."
                    )
                elif err_cls == "RateLimitError":
                    error_kind = "rate_limit"
                    user_message = (
                        f"{provider_label} rate-limit hit — wait a moment and retry."
                    )
                else:
                    error_kind = "error"
                    user_message = f"Chat failed ({err_cls}): {e}"
                yield {
                    "type": "step",
                    "phase": "error",
                    "rid": rid,
                    "where": "tools",
                    "round": round_idx,
                    "message": str(e),
                    "error_kind": error_kind,
                    "user_message": user_message,
                }
                # Raise a clean message so the SSE `[ERROR]` frame stays readable.
                raise RuntimeError(user_message) from e
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
