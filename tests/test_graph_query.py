"""Unit tests for GraphQuery."""
import pytest

from apollo.graph import GraphQuery


class TestGraphQuery:
    """Test cases for GraphQuery."""
    
    def test_query_initialization(self, test_graph_data):
        """Test GraphQuery initialization."""
        query = GraphQuery(test_graph_data)
        assert query is not None
        assert query.graph == test_graph_data
    
    def test_find_function(self, test_graph_data):
        """Test finding a function by name."""
        query = GraphQuery(test_graph_data)
        results = query.find("add", node_type="function")
        
        assert len(results) > 0
        assert results[0]["name"] == "add"
        assert results[0]["type"] == "function"
    
    def test_find_class(self, test_graph_data):
        """Test finding a class by name."""
        query = GraphQuery(test_graph_data)
        results = query.find("Calculator", node_type="class")
        
        assert len(results) > 0
        assert results[0]["name"] == "Calculator"
        assert results[0]["type"] == "class"
    
    def test_find_without_type_filter(self, test_graph_data):
        """Test finding nodes without type filter."""
        query = GraphQuery(test_graph_data)
        results = query.find("add")
        
        assert len(results) > 0
    
    def test_find_nonexistent_node(self, test_graph_data):
        """Test finding a nonexistent node."""
        query = GraphQuery(test_graph_data)
        results = query.find("nonexistent_function")
        
        assert len(results) == 0
    
    def test_stats(self, test_graph_data):
        """Test getting graph statistics."""
        query = GraphQuery(test_graph_data)
        stats = query.stats()
        
        assert "total_nodes" in stats
        assert "total_edges" in stats
        assert "node_types" in stats
        assert "edge_types" in stats
        
        assert stats["total_nodes"] == 4
        assert stats["total_edges"] == 2
    
    def test_stats_node_types(self, test_graph_data):
        """Test that stats includes correct node type counts."""
        query = GraphQuery(test_graph_data)
        stats = query.stats()
        
        node_types = stats.get("node_types", {})
        assert "function" in node_types
        assert "class" in node_types
        assert "method" in node_types
    
    def test_callers_of_function(self, test_graph_data):
        """`add` is called by both `multiply` and `Calculator.compute`."""
        query = GraphQuery(test_graph_data)
        callers = query.callers("func::test.py::add")

        caller_ids = {c.get("id") for c in callers}
        assert "func::test.py::multiply" in caller_ids
        assert "method::test.py::Calculator::compute" in caller_ids

    def test_callees_of_function(self, test_graph_data):
        """`multiply` calls `add`."""
        query = GraphQuery(test_graph_data)
        callees = query.callees("func::test.py::multiply")

        callee_ids = {c.get("id") for c in callees}
        assert "func::test.py::add" in callee_ids

    def test_callers_nonexistent_returns_empty(self, test_graph_data):
        """Callers of a nonexistent node return an empty list."""
        query = GraphQuery(test_graph_data)
        assert query.callers("nonexistent::node") == []

    def test_callees_nonexistent_returns_empty(self, test_graph_data):
        """Callees of a nonexistent node return an empty list."""
        query = GraphQuery(test_graph_data)
        assert query.callees("nonexistent::node") == []
    
class TestGraphQueryAdvanced:
    """Advanced test cases for GraphQuery."""
    
    def test_find_multiple_results(self, test_graph_data):
        """Test finding multiple results."""
        query = GraphQuery(test_graph_data)
        # Add a duplicate node for testing
        test_graph_data.add_node("func::other.py::add", type="function", name="add", path="other.py", line_start=1)
        
        results = query.find("add")
        assert len(results) >= 1
    
    def test_query_result_format(self, test_graph_data):
        """Test that query results have correct format."""
        query = GraphQuery(test_graph_data)
        results = query.find("add")
        
        if results:
            result = results[0]
            assert "id" in result or "name" in result
            assert "type" in result
    
    def test_stats_edge_types(self, test_graph_data):
        """Test that stats includes edge type counts."""
        query = GraphQuery(test_graph_data)
        stats = query.stats()
        
        edge_types = stats.get("edge_types", {})
        assert "calls" in edge_types
        assert edge_types["calls"] == 2


class TestGraphQueryIntegration:
    """Integration tests for GraphQuery."""
    
    def test_query_on_built_graph(self, multi_file_project):
        """Test querying a graph built from multiple files."""
        from apollo.graph import GraphBuilder
        from apollo.parser import PythonParser
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(multi_file_project))
        
        query = GraphQuery(graph)
        stats = query.stats()
        
        # Should have some nodes and edges
        assert stats["total_nodes"] > 0
    
    def test_find_and_traverse_calls(self, multi_file_project):
        """Test finding a function and traversing its calls."""
        from apollo.graph import GraphBuilder
        from apollo.parser import PythonParser
        
        builder = GraphBuilder(parsers=[PythonParser()])
        graph = builder.build(str(multi_file_project))
        
        query = GraphQuery(graph)
        
        # Find functions
        results = query.find("process", node_type="function")
        if results:
            node_id = results[0].get("id") or f"func::{results[0].get('path')}::{results[0].get('name')}"
            # Try to get callees
            callees = query.callees(node_id, depth=1)
            assert isinstance(callees, list)
