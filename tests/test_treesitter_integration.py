"""Integration tests for TreeSitterParser with GraphBuilder."""
import pytest
from pathlib import Path
from parser import TreeSitterParser
from graph.builder import GraphBuilder


class TestTreeSitterGraphBuilderIntegration:
    """Test TreeSitterParser integrated with GraphBuilder."""
    
    def test_build_graph_with_treesitter(self, tmp_path):
        """Build a graph using TreeSitterParser backend."""
        # Create a small project structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        # Create multiple language files
        py_file = src_dir / "math.py"
        py_file.write_text("""
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

class Calculator:
    def multiply(self, a, b):
        return a * b
""")
        
        js_file = src_dir / "utils.js"
        js_file.write_text("""
function format(value) {
    return value.toString();
}

const parse = (str) => parseInt(str);
""")
        
        go_file = src_dir / "main.go"
        go_file.write_text("""
package main

import "fmt"

func main() {
    fmt.Println("Hello")
}

func process(data string) string {
    return data
}
""")
        
        # Build graph with TreeSitterParser
        parsers = [TreeSitterParser()]  # Only use tree-sitter
        builder = GraphBuilder(parsers=parsers)
        graph = builder.build(root_dir=str(src_dir))
        
        # Check that graph was built
        assert graph is not None
        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0
        
        # Should have nodes from all languages
        node_ids = list(graph.nodes(data=True))
        node_types = [n[1].get("type") for n in node_ids]
        
        # Check for file nodes
        assert "file" in node_types
        
        # Check for function nodes
        assert "function" in node_types
        
        # Check for class nodes  
        assert "class" in node_types
    
    def test_treesitter_python_extraction(self, tmp_path):
        """Verify Python extraction with TreeSitterParser."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        py_file = src_dir / "test.py"
        py_file.write_text("""
import os
from pathlib import Path

def greet(name):
    '''Say hello.'''
    return f"Hello, {name}!"

class Greeter:
    def __init__(self, prefix="Hi"):
        self.prefix = prefix
    
    def greet(self, name):
        return f"{self.prefix}, {name}!"

MESSAGE = "Hello"
""")
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        # Find function and class nodes
        functions = [n for n, d in graph.nodes(data=True) if d.get("type") == "function"]
        classes = [n for n, d in graph.nodes(data=True) if d.get("type") == "class"]
        variables = [n for n, d in graph.nodes(data=True) if d.get("type") == "variable"]
        
        # Should extract symbols
        assert len(functions) > 0, "Should extract functions"
        assert len(classes) > 0, "Should extract classes"
        assert len(variables) > 0, "Should extract variables"
    
    def test_treesitter_javascript_extraction(self, tmp_path):
        """Verify JavaScript extraction with TreeSitterParser."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        js_file = src_dir / "app.js"
        js_file.write_text("""
function init() {
    console.log('Initializing');
}

const add = (a, b) => a + b;

class App {
    constructor() {
        this.state = {};
    }
    
    render() {
        return null;
    }
}
""")
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        functions = [n for n, d in graph.nodes(data=True) if d.get("type") == "function"]
        classes = [n for n, d in graph.nodes(data=True) if d.get("type") == "class"]
        
        assert len(functions) > 0
        assert len(classes) > 0
    
    def test_treesitter_mixed_language_graph(self, tmp_path):
        """Build a graph with multiple languages together."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        # Python
        src_dir.joinpath("api.py").write_text("""
def handle_request(req):
    return process(req)

def process(data):
    return data
""")
        
        # JavaScript
        src_dir.joinpath("client.js").write_text("""
async function fetchData() {
    const response = await fetch('/api');
    return response.json();
}
""")
        
        # TypeScript
        src_dir.joinpath("types.ts").write_text("""
interface Request {
    id: string;
    data: any;
}

function handle(req: Request): void {
    console.log(req.id);
}
""")
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        # Check we have nodes from all languages
        nodes = list(graph.nodes(data=True))
        files = [n for n in nodes if n[1].get("type") == "file"]
        
        # Should have at least 3 files
        assert len(files) >= 3
    
    def test_parser_selection_by_extension(self, tmp_path):
        """Verify that TreeSitterParser is auto-selected for known extensions."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        # Create files for different languages
        src_dir.joinpath("test.py").write_text("def test(): pass")
        src_dir.joinpath("test.js").write_text("function test() {}")
        src_dir.joinpath("test.go").write_text("func test() {}")
        
        # Pass TreeSitterParser — should handle all file types
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        assert len(graph.nodes) > 0
        functions = [n for n, d in graph.nodes(data=True) if d.get("type") == "function"]
        assert len(functions) > 0  # Should extract from all files


class TestTreeSitterEdgeCases:
    """Test edge cases with TreeSitterParser in graph context."""
    
    def test_empty_directory(self, tmp_path):
        """Handle empty directory gracefully."""
        src_dir = tmp_path / "empty"
        src_dir.mkdir()
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        # Should create at least a root node
        assert graph is not None
    
    def test_unsupported_files_ignored(self, tmp_path):
        """Unsupported file types should not crash."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        # Create files that TreeSitterParser doesn't support
        src_dir.joinpath("data.json").write_text('{"key": "value"}')
        src_dir.joinpath("config.yaml").write_text('key: value\n')
        src_dir.joinpath("README.md").write_text('# README\n')
        
        # Also add a supported file
        src_dir.joinpath("test.py").write_text("def test(): pass")
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        # Should still build and include the .py file
        functions = [n for n, d in graph.nodes(data=True) if d.get("type") == "function"]
        assert len(functions) > 0


class TestTreeSitterCallExtraction:
    """Test extraction of function calls."""
    
    def test_extract_python_calls(self, tmp_path):
        """Extract function calls from Python."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        src_dir.joinpath("caller.py").write_text("""
def helper():
    return 42

def main():
    result = helper()
    print(result)
    return result
""")
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        # Check for call edges
        call_edges = [e for e in graph.edges(data=True) if e[2].get("type") == "calls"]
        assert len(call_edges) > 0, "Should extract function calls"
    
    def test_extract_javascript_calls(self, tmp_path):
        """Extract function calls from JavaScript."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        src_dir.joinpath("app.js").write_text("""
function log(msg) {
    console.log(msg);
}

function main() {
    log("Starting");
    process();
}

function process() {
    return 1;
}
""")
        
        builder = GraphBuilder(parsers=[TreeSitterParser()])
        graph = builder.build(root_dir=str(src_dir))
        
        # Check for call edges
        call_edges = [e for e in graph.edges(data=True) if e[2].get("type") == "calls"]
        assert len(call_edges) > 0
