"""Unit tests for spatial module."""
import pytest

from graph_search.spatial import SpatialMapper


class TestSpatialMapper:
    """Test cases for SpatialMapper."""
    
    def test_spatial_mapper_initialization(self):
        """Test SpatialMapper initialization."""
        mapper = SpatialMapper()
        assert mapper is not None
    
    def test_compute_all_empty_graph(self):
        """Test computing spatial coordinates for empty graph."""
        import networkx as nx
        
        mapper = SpatialMapper()
        empty_graph = nx.DiGraph()
        
        coords = mapper.compute_all(empty_graph)
        assert coords is not None
        assert len(coords) == 0
    
    def test_compute_all_single_node(self):
        """Test computing spatial coordinates for single node."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        graph.add_node("node1", type="test", name="test_node")
        
        coords = mapper.compute_all(graph)
        assert len(coords) == 1
        assert "node1" in coords
    
    def test_compute_all_multiple_nodes(self):
        """Test computing spatial coordinates for multiple nodes."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        for i in range(5):
            graph.add_node(f"node{i}", type="test", name=f"node_{i}")
        
        coords = mapper.compute_all(graph)
        assert len(coords) == 5
        
        # All nodes should have coordinates
        for node_id in graph.nodes:
            assert node_id in coords
    
    def test_compute_all_with_edges(self):
        """Test computing spatial coordinates with edges."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        graph.add_node("node1", type="test", name="node1")
        graph.add_node("node2", type="test", name="node2")
        graph.add_node("node3", type="test", name="node3")
        
        graph.add_edge("node1", "node2")
        graph.add_edge("node2", "node3")
        
        coords = mapper.compute_all(graph)
        assert len(coords) == 3
    
    def test_spatial_coordinates_format(self):
        """Coordinates are dicts containing numeric x, y, z."""
        import networkx as nx

        mapper = SpatialMapper()
        graph = nx.DiGraph()
        graph.add_node("test_node", type="function", name="test")

        coords = mapper.compute_all(graph)
        coord = coords["test_node"]
        assert isinstance(coord, dict)
        for axis in ("x", "y", "z"):
            assert axis in coord
            assert isinstance(coord[axis], (int, float))


class TestSpatialMapperAdvanced:
    """Advanced test cases for SpatialMapper."""
    
    def test_spatial_mapper_consistency(self):
        """Test that spatial mapper produces consistent results."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        # Create a simple graph
        for i in range(3):
            graph.add_node(f"node{i}", type="test", name=f"node_{i}")
        
        graph.add_edge("node0", "node1")
        graph.add_edge("node1", "node2")
        
        # Compute twice
        coords1 = mapper.compute_all(graph)
        coords2 = mapper.compute_all(graph)
        
        # Results should be identical
        assert coords1 == coords2
    
    def test_spatial_mapper_with_different_node_types(self):
        """Test spatial mapper with different node types."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        # Add different types of nodes
        graph.add_node("func1", type="function", name="func1")
        graph.add_node("class1", type="class", name="class1")
        graph.add_node("file1", type="file", name="file1")
        
        coords = mapper.compute_all(graph)
        
        assert len(coords) == 3
        assert all(node_id in coords for node_id in graph.nodes)


class TestSpatialMapperEdgeCases:
    """Test edge cases for SpatialMapper."""
    
    def test_spatial_mapper_cyclic_graph(self):
        """Test spatial mapper with cyclic graph."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        # Create a cycle
        graph.add_edge("node1", "node2")
        graph.add_edge("node2", "node3")
        graph.add_edge("node3", "node1")
        
        coords = mapper.compute_all(graph)
        assert len(coords) == 3
    
    def test_spatial_mapper_dense_graph(self):
        """Test spatial mapper with dense graph."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        # Create a dense graph (all nodes connected)
        for i in range(5):
            for j in range(5):
                if i != j:
                    graph.add_edge(f"node{i}", f"node{j}")
        
        coords = mapper.compute_all(graph)
        assert len(coords) == 5
    
    def test_spatial_mapper_large_graph(self):
        """Test spatial mapper with large graph."""
        import networkx as nx
        
        mapper = SpatialMapper()
        graph = nx.DiGraph()
        
        # Create a larger graph
        for i in range(100):
            graph.add_node(f"node{i}", type="test", name=f"node_{i}")
            if i > 0:
                graph.add_edge(f"node{i-1}", f"node{i}")
        
        coords = mapper.compute_all(graph)
        assert len(coords) == 100
