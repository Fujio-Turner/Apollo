"""
Tree-sitter multi-language parser — extracts functions, classes, imports,
and call sites from Python, JavaScript/TypeScript, Go, and Rust files.

Requires `tree-sitter` and the corresponding language grammar packages
(e.g., `tree-sitter-python`, `tree-sitter-javascript`). Falls back
gracefully when packages are not installed.
"""
from __future__ import annotations

from pathlib import Path

from .base import BaseParser

# Tree-sitter imports — optional dependency
try:
    from tree_sitter import Language, Parser

    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False


# ---------------------------------------------------------------------------
# Lazy grammar loaders — each returns a Language or None
# ---------------------------------------------------------------------------

def _load_python() -> "Language | None":
    try:
        import tree_sitter_python as tsp
        return Language(tsp.language())
    except Exception:
        return None


def _load_javascript() -> "Language | None":
    try:
        import tree_sitter_javascript as tsjs
        return Language(tsjs.language())
    except Exception:
        return None


def _load_typescript() -> "Language | None":
    try:
        import tree_sitter_typescript as tsts
        return Language(tsts.language_typescript())
    except Exception:
        return None


def _load_tsx() -> "Language | None":
    try:
        import tree_sitter_typescript as tsts
        return Language(tsts.language_tsx())
    except Exception:
        return None


def _load_go() -> "Language | None":
    try:
        import tree_sitter_go as tsgo
        return Language(tsgo.language())
    except Exception:
        return None


def _load_rust() -> "Language | None":
    try:
        import tree_sitter_rust as tsrs
        return Language(tsrs.language())
    except Exception:
        return None


# Maps file extension → (language key, loader function)
_EXT_MAP: dict[str, tuple[str, callable]] = {
    ".py": ("python", _load_python),
    ".js": ("javascript", _load_javascript),
    ".jsx": ("javascript", _load_javascript),
    ".ts": ("typescript", _load_typescript),
    ".tsx": ("tsx", _load_tsx),
    ".go": ("go", _load_go),
    ".rs": ("rust", _load_rust),
}


