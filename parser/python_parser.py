"""
Python AST parser — extracts functions, classes, imports, and call sites
from Python source files.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Optional

from .base import BaseParser

_COMMENT_TAG_RE = re.compile(
    r"#\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
)

_SQL_KEYWORDS_RE = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE)\b", re.IGNORECASE
)

_RE_FUNC_NAMES = frozenset({"re.compile", "re.match", "re.search", "re.findall", "re.sub"})


class PythonParser(BaseParser):
    """Parses a Python file and extracts code entities and relationships."""

    def can_parse(self, filepath: str) -> bool:
        """Return True if the file has a .py extension."""
        return Path(filepath).suffix == ".py"

    def parse_file(self, filepath: str) -> dict | None:
        """Parse a single Python file and return extracted entities."""
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse from an already-read source string."""
        try:
            tree = ast.parse(source, filename=filepath)
        except (SyntaxError, UnicodeDecodeError):
            return None

        source_lines = source.splitlines()
        parent_map = self._build_parent_map(tree)
        imports = self._extract_imports(tree)
        functions = self._extract_functions(tree, source_lines, parent_map)
        classes = self._extract_classes(tree, source_lines)

        return {
            "file": filepath,
            "module_docstring": ast.get_docstring(tree),
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "variables": self._extract_variables(tree),
            "comments": self._extract_comments(source_lines),
            "type_checking_imports": self._extract_type_checking_imports(tree),
            "strings": self._extract_strings(tree),
            "patterns": self._detect_patterns(imports, functions, classes),
        }

    # ------------------------------------------------------------------
    # Parent map for nesting detection
    # ------------------------------------------------------------------

    def _build_parent_map(self, tree: ast.Module) -> dict[int, ast.AST]:
        """Build a mapping from node id to parent node."""
        parent_map: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent_map[id(child)] = node
        return parent_map

    # ------------------------------------------------------------------
    # Functions (top-level and nested only — methods handled by classes)
    # ------------------------------------------------------------------

    def _extract_functions(
        self, tree: ast.Module, source_lines: list[str], parent_map: dict[int, ast.AST]
    ) -> list[dict]:
        """Extract top-level and nested function definitions (not methods)."""
        functions = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            parent = parent_map.get(id(node))
            if isinstance(parent, ast.ClassDef):
                continue

            is_nested = isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            )

            functions.append(self._extract_callable(node, source_lines, is_nested=is_nested))
        return functions

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _extract_classes(self, tree: ast.Module, source_lines: list[str]) -> list[dict]:
        """Extract class definitions with their bases and methods."""
        classes = []
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            line_start = node.lineno
            line_end = node.end_lineno or node.lineno
            class_source = "\n".join(source_lines[line_start - 1 : line_end])

            methods = []
            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(self._extract_callable(item, source_lines, is_nested=False))

            decorator_names = [self._decorator_name(d) for d in node.decorator_list]
            base_names = [self._node_name(b) for b in node.bases]

            is_dataclass = "dataclass" in decorator_names
            is_namedtuple = any(
                b in ("NamedTuple", "typing.NamedTuple") for b in base_names if b
            )

            classes.append({
                "name": node.name,
                "line_start": line_start,
                "line_end": line_end,
                "source": class_source,
                "docstring": ast.get_docstring(node),
                "bases": base_names,
                "methods": methods,
                "decorators": decorator_names,
                "class_vars": self._extract_class_vars(node),
                "is_dataclass": is_dataclass,
                "is_namedtuple": is_namedtuple,
            })
        return classes

    # ------------------------------------------------------------------
    # Unified callable extraction (functions + methods)
    # ------------------------------------------------------------------

    def _extract_callable(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        source_lines: list[str],
        *,
        is_nested: bool = False,
    ) -> dict:
        """Extract all metadata from a function/method in a single subtree walk."""
        line_start = node.lineno
        line_end = node.end_lineno or node.lineno
        func_source = "\n".join(source_lines[line_start - 1 : line_end])

        params = self._extract_params(node.args)
        param_names = [p["name"] for p in params]

        calls, complexity, managers, exceptions = self._analyze_callable(node)

        return {
            "name": node.name,
            "line_start": line_start,
            "line_end": line_end,
            "loc": line_end - line_start + 1,
            "source": func_source,
            "docstring": ast.get_docstring(node),
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "is_nested": is_nested,
            "is_test": node.name.startswith("test_") or node.name.startswith("test"),
            "calls": calls,
            "args": [arg.arg for arg in node.args.args],
            "params": params,
            "return_annotation": self._safe_unparse(node.returns),
            "decorators": [self._decorator_name(d) for d in node.decorator_list],
            "signature_hash": hashlib.md5(
                f"{node.name}({','.join(param_names)})".encode()
            ).hexdigest(),
            "complexity": complexity,
            "context_managers": managers,
            "exceptions": exceptions,
        }

    def _analyze_callable(
        self, func_node: ast.AST
    ) -> tuple[list[dict], int, list[str], list[str]]:
        """Single-pass analysis: calls, complexity, context managers, exceptions."""
        calls: list[dict] = []
        complexity = 1
        managers: list[str] = []
        exceptions: list[str] = []
        seen_exceptions: set[str] = set()

        for child in ast.walk(func_node):
            # -- Calls --
            if isinstance(child, ast.Call):
                name = self._call_name(child)
                if name:
                    call_args = []
                    for a in child.args:
                        unparsed = self._safe_unparse(a)
                        if unparsed is not None:
                            call_args.append(unparsed)
                    for kw in child.keywords:
                        val = self._safe_unparse(kw.value)
                        if kw.arg and val is not None:
                            call_args.append(f"{kw.arg}={val}")
                        elif val is not None:
                            call_args.append(f"**{val}")
                    calls.append({
                        "name": name,
                        "args": call_args,
                        "line": getattr(child, "lineno", None),
                    })

            # -- Complexity --
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                  ast.ExceptHandler, ast.Assert,
                                  ast.With, ast.AsyncWith)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1

            # -- Context managers --
            if isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    expr = self._safe_unparse(item.context_expr)
                    if expr:
                        managers.append(expr)

            # -- Exception handlers --
            if isinstance(child, ast.ExceptHandler) and child.type is not None:
                name = self._safe_unparse(child.type)
                if name and name not in seen_exceptions:
                    seen_exceptions.add(name)
                    exceptions.append(name)

        return calls, complexity, managers, exceptions

    def _extract_class_vars(self, class_node: ast.ClassDef) -> list[dict]:
        """Extract class-level variable assignments (direct children only)."""
        class_vars: list[dict] = []
        for item in class_node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        class_vars.append({
                            "name": target.id,
                            "line": item.lineno,
                            "annotation": None,
                            "value": self._safe_unparse(item.value),
                        })
            elif isinstance(item, ast.AnnAssign) and isinstance(
                item.target, ast.Name
            ):
                class_vars.append({
                    "name": item.target.id,
                    "line": item.lineno,
                    "annotation": self._safe_unparse(item.annotation),
                    "value": self._safe_unparse(item.value),
                })
        return class_vars

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_imports(self, tree: ast.Module) -> list[dict]:
        """Extract import and from-import statements."""
        imports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "module": alias.name,
                        "names": [],
                        "alias": alias.asname,
                        "line": node.lineno,
                        "level": 0,
                    })
            elif isinstance(node, ast.ImportFrom):
                imports.append({
                    "module": node.module or "",
                    "names": [a.name for a in node.names],
                    "alias": None,
                    "line": node.lineno,
                    "level": node.level or 0,
                })
        return imports

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    def _extract_variables(self, tree: ast.Module) -> list[dict]:
        """Extract top-level variable assignments."""
        variables = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        value = self._extract_variable_value(target.id, node.value)
                        variables.append({
                            "name": target.id,
                            "line": node.lineno,
                            "value": value,
                        })
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                value = self._extract_variable_value(
                    node.target.id, node.value
                )
                variables.append({
                    "name": node.target.id,
                    "line": node.lineno,
                    "value": value,
                })
        return variables

    def _extract_variable_value(
        self, name: str, value_node: ast.AST | None
    ) -> str | None:
        """Extract a string representation for special variables."""
        if value_node is None:
            return None
        if name == "__all__":
            try:
                return repr(ast.literal_eval(value_node))
            except (ValueError, TypeError):
                return self._safe_unparse(value_node)
        if name == "__version__":
            try:
                val = ast.literal_eval(value_node)
                if isinstance(val, str):
                    return val
            except (ValueError, TypeError):
                pass
            return self._safe_unparse(value_node)
        return None

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def _extract_params(self, args_node: ast.arguments) -> list[dict]:
        """Extract rich parameter info from a function's arguments node."""
        params = []
        num_args = len(args_node.args)
        num_defaults = len(args_node.defaults)
        num_no_default = num_args - num_defaults

        for i, arg in enumerate(args_node.args):
            default = None
            if i >= num_no_default:
                default = self._safe_unparse(args_node.defaults[i - num_no_default])
            params.append({
                "name": arg.arg,
                "default": default,
                "annotation": self._safe_unparse(arg.annotation),
                "kind": "arg",
            })

        if args_node.vararg:
            params.append({
                "name": args_node.vararg.arg,
                "default": None,
                "annotation": self._safe_unparse(args_node.vararg.annotation),
                "kind": "vararg",
            })

        for i, arg in enumerate(args_node.kwonlyargs):
            default = self._safe_unparse(args_node.kw_defaults[i])
            params.append({
                "name": arg.arg,
                "default": default,
                "annotation": self._safe_unparse(arg.annotation),
                "kind": "kwonly",
            })

        if args_node.kwarg:
            params.append({
                "name": args_node.kwarg.arg,
                "default": None,
                "annotation": self._safe_unparse(args_node.kwarg.annotation),
                "kind": "kwarg",
            })

        return params

    # ------------------------------------------------------------------
    # Comments (TODO / FIXME / NOTE / HACK / XXX)
    # ------------------------------------------------------------------

    def _extract_comments(self, source_lines: list[str]) -> list[dict]:
        """Scan raw source lines for tagged comments."""
        comments: list[dict] = []
        for lineno, line in enumerate(source_lines, start=1):
            m = _COMMENT_TAG_RE.search(line)
            if m:
                comments.append({
                    "tag": m.group(1).upper(),
                    "text": m.group(2).strip(),
                    "line": lineno,
                })
        return comments

    # ------------------------------------------------------------------
    # TYPE_CHECKING imports
    # ------------------------------------------------------------------

    def _extract_type_checking_imports(self, tree: ast.Module) -> list[dict]:
        """Extract imports inside `if TYPE_CHECKING:` blocks."""
        imports: list[dict] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            is_tc = (
                (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
                or (
                    isinstance(test, ast.Attribute)
                    and test.attr == "TYPE_CHECKING"
                )
            )
            if not is_tc:
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        imports.append({
                            "module": alias.name,
                            "names": [],
                            "alias": alias.asname,
                            "line": child.lineno,
                            "level": 0,
                        })
                elif isinstance(child, ast.ImportFrom):
                    imports.append({
                        "module": child.module or "",
                        "names": [a.name for a in child.names],
                        "alias": None,
                        "line": child.lineno,
                        "level": child.level or 0,
                    })
        return imports

    # ------------------------------------------------------------------
    # Strings (SQL, URLs, regex patterns)
    # ------------------------------------------------------------------

    def _extract_strings(self, tree: ast.Module) -> list[dict]:
        """Extract notable string patterns from the AST."""
        strings: list[dict] = []
        seen: set[tuple[str, int]] = set()

        for node in ast.walk(tree):
            # Regex patterns passed to re.compile / re.match / etc.
            if isinstance(node, ast.Call):
                call_name = self._call_name(node)
                if call_name in _RE_FUNC_NAMES and node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(
                        first_arg.value, str
                    ):
                        key = (first_arg.value, first_arg.lineno)
                        if key not in seen:
                            seen.add(key)
                            strings.append({
                                "kind": "regex",
                                "value": first_arg.value,
                                "line": first_arg.lineno,
                            })

            if not isinstance(node, ast.Constant) or not isinstance(
                node.value, str
            ):
                continue

            val = node.value
            lineno = getattr(node, "lineno", 0)
            key = (val, lineno)
            if key in seen:
                continue

            if _SQL_KEYWORDS_RE.search(val):
                seen.add(key)
                strings.append({"kind": "sql", "value": val, "line": lineno})
            elif val.startswith("/") and "/" in val[1:] or "://" in val:
                seen.add(key)
                strings.append({"kind": "url", "value": val, "line": lineno})

        return strings

    # ------------------------------------------------------------------
    # Pattern / library detection
    # ------------------------------------------------------------------

    _KNOWN_PATTERNS: dict[str, list[str]] = {
        "fastapi": ["fastapi"],
        "django": ["django"],
        "flask": ["flask"],
        "sqlalchemy": ["sqlalchemy"],
        "pydantic": ["pydantic"],
        "celery": ["celery"],
        "pytest": ["pytest"],
    }

    def _detect_patterns(
        self,
        imports: list[dict],
        functions: list[dict],
        classes: list[dict],
    ) -> list[str]:
        """Detect framework/library patterns based on imports and code."""
        import_modules = set()
        for imp in imports:
            mod = imp.get("module", "")
            if mod:
                import_modules.add(mod.split(".")[0])

        patterns: list[str] = []
        for pattern, prefixes in self._KNOWN_PATTERNS.items():
            if any(p in import_modules for p in prefixes):
                patterns.append(pattern)

        if "pytest" not in patterns:
            has_tests = any(f.get("is_test") for f in functions)
            if has_tests:
                patterns.append("pytest")

        return patterns

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_unparse(self, node: ast.AST | None) -> str | None:
        """Safely unparse an AST node to its string representation."""
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return None

    def _call_name(self, node: ast.Call) -> str | None:
        """Get the name of a function call."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            value_name = self._node_name(node.func.value)
            if value_name:
                return f"{value_name}.{node.func.attr}"
            return node.func.attr
        return None

    def _node_name(self, node: ast.AST) -> str | None:
        """Get a string name from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            value = self._node_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        return None

    def _decorator_name(self, node: ast.AST) -> str:
        """Get the name of a decorator."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._node_name(node) or ""
        if isinstance(node, ast.Call):
            return self._node_name(node.func) or ""
        return ""
