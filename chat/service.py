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
import os
from typing import Iterator, Optional

import networkx as nx


SYSTEM_PROMPT = """You are a code and file exploration assistant embedded in Graph Search,
an app that indexes local codebases into a knowledge graph.

You have tools to query the user's indexed graph. Use them when the question is about their
files, code, functions, classes, imports, or project structure. For general questions
(greetings, explanations, opinions, non-code topics), just answer directly — no tool needed.

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
3. Call `return_result(summary, files, node_refs)` to finalize — do NOT just emit prose.
   - The `summary` field MUST itself be well-formatted Markdown (headings, bullets, code blocks).
   - Pass file paths in `files` and node IDs in `node_refs` — the UI renders them as clickable chips, so do NOT also list them inline in the summary.
   - Set `confidence` to "high", "med", or "low" so the UI can show a status dot.

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
            "description": "Get the most frequent entity names in the graph (for understanding what the codebase contains).",
            "parameters": {"type": "object", "properties": {}},
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

# Max tool-call rounds before forcing a text response
_MAX_TOOL_ROUNDS = 5


class ChatService:
    """Manages the chat pipeline: user question → Grok (with tools) → response."""

    def __init__(
        self,
        graph: nx.DiGraph,
        search=None,
        embedder=None,
        model: str = "grok-4-1-fast-non-reasoning",
        root_dir: str | None = None,
    ):
        self.graph = graph
        self.search = search
        self.embedder = embedder
        self.model = model
        self.root_dir = root_dir
        self._client = None
        self._query = None  # lazy GraphQuery

    @property
    def available(self) -> bool:
        return bool(os.environ.get("XAI_API_KEY"))

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            api_key = os.environ.get("XAI_API_KEY")
            if not api_key:
                raise RuntimeError("XAI_API_KEY environment variable is not set")
            self._client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        return self._client

    def _get_query(self):
        if self._query is None:
            from graph_search.graph.query import GraphQuery
            self._query = GraphQuery(self.graph)
        return self._query

    # ── Tool execution ─────────────────────────────────────────────

    def _exec_tool(self, name: str, args: dict) -> str:
        """Execute an internal tool and return JSON result."""
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
            from graph_search import file_inspect
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

        elif name == "get_wordcloud":
            from collections import defaultdict
            exclude = {"directory", "file", "import"}
            counts: dict[str, int] = defaultdict(int)
            for _, data in self.graph.nodes(data=True):
                if data.get("type", "") in exclude:
                    continue
                n = data.get("name", "")
                if n:
                    counts[n] += 1
            top_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:50]
            return json.dumps([{"name": n, "count": c} for n, c in top_items])

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
        use_model = model or self.model

        for _ in range(_MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=use_model, messages=messages, tools=TOOLS,
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                # Check for `return_result` — terminate immediately.
                for tc in choice.message.tool_calls:
                    if tc.function.name == "return_result":
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        return self._format_return_result(args)

                messages.append(choice.message)
                for tc in choice.message.tool_calls:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    result = self._exec_tool(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue

            return choice.message.content or ""

        # Exhausted rounds — force a response without tools
        response = client.chat.completions.create(model=use_model, messages=messages)
        return response.choices[0].message.content or ""

    def chat_stream(
        self,
        message: str,
        history: list[dict] | None = None,
        context_node_id: str | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        """Send a chat message and yield response tokens.

        Handles the tool-calling loop internally (non-streamed), then
        streams the final text response to the client.
        """
        messages = self._build_messages(message, history, context_node_id)
        client = self._get_client()
        use_model = model or self.model

        # Tool-calling loop (non-streamed so we can process tool calls)
        for _ in range(_MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=use_model, messages=messages, tools=TOOLS,
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                # Check for `return_result` — terminate the loop and yield the
                # formatted answer directly instead of asking for another completion.
                for tc in choice.message.tool_calls:
                    if tc.function.name == "return_result":
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        yield self._format_return_result(args)
                        return

                messages.append(choice.message)
                for tc in choice.message.tool_calls:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    result = self._exec_tool(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue

            # No tool calls — done with pre-processing, now stream the final answer
            break

        # Stream the final response
        stream = client.chat.completions.create(
            model=use_model, messages=messages, stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

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
