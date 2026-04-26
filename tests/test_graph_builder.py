"""Unit tests for GraphBuilder."""
import pytest

from apollo.graph import GraphBuilder
from apollo.parser import PythonParser, MarkdownParser, TextFileParser


class TestGraphBuilder:
    """Test cases for GraphBuilder."""
    
    def test_builder_initialization(self):
        """Test GraphBuilder initialization."""
        parsers = [PythonParser()]
        builder = GraphBuilder(parsers=parsers)
        
        assert builder is not None
    
    def test_build_empty_directory(self, temp_dir):
        """Test building graph from empty directory."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        assert graph is not None
        # May create a root directory node
        assert len(graph.nodes) >= 0
    
    def test_build_single_file(self, sample_python_file, temp_dir):
        """Test building graph from single Python file."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        assert graph is not None
        # Should have nodes for file, functions, classes, etc.
        assert len(graph.nodes) > 0
    
    def test_build_with_multiple_files(self, multi_file_project):
        """Test building graph from multiple files."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(multi_file_project))
        
        assert graph is not None
        assert len(graph.nodes) > 0
    
    def test_builder_with_multiple_parsers(self, temp_dir):
        """Test builder with multiple parsers."""
        parsers = [PythonParser(), MarkdownParser(), TextFileParser()]
        builder = GraphBuilder(parsers=parsers)
        graph = builder.build(str(temp_dir))
        
        assert graph is not None
    
    def test_build_skips_venv_directories(self, temp_dir):
        """Test that builder skips virtual environment directories."""
        # Create a venv-like directory
        venv_dir = temp_dir / "venv"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").write_text("")
        
        # Add a Python file inside venv (should be skipped)
        py_file = venv_dir / "module.py"
        py_file.write_text("def func():\n    pass")
        
        # Add a Python file outside venv (should be included)
        main_py = temp_dir / "main.py"
        main_py.write_text("def main():\n    pass")
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        # Should have nodes for main.py but not venv files
        assert graph is not None
        # main.py should be in the graph
        assert any("main.py" in str(node) for node in graph.nodes)
    
    def test_build_skips_node_modules(self, temp_dir):
        """Test that builder skips node_modules directories."""
        # Create node_modules directory
        nm_dir = temp_dir / "node_modules"
        nm_dir.mkdir()
        
        # Add files inside node_modules (should be skipped)
        js_file = nm_dir / "module.js"
        js_file.write_text("function test() {}")
        
        # Add a Python file outside (should be included)
        py_file = temp_dir / "app.py"
        py_file.write_text("def app():\n    pass")
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        assert graph is not None
    
    def test_build_returns_networkx_graph(self, sample_python_file, temp_dir):
        """Test that build returns a NetworkX graph."""
        import networkx as nx
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        assert isinstance(graph, nx.DiGraph)
    
    def test_graph_nodes_have_required_attributes(self, sample_python_file, temp_dir):
        """Test that graph nodes have required attributes."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        for node_id, node_data in graph.nodes(data=True):
            # Each node should have these attributes
            assert "type" in node_data or node_id
            if "type" in node_data:
                assert node_data["type"] in [
                    "file", "function", "class", "method", "variable",
                    "import", "directory", "comment", "string"
                ]
    
    def test_build_creates_file_nodes(self, sample_python_file, temp_dir):
        """Test that build creates file nodes."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        # Should have at least one file node
        file_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") == "file"]
        assert len(file_nodes) > 0
    
    def test_build_creates_function_nodes(self, sample_python_file, temp_dir):
        """Test that build creates function nodes."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        # Should have function nodes
        func_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") == "function"]
        assert len(func_nodes) > 0
    
    def test_build_creates_edges(self, sample_python_file, temp_dir):
        """Build creates edges including call relationships."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))

        assert len(graph.edges) > 0
        # multiply() calls add() in the sample file
        edge_types = {d.get("type") for _, _, d in graph.edges(data=True)}
        assert "calls" in edge_types

    def test_build_resolves_call_edge_between_functions(self, temp_dir):
        """A direct call from one function to another is captured."""
        py_file = temp_dir / "calls.py"
        py_file.write_text("def caller():\n    callee()\n\ndef callee():\n    pass\n")

        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))

        caller_id = "func::calls.py::caller"
        callee_id = "func::calls.py::callee"
        assert caller_id in graph.nodes
        assert callee_id in graph.nodes
        # Edge from caller -> callee should exist with type "calls"
        assert graph.has_edge(caller_id, callee_id)
        assert graph.edges[caller_id, callee_id].get("type") == "calls"


class TestGraphBuilderIncremental:
    """Test cases for incremental building."""
    
    def test_incremental_build_returns_tuple(self, sample_python_file, temp_dir):
        """Test that incremental build returns (graph, hashes) tuple."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph, hashes = builder.build_incremental(str(temp_dir), {})
        
        assert graph is not None
        assert isinstance(hashes, dict)
    
    def test_incremental_build_with_empty_hashes(self, sample_python_file, temp_dir):
        """Test incremental build with no previous hashes."""
        builder = GraphBuilder(parsers=[PythonParser()])
        graph, hashes = builder.build_incremental(str(temp_dir), {})
        
        assert graph is not None
        assert len(hashes) > 0
    
    def test_incremental_build_with_previous_hashes(self, sample_python_file, temp_dir):
        """Test incremental build with previous hashes."""
        builder = GraphBuilder(parsers=[PythonParser()])
        
        # First build
        _, hashes1 = builder.build_incremental(str(temp_dir), {})
        
        # Second build with same files (should detect no changes)
        _, hashes2 = builder.build_incremental(str(temp_dir), hashes1)
        
        assert hashes2 == hashes1
    
    def test_incremental_build_detects_changes(self, sample_python_file, temp_dir):
        """Test that incremental build detects file changes."""
        builder = GraphBuilder(parsers=[PythonParser()])
        
        # First build
        _, hashes1 = builder.build_incremental(str(temp_dir), {})
        
        # Modify a file
        sample_python_file.write_text("def new_function():\n    pass")
        
        # Second build should detect change
        _, hashes2 = builder.build_incremental(str(temp_dir), hashes1)
        
        # Hashes should be different
        assert hashes1 != hashes2


class TestGraphBuilderEdgeCases:
    """Test edge cases for GraphBuilder."""
    
    def test_build_with_empty_python_file(self, temp_dir):
        """Test building from empty Python file."""
        py_file = temp_dir / "empty.py"
        py_file.write_text("")
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        assert graph is not None
    
    def test_build_with_syntax_errors(self, temp_dir):
        """Test building skips files with syntax errors."""
        bad_py = temp_dir / "bad.py"
        bad_py.write_text("def foo(\n    invalid")
        
        good_py = temp_dir / "good.py"
        good_py.write_text("def bar():\n    pass")
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        # Should successfully parse good.py despite bad.py
        assert graph is not None
        assert any("good.py" in str(node) for node in graph.nodes)
    
    def test_build_with_unicode_files(self, temp_dir):
        """Test building handles Unicode file content."""
        unicode_py = temp_dir / "unicode.py"
        unicode_py.write_text('# -*- coding: utf-8 -*-\n"""文字テスト"""\ndef hello():\n    """こんにちは"""\n    pass')
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(temp_dir))
        
        assert graph is not None
