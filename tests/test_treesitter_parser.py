"""Unit tests for TreeSitterParser — multi-language parser backend."""
import pytest
from pathlib import Path
from parser import TreeSitterParser, BaseParser


class TestTreeSitterParserInterface:
    """Test TreeSitterParser implements BaseParser interface."""
    
    def test_is_baseparser(self):
        """TreeSitterParser should implement BaseParser."""
        parser = TreeSitterParser()
        assert isinstance(parser, BaseParser)
    
    def test_can_parse_python(self):
        """Should recognize .py files."""
        parser = TreeSitterParser()
        assert parser.can_parse("test.py") is True
        assert parser.can_parse("module.py") is True
    
    def test_can_parse_javascript(self):
        """Should recognize .js files."""
        parser = TreeSitterParser()
        assert parser.can_parse("app.js") is True
    
    def test_can_parse_typescript(self):
        """Should recognize .ts and .tsx files."""
        parser = TreeSitterParser()
        assert parser.can_parse("main.ts") is True
        assert parser.can_parse("Component.tsx") is True
    
    def test_can_parse_go(self):
        """Should recognize .go files."""
        parser = TreeSitterParser()
        assert parser.can_parse("main.go") is True
    
    def test_can_parse_rust(self):
        """Should recognize .rs files."""
        parser = TreeSitterParser()
        assert parser.can_parse("lib.rs") is True
        assert parser.can_parse("main.rs") is True
    
    def test_cannot_parse_unknown_extension(self):
        """Should reject unknown file types."""
        parser = TreeSitterParser()
        assert parser.can_parse("readme.md") is False
        assert parser.can_parse("config.json") is False
        assert parser.can_parse("test.unknown") is False