class TreeSitterParser(BaseParser):
    """Multi-language parser backed by tree-sitter."""

    def __init__(self) -> None:
        # lang_key → Language (cached after first load)
        self._languages: dict[str, Language | None] = {}
        # lang_key → Parser (cached)
        self._parsers: dict[str, "Parser"] = {}

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    def can_parse(self, filepath: str) -> bool:
        if not _HAS_TREE_SITTER:
            return False
        ext = Path(filepath).suffix.lower()
        if ext not in _EXT_MAP:
            return False
        lang_key, _ = _EXT_MAP[ext]
        return self._get_language(lang_key, ext) is not None

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        ext = filepath.suffix.lower()
        if ext not in _EXT_MAP:
            return None
        lang_key, _ = _EXT_MAP[ext]
        language = self._get_language(lang_key, ext)
        if language is None:
            return None

        try:
            source = filepath.read_bytes()
        except (OSError, IOError):
            return None

        return self._parse_bytes(source, lang_key, language, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        ext = Path(filepath).suffix.lower()
        if ext not in _EXT_MAP:
            return None
        lang_key, _ = _EXT_MAP[ext]
        language = self._get_language(lang_key, ext)
        if language is None:
            return None
        return self._parse_bytes(source.encode("utf-8", errors="replace"), lang_key, language, filepath)

    def _parse_bytes(self, source: bytes, lang_key: str, language, filepath: str) -> dict | None:
        parser = self._get_parser(lang_key, language)
        tree = parser.parse(source)
        if tree is None:
            return None

        extractor = _EXTRACTORS.get(lang_key)
        if extractor is None:
            return None
        return extractor(tree, source, filepath)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_language(self, lang_key: str, ext: str) -> "Language | None":
        if lang_key in self._languages:
            return self._languages[lang_key]
        _, loader = _EXT_MAP[ext]
        lang = loader()
        self._languages[lang_key] = lang
        return lang

    def _get_parser(self, lang_key: str, language: "Language") -> "Parser":
        if lang_key not in self._parsers:
            self._parsers[lang_key] = Parser(language)
        return self._parsers[lang_key]


# ======================================================================
# Extraction helpers — shared utilities
# ======================================================================

def _node_text(node, source: bytes) -> str:
    """Get the UTF-8 text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _node_source_lines(node, source: bytes) -> str:
    """Get the full source text for a node."""
    return _node_text(node, source)


def _children_of_type(node, *types: str):
    """Yield direct children matching any of the given types."""
    for child in node.children:
        if child.type in types:
            yield child


def _walk_descendants(node):
    """Yield all descendants of a node (depth-first)."""
    cursor = node.walk()
    visited = False
    while True:
        if not visited:
            yield cursor.node
        if not visited and cursor.goto_first_child():
            visited = False
            continue
        if cursor.goto_next_sibling():
            visited = False
            continue
        if cursor.goto_parent():
            visited = True
            continue
        break


def _extract_call_names(node, source: bytes) -> list[str]:
    """Extract function/method call names from all descendants of *node*."""
    calls: list[str] = []
    for desc in _walk_descendants(node):
        if desc.type == "call":
            # Python / JS / TS
            func = desc.child_by_field_name("function")
            if func is not None:
                calls.append(_node_text(func, source))
        elif desc.type == "call_expression":
            # Go / Rust
            func = desc.child_by_field_name("function")
            if func is not None:
                calls.append(_node_text(func, source))
        elif desc.type == "macro_invocation":
            # Rust macros like println!
            macro = desc.child_by_field_name("macro")
            if macro is not None:
                calls.append(_node_text(macro, source) + "!")
    return calls


# ======================================================================
# Python extraction
# ======================================================================

def _extract_python(tree, source: bytes, filepath: str) -> dict:
    root = tree.root_node
    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[dict] = []
    variables: list[dict] = []

    for node in root.children:
        if node.type in ("function_definition", "decorated_definition"):
            func = _extract_python_function(node, source)
            if func:
                functions.append(func)
        elif node.type == "class_definition":
            cls = _extract_python_class(node, source)
            if cls:
                classes.append(cls)
        elif node.type in ("import_statement", "import_from_statement"):
            imp = _extract_python_import(node, source)
            if imp:
                imports.extend(imp)
        elif node.type == "expression_statement":
            var = _extract_python_variable(node, source)
            if var:
                variables.extend(var)

    return {
        "file": filepath,
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "variables": variables,
    }


def _unwrap_decorated(node):
    """If node is a decorated_definition, return (decorators, inner_def). Else ([], node)."""
    if node.type == "decorated_definition":
        decorators = []
        definition = None
        for child in node.children:
            if child.type == "decorator":
                decorators.append(child)
            elif child.type in ("function_definition", "class_definition"):
                definition = child
        return decorators, definition
    return [], node


def _decorator_text(dec_node, source: bytes) -> str:
    """Get decorator name (without @)."""
    text = _node_text(dec_node, source)
    if text.startswith("@"):
        text = text[1:]
    # Strip arguments: @foo(bar) -> foo
    paren = text.find("(")
    if paren != -1:
        text = text[:paren]
    return text.strip()


def _extract_python_function(node, source: bytes) -> dict | None:
    decorators_nodes, func_node = _unwrap_decorated(node)
    if func_node is None or func_node.type != "function_definition":
        return None
    name_node = func_node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = func_node.child_by_field_name("parameters")
    args = _extract_python_params(params_node, source) if params_node else []
    body = func_node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    # Use the outer decorated node for line range if present
    outer = node if node.type == "decorated_definition" else func_node
    return {
        "name": _node_text(name_node, source),
        "line_start": outer.start_point[0] + 1,
        "line_end": outer.end_point[0] + 1,
        "source": _node_source_lines(outer, source),
        "calls": calls,
        "args": args,
        "decorators": [_decorator_text(d, source) for d in decorators_nodes],
    }


def _extract_python_params(params_node, source: bytes) -> list[str]:
    args = []
    for child in params_node.children:
        if child.type == "identifier":
            args.append(_node_text(child, source))
        elif child.type in ("default_parameter", "typed_parameter",
                            "typed_default_parameter"):
            name = child.child_by_field_name("name")
            if name:
                args.append(_node_text(name, source))
            elif child.children:
                # fallback to first identifier child
                for sub in child.children:
                    if sub.type == "identifier":
                        args.append(_node_text(sub, source))
                        break
        elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            for sub in child.children:
                if sub.type == "identifier":
                    args.append(_node_text(sub, source))
                    break
    return args


def _extract_python_class(node, source: bytes) -> dict | None:
    decorators_nodes, class_node = _unwrap_decorated(node)
    if class_node is None:
        # Might be a top-level decorated_definition wrapping a class
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "class_definition":
                    class_node = child
                    break
        if class_node is None:
            class_node = node
    if class_node.type != "class_definition":
        return None
    name_node = class_node.child_by_field_name("name")
    if name_node is None:
        return None

    # Base classes
    bases = []
    superclasses = class_node.child_by_field_name("superclasses")
    if superclasses:
        for child in superclasses.children:
            if child.type in ("identifier", "attribute"):
                bases.append(_node_text(child, source))

    # Methods
    methods = []
    body = class_node.child_by_field_name("body")
    if body:
        for item in body.children:
            if item.type in ("function_definition", "decorated_definition"):
                m = _extract_python_method(item, source)
                if m:
                    methods.append(m)

    outer = node if node.type == "decorated_definition" else class_node
    return {
        "name": _node_text(name_node, source),
        "line_start": outer.start_point[0] + 1,
        "line_end": outer.end_point[0] + 1,
        "source": _node_source_lines(outer, source),
        "bases": bases,
        "methods": methods,
        "decorators": [_decorator_text(d, source) for d in decorators_nodes],
    }


def _extract_python_method(node, source: bytes) -> dict | None:
    _, func_node = _unwrap_decorated(node)
    if func_node is None or func_node.type != "function_definition":
        return None
    name_node = func_node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = func_node.child_by_field_name("parameters")
    args = _extract_python_params(params_node, source) if params_node else []
    body = func_node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    return {
        "name": _node_text(name_node, source),
        "line_start": func_node.start_point[0] + 1,
        "line_end": func_node.end_point[0] + 1,
        "calls": calls,
        "args": args,
    }


def _extract_python_import(node, source: bytes) -> list[dict]:
    results = []
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                results.append({
                    "module": _node_text(child, source),
                    "names": [],
                    "alias": None,
                    "line": node.start_point[0] + 1,
                })
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    results.append({
                        "module": _node_text(name_node, source),
                        "names": [],
                        "alias": _node_text(alias_node, source) if alias_node else None,
                        "line": node.start_point[0] + 1,
                    })
    elif node.type == "import_from_statement":
        module = ""
        names = []
        for child in node.children:
            if child.type == "dotted_name":
                module = _node_text(child, source)
            elif child.type == "relative_import":
                module = _node_text(child, source)
            elif child.type in ("identifier",):
                # 'from x import y' — y appears as identifier
                names.append(_node_text(child, source))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    names.append(_node_text(name_node, source))
        results.append({
            "module": module,
            "names": names,
            "alias": None,
            "line": node.start_point[0] + 1,
        })
    return results


def _extract_python_variable(node, source: bytes) -> list[dict]:
    """Extract top-level variable assignments from expression_statement."""
    results = []
    for child in node.children:
        if child.type == "assignment":
            left = child.child_by_field_name("left")
            if left and left.type == "identifier":
                results.append({
                    "name": _node_text(left, source),
                    "line": child.start_point[0] + 1,
                })
        elif child.type == "augmented_assignment":
            left = child.child_by_field_name("left")
            if left and left.type == "identifier":
                results.append({
                    "name": _node_text(left, source),
                    "line": child.start_point[0] + 1,
                })
    return results


# ======================================================================
# JavaScript / TypeScript extraction
# ======================================================================

def _extract_js(tree, source: bytes, filepath: str) -> dict:
    """Extract from JavaScript or TypeScript files."""
    root = tree.root_node
    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[dict] = []
    variables: list[dict] = []

    for node in root.children:
        if node.type == "function_declaration":
            func = _extract_js_function(node, source)
            if func:
                functions.append(func)
        elif node.type == "class_declaration":
            cls = _extract_js_class(node, source)
            if cls:
                classes.append(cls)
        elif node.type == "import_statement":
            imp = _extract_js_import(node, source)
            if imp:
                imports.extend(imp)
        elif node.type in ("variable_declaration", "lexical_declaration"):
            extracted = _extract_js_variable_or_function(node, source)
            for item in extracted:
                if item.get("_is_func"):
                    del item["_is_func"]
                    functions.append(item)
                else:
                    variables.append(item)
        elif node.type == "export_statement":
            # Unwrap: export default function / export class / etc.
            for child in node.children:
                if child.type == "function_declaration":
                    func = _extract_js_function(child, source)
                    if func:
                        functions.append(func)
                elif child.type == "class_declaration":
                    cls = _extract_js_class(child, source)
                    if cls:
                        classes.append(cls)
                elif child.type in ("variable_declaration", "lexical_declaration"):
                    extracted = _extract_js_variable_or_function(child, source)
                    for item in extracted:
                        if item.get("_is_func"):
                            del item["_is_func"]
                            functions.append(item)
                        else:
                            variables.append(item)

    return {
        "file": filepath,
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "variables": variables,
    }


def _extract_js_function(node, source: bytes) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = node.child_by_field_name("parameters")
    args = _extract_js_params(params_node, source) if params_node else []
    body = node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    return {
        "name": _node_text(name_node, source),
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "source": _node_source_lines(node, source),
        "calls": calls,
        "args": args,
        "decorators": [],
    }


def _extract_js_params(params_node, source: bytes) -> list[str]:
    args = []
    for child in params_node.children:
        if child.type == "identifier":
            args.append(_node_text(child, source))
        elif child.type in ("required_parameter", "optional_parameter"):
            # TypeScript typed params
            pattern = child.child_by_field_name("pattern")
            if pattern and pattern.type == "identifier":
                args.append(_node_text(pattern, source))
        elif child.type == "assignment_pattern":
            left = child.child_by_field_name("left")
            if left and left.type == "identifier":
                args.append(_node_text(left, source))
        elif child.type == "rest_pattern":
            for sub in child.children:
                if sub.type == "identifier":
                    args.append(_node_text(sub, source))
                    break
    return args


def _extract_js_class(node, source: bytes) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    # Base / extends
    bases = []
    heritage = node.child_by_field_name("heritage")
    if heritage is None:
        # Try to find class_heritage child
        for child in node.children:
            if child.type == "class_heritage":
                heritage = child
                break
    if heritage:
        for child in heritage.children:
            if child.type in ("identifier", "member_expression"):
                bases.append(_node_text(child, source))

    body = node.child_by_field_name("body")
    methods = []
    if body:
        for item in body.children:
            if item.type in ("method_definition", "public_field_definition"):
                if item.type == "method_definition":
                    m = _extract_js_method(item, source)
                    if m:
                        methods.append(m)

    return {
        "name": _node_text(name_node, source),
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "source": _node_source_lines(node, source),
        "bases": bases,
        "methods": methods,
        "decorators": [],
    }


def _extract_js_method(node, source: bytes) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = node.child_by_field_name("parameters")
    args = _extract_js_params(params_node, source) if params_node else []
    body = node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    return {
        "name": _node_text(name_node, source),
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "calls": calls,
        "args": args,
    }


def _extract_js_import(node, source: bytes) -> list[dict]:
    """Extract JS/TS import statements."""
    results = []
    # import X from 'module'
    # import { X, Y } from 'module'
    # import * as X from 'module'
    source_node = node.child_by_field_name("source")
    if source_node is None:
        # Fallback: look for a string child
        for child in node.children:
            if child.type == "string":
                source_node = child
                break
    module = ""
    if source_node:
        module = _node_text(source_node, source).strip("'\"")

    names = []
    for child in node.children:
        if child.type == "import_clause":
            for sub in child.children:
                if sub.type == "identifier":
                    names.append(_node_text(sub, source))
                elif sub.type == "named_imports":
                    for spec in sub.children:
                        if spec.type == "import_specifier":
                            name_n = spec.child_by_field_name("name")
                            if name_n:
                                names.append(_node_text(name_n, source))
                elif sub.type == "namespace_import":
                    for ns_child in sub.children:
                        if ns_child.type == "identifier":
                            names.append(_node_text(ns_child, source))
                            break
        elif child.type == "identifier":
            names.append(_node_text(child, source))
        elif child.type == "named_imports":
            for spec in child.children:
                if spec.type == "import_specifier":
                    name_n = spec.child_by_field_name("name")
                    if name_n:
                        names.append(_node_text(name_n, source))

    results.append({
        "module": module,
        "names": names,
        "alias": None,
        "line": node.start_point[0] + 1,
    })
    return results


def _extract_js_variable_or_function(node, source: bytes) -> list[dict]:
    """Extract variable declarations. If the value is an arrow function or
    function expression, return it as a function instead."""
    results = []
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None:
                continue
            name = _node_text(name_node, source)
            # Check if value is an arrow/function expression
            if value_node and value_node.type in ("arrow_function", "function_expression", "function"):
                params_node = value_node.child_by_field_name("parameters")
                args = _extract_js_params(params_node, source) if params_node else []
                body = value_node.child_by_field_name("body")
                calls = _extract_call_names(body, source) if body else []
                results.append({
                    "name": name,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "source": _node_source_lines(node, source),
                    "calls": calls,
                    "args": args,
                    "decorators": [],
                    "_is_func": True,
                })
            else:
                results.append({
                    "name": name,
                    "line": node.start_point[0] + 1,
                })
    return results


# ======================================================================
# Go extraction
# ======================================================================

def _extract_go(tree, source: bytes, filepath: str) -> dict:
    root = tree.root_node
    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[dict] = []
    variables: list[dict] = []

    for node in root.children:
        if node.type == "function_declaration":
            func = _extract_go_function(node, source)
            if func:
                functions.append(func)
        elif node.type == "method_declaration":
            # Go methods (func (r Receiver) Name()) — treat as functions
            func = _extract_go_method_decl(node, source)
            if func:
                functions.append(func)
        elif node.type == "type_declaration":
            cls = _extract_go_type(node, source)
            if cls:
                classes.append(cls)
        elif node.type == "import_declaration":
            imp = _extract_go_import(node, source)
            if imp:
                imports.extend(imp)
        elif node.type in ("var_declaration", "const_declaration",
                           "short_var_declaration"):
            var = _extract_go_variable(node, source)
            if var:
                variables.extend(var)

    return {
        "file": filepath,
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "variables": variables,
    }


def _extract_go_function(node, source: bytes) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = node.child_by_field_name("parameters")
    args = _extract_go_params(params_node, source) if params_node else []
    body = node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    return {
        "name": _node_text(name_node, source),
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "source": _node_source_lines(node, source),
        "calls": calls,
        "args": args,
        "decorators": [],
    }


def _extract_go_method_decl(node, source: bytes) -> dict | None:
    """Extract a Go method declaration as a function."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = node.child_by_field_name("parameters")
    args = _extract_go_params(params_node, source) if params_node else []
    body = node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    # Get receiver type for context
    receiver = node.child_by_field_name("receiver")
    receiver_type = ""
    if receiver:
        for child in receiver.children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node:
                    receiver_type = _node_text(type_node, source).lstrip("*")
    name = _node_text(name_node, source)
    if receiver_type:
        name = f"{receiver_type}.{name}"
    return {
        "name": name,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "source": _node_source_lines(node, source),
        "calls": calls,
        "args": args,
        "decorators": [],
    }


def _extract_go_params(params_node, source: bytes) -> list[str]:
    args = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            name = child.child_by_field_name("name")
            if name:
                args.append(_node_text(name, source))
        elif child.type == "variadic_parameter_declaration":
            name = child.child_by_field_name("name")
            if name:
                args.append(_node_text(name, source))
    return args


def _extract_go_type(node, source: bytes) -> dict | None:
    """Extract Go type declarations (struct → class)."""
    for child in node.children:
        if child.type == "type_spec":
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if name_node is None:
                continue
            name = _node_text(name_node, source)
            is_struct = type_node and type_node.type == "struct_type"
            is_interface = type_node and type_node.type == "interface_type"
            if is_struct or is_interface:
                return {
                    "name": name,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "source": _node_source_lines(node, source),
                    "bases": [],
                    "methods": [],
                    "decorators": [],
                }
    return None


def _extract_go_import(node, source: bytes) -> list[dict]:
    results = []
    for desc in _walk_descendants(node):
        if desc.type == "import_spec":
            path_node = desc.child_by_field_name("path")
            name_node = desc.child_by_field_name("name")
            if path_node:
                module = _node_text(path_node, source).strip('"')
                alias = _node_text(name_node, source) if name_node else None
                results.append({
                    "module": module,
                    "names": [],
                    "alias": alias,
                    "line": desc.start_point[0] + 1,
                })
        elif desc.type == "interpreted_string_literal" and desc.parent and desc.parent.type == "import_declaration":
            # Single import: import "fmt"
            module = _node_text(desc, source).strip('"')
            results.append({
                "module": module,
                "names": [],
                "alias": None,
                "line": desc.start_point[0] + 1,
            })
    return results


def _extract_go_variable(node, source: bytes) -> list[dict]:
    results = []
    for child in _walk_descendants(node):
        if child.type == "var_spec":
            name_node = child.child_by_field_name("name")
            if name_node:
                results.append({
                    "name": _node_text(name_node, source),
                    "line": child.start_point[0] + 1,
                })
        elif child.type == "const_spec":
            name_node = child.child_by_field_name("name")
            if name_node:
                results.append({
                    "name": _node_text(name_node, source),
                    "line": child.start_point[0] + 1,
                })
    # short_var_declaration
    if node.type == "short_var_declaration":
        left = node.child_by_field_name("left")
        if left:
            for child in left.children:
                if child.type == "identifier":
                    results.append({
                        "name": _node_text(child, source),
                        "line": node.start_point[0] + 1,
                    })
    return results


# ======================================================================
# Rust extraction
# ======================================================================

def _extract_rust(tree, source: bytes, filepath: str) -> dict:
    root = tree.root_node
    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[dict] = []
    variables: list[dict] = []

    for node in root.children:
        if node.type == "function_item":
            func = _extract_rust_function(node, source)
            if func:
                functions.append(func)
        elif node.type in ("struct_item", "enum_item"):
            cls = _extract_rust_struct(node, source)
            if cls:
                classes.append(cls)
        elif node.type == "impl_item":
            # Extract methods from impl blocks
            methods = _extract_rust_impl_methods(node, source)
            functions.extend(methods)
        elif node.type == "use_declaration":
            imp = _extract_rust_use(node, source)
            if imp:
                imports.extend(imp)
        elif node.type in ("let_declaration", "static_item", "const_item"):
            var = _extract_rust_variable(node, source)
            if var:
                variables.append(var)

    return {
        "file": filepath,
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "variables": variables,
    }


def _extract_rust_function(node, source: bytes) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    params_node = node.child_by_field_name("parameters")
    args = _extract_rust_params(params_node, source) if params_node else []
    body = node.child_by_field_name("body")
    calls = _extract_call_names(body, source) if body else []
    # Attributes (like #[test])
    decorators = _extract_rust_attributes(node, source)
    return {
        "name": _node_text(name_node, source),
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "source": _node_source_lines(node, source),
        "calls": calls,
        "args": args,
        "decorators": decorators,
    }


def _extract_rust_params(params_node, source: bytes) -> list[str]:
    args = []
    for child in params_node.children:
        if child.type == "parameter":
            pattern = child.child_by_field_name("pattern")
            if pattern:
                args.append(_node_text(pattern, source))
        elif child.type == "self_parameter":
            args.append("self")
    return args


def _extract_rust_attributes(node, source: bytes) -> list[str]:
    """Extract #[...] attributes from the preceding siblings or children."""
    attrs = []
    # Check if parent has attribute_item children before this node
    if node.parent:
        for sibling in node.parent.children:
            if sibling == node:
                break
            if sibling.type == "attribute_item":
                text = _node_text(sibling, source)
                # Strip #[ and ]
                text = text.lstrip("#[").rstrip("]")
                attrs.append(text)
    return attrs


def _extract_rust_struct(node, source: bytes) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return {
        "name": _node_text(name_node, source),
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "source": _node_source_lines(node, source),
        "bases": [],
        "methods": [],
        "decorators": _extract_rust_attributes(node, source),
    }


def _extract_rust_impl_methods(node, source: bytes) -> list[dict]:
    """Extract methods from an impl block."""
    results = []
    type_node = node.child_by_field_name("type")
    type_name = _node_text(type_node, source) if type_node else ""
    body = node.child_by_field_name("body")
    if body:
        for child in body.children:
            if child.type == "function_item":
                func = _extract_rust_function(child, source)
                if func:
                    if type_name:
                        func["name"] = f"{type_name}::{func['name']}"
                    results.append(func)
    return results


def _extract_rust_use(node, source: bytes) -> list[dict]:
    """Extract Rust use declarations."""
    text = _node_text(node, source)
    # use std::collections::HashMap;
    # use crate::module::{Foo, Bar};
    # Simplify: treat the whole path as "module"
    # Strip "use " prefix and ";" suffix
    path = text
    if path.startswith("use "):
        path = path[4:]
    if path.endswith(";"):
        path = path[:-1]
    path = path.strip()

    # Check for braces: use foo::{A, B}
    names = []
    if "{" in path:
        base, rest = path.split("{", 1)
        base = base.rstrip(":")
        rest = rest.rstrip("}")
        names = [n.strip() for n in rest.split(",") if n.strip()]
        module = base
    else:
        module = path

    return [{
        "module": module,
        "names": names,
        "alias": None,
        "line": node.start_point[0] + 1,
    }]


def _extract_rust_variable(node, source: bytes) -> dict | None:
    if node.type == "let_declaration":
        pattern = node.child_by_field_name("pattern")
        if pattern and pattern.type == "identifier":
            return {
                "name": _node_text(pattern, source),
                "line": node.start_point[0] + 1,
            }
    elif node.type in ("static_item", "const_item"):
        name_node = node.child_by_field_name("name")
        if name_node:
            return {
                "name": _node_text(name_node, source),
                "line": node.start_point[0] + 1,
            }
    return None


# ======================================================================
# Extractor dispatch table
# ======================================================================

_EXTRACTORS = {
    "python": _extract_python,
    "javascript": _extract_js,
    "typescript": _extract_js,  # Same CST structure
    "tsx": _extract_js,
    "go": _extract_go,
    "rust": _extract_rust,
}