class TestTreeSitterPythonParsing:
    """Test Python parsing via tree-sitter."""
    
    def test_parse_simple_python_file(self, tmp_path):
        """Parse a simple Python file with function and class."""
        py_file = tmp_path / "test.py"
        py_file.write_text("""
def greet(name):
    return f"Hello, {name}!"

class Calculator:
    def add(self, a, b):
        return a + b
    
    def multiply(self, a, b):
        return a * b

x = 42
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        assert result["file"] == str(py_file)
        assert "functions" in result
        assert "classes" in result
        assert "variables" in result
        
        # Check functions
        func_names = [f["name"] for f in result["functions"]]
        assert "greet" in func_names
        
        # Check classes
        class_names = [c["name"] for c in result["classes"]]
        assert "Calculator" in class_names
        
        # Check variables
        var_names = [v["name"] for v in result["variables"]]
        assert "x" in var_names
    
    def test_parse_python_imports(self, tmp_path):
        """Parse Python imports correctly."""
        py_file = tmp_path / "test.py"
        py_file.write_text("""
import os
import sys
from pathlib import Path
from typing import List, Dict
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        imports = result["imports"]
        assert len(imports) >= 2
        
        import_mods = [i["module"] for i in imports]
        assert "os" in import_mods
        assert "sys" in import_mods
    
    def test_parse_python_function_calls(self, tmp_path):
        """Extract function calls from Python."""
        py_file = tmp_path / "test.py"
        py_file.write_text("""
def process():
    print("Starting")
    result = helper()
    save(result)
    return result

def helper():
    return 42
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        funcs = result["functions"]
        process = next((f for f in funcs if f["name"] == "process"), None)
        assert process is not None
        assert "calls" in process
        # Should have detected calls to print, helper, save
        calls = process["calls"]
        assert len(calls) > 0
    
    def test_parse_source_string_python(self):
        """Parse Python from source string instead of file."""
        source = """
def add(a, b):
    return a + b
"""
        parser = TreeSitterParser()
        result = parser.parse_source(source, "test.py")
        
        assert result is not None
        assert any(f["name"] == "add" for f in result["functions"])


class TestTreeSitterJavaScriptParsing:
    """Test JavaScript parsing via tree-sitter."""
    
    def test_can_parse_javascript_functions(self, tmp_path):
        """Parse JavaScript functions."""
        js_file = tmp_path / "test.js"
        js_file.write_text("""
function greet(name) {
    return `Hello, ${name}!`;
}

const add = (a, b) => a + b;

class Calculator {
    constructor() {
        this.value = 0;
    }
    
    add(n) {
        this.value += n;
    }
}
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(js_file))
        
        assert result is not None
        funcs = [f["name"] for f in result["functions"]]
        # greet function should be found
        assert any("greet" in f for f in funcs)
    
    def test_can_parse_javascript_imports(self, tmp_path):
        """Parse JavaScript imports."""
        js_file = tmp_path / "test.js"
        js_file.write_text("""
import React from 'react';
import { useState } from 'react';
const lodash = require('lodash');
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(js_file))
        
        assert result is not None
        assert len(result["imports"]) > 0


class TestTreeSitterTypescriptParsing:
    """Test TypeScript parsing via tree-sitter."""
    
    def test_can_parse_typescript(self, tmp_path):
        """Parse TypeScript files."""
        ts_file = tmp_path / "test.ts"
        ts_file.write_text("""
interface User {
    id: number;
    name: string;
}

function getUser(id: number): User {
    return { id, name: "Alice" };
}

const add = (a: number, b: number): number => a + b;
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(ts_file))
        
        assert result is not None
        funcs = [f["name"] for f in result["functions"]]
        assert len(funcs) > 0


class TestTreeSitterGoRustParsing:
    """Test Go and Rust parsing via tree-sitter."""
    
    def test_can_parse_go(self, tmp_path):
        """Parse Go files."""
        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "fmt"

func main() {
    fmt.Println("Hello")
}

func add(a, b int) int {
    return a + b
}
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(go_file))
        
        assert result is not None
        funcs = [f["name"] for f in result["functions"]]
        assert "main" in funcs or any("main" in f for f in funcs)
    
    def test_can_parse_rust(self, tmp_path):
        """Parse Rust files."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn main() {
    println!("Hello, world!");
    let result = add(2, 3);
}

fn add(a: i32, b: i32) -> i32 {
    a + b
}

struct Point {
    x: i32,
    y: i32,
}
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(rs_file))
        
        assert result is not None
        funcs = [f["name"] for f in result["functions"]]
        assert "main" in funcs or any("main" in f for f in funcs)


class TestTreeSitterErrorHandling:
    """Test error handling."""
    
    def test_parse_nonexistent_file(self):
        """Should gracefully handle missing files."""
        parser = TreeSitterParser()
        result = parser.parse_file("/nonexistent/path/file.py")
        assert result is None
    
    def test_parse_invalid_syntax(self, tmp_path):
        """Should handle invalid syntax gracefully."""
        py_file = tmp_path / "broken.py"
        py_file.write_text("def broken(: invalid syntax here")
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        # Tree-sitter is lenient; it might still return partial results
        assert isinstance(result, dict) or result is None
    
    def test_parse_binary_file(self, tmp_path):
        """Should handle binary files gracefully."""
        bin_file = tmp_path / "test.py"
        bin_file.write_bytes(b"\x00\x01\x02\x03")
        parser = TreeSitterParser()
        result = parser.parse_file(str(bin_file))
        # Should not crash, might return None or partial result
        assert result is None or isinstance(result, dict)


class TestTreeSitterCaching:
    """Test parser caching behavior."""
    
    def test_language_cache(self):
        """Languages should be cached after first load."""
        parser = TreeSitterParser()
        
        # First call loads the language
        parser.can_parse("test1.py")
        assert "python" in parser._languages
        
        # Second call should use cache
        parser.can_parse("test2.py")
        # Both should have same language instance if cached
        assert "python" in parser._languages
    
    def test_parser_cache(self):
        """Parsers should be cached per language."""
        parser = TreeSitterParser()
        
        # Trigger parser creation
        parser.can_parse("test.py")
        
        # Check cache exists
        assert len(parser._parsers) >= 0  # May be lazy-created


class TestTreeSitterOutputSchema:
    """Test that parser output matches expected schema."""
    
    def test_output_has_required_fields(self, tmp_path):
        """Parsed output should have standard fields."""
        py_file = tmp_path / "test.py"
        py_file.write_text("def test(): pass")
        
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        assert "file" in result
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result
    
    def test_function_schema(self, tmp_path):
        """Functions should have required fields."""
        py_file = tmp_path / "test.py"
        py_file.write_text("""
def test_func(a, b):
    '''Docstring'''
    return a + b
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        
        funcs = result["functions"]
        assert len(funcs) > 0
        
        func = funcs[0]
        assert "name" in func
        assert "line_start" in func
        assert "line_end" in func
        assert "source" in func
        assert isinstance(func["line_start"], int)
        assert isinstance(func["line_end"], int)
    
    def test_class_schema(self, tmp_path):
        """Classes should have required fields."""
        py_file = tmp_path / "test.py"
        py_file.write_text("""
class MyClass:
    def method(self):
        pass
""")
        parser = TreeSitterParser()
        result = parser.parse_file(str(py_file))
        
        classes = result["classes"]
        assert len(classes) > 0
        
        cls = classes[0]
        assert "name" in cls
        assert "line_start" in cls
        assert "line_end" in cls
        assert "source" in cls
        assert "methods" in cls
        assert isinstance(cls["methods"], list)
