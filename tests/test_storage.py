"""Unit tests for storage module."""
import json
import pytest

from graph_search.storage import open_store


class TestStorageFactory:
    """Test cases for the storage factory."""
    
    def test_open_store_json(self, temp_dir):
        """Test opening JSON storage."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        assert store is not None
        store.close()
    
    def test_invalid_backend(self, temp_dir):
        """Test invalid backend raises error."""
        with pytest.raises(ValueError):
            open_store("invalid", str(temp_dir / "index"))


class TestJsonStore:
    """Test cases for JsonStore."""
    
    def test_save_and_load_graph(self, temp_dir, test_graph_data):
        """Test saving and loading a graph."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        # Save graph
        store.save(test_graph_data)
        
        # Load graph
        loaded_graph = store.load(include_embeddings=False)
        
        assert loaded_graph is not None
        assert len(loaded_graph.nodes) == len(test_graph_data.nodes)
        assert len(loaded_graph.edges) == len(test_graph_data.edges)
        
        store.close()
    
    def test_store_creates_file(self, temp_dir, test_graph_data):
        """Test that store creates the index file."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        store.save(test_graph_data)
        
        # Check file was created
        assert (temp_dir / "test_index.json").exists()
        
        store.close()
    
    def test_store_file_is_valid_json(self, temp_dir, test_graph_data):
        """Test that stored file is valid JSON."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        store.save(test_graph_data)
        
        # Try to load and parse the JSON
        with open(store_path) as f:
            data = json.load(f)
            assert data is not None
        
        store.close()
    
    def test_load_nonexistent_store(self, temp_dir):
        """Loading a store whose file does not yet exist should raise."""
        store_path = str(temp_dir / "missing.json")
        store = open_store("json", store_path)
        with pytest.raises(Exception):
            store.load(include_embeddings=False)
        store.close()
    
    def test_store_close(self, temp_dir, test_graph_data):
        """Test closing store."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        store.save(test_graph_data)
        store.close()
        
        # After close, store should not be usable
        # (implementation dependent)
    
    def test_graph_node_attributes_preserved(self, temp_dir, test_graph_data):
        """Test that node attributes are preserved through save/load."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        store.save(test_graph_data)
        loaded_graph = store.load(include_embeddings=False)
        
        # Check that original node data is preserved
        for node_id in test_graph_data.nodes:
            original_attrs = test_graph_data.nodes[node_id]
            loaded_attrs = loaded_graph.nodes[node_id]
            
            # Core attributes should match
            assert loaded_attrs.get("name") == original_attrs.get("name")
            assert loaded_attrs.get("type") == original_attrs.get("type")
        
        store.close()
    
    def test_graph_edges_preserved(self, temp_dir, test_graph_data):
        """Test that edges are preserved through save/load."""
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        store.save(test_graph_data)
        loaded_graph = store.load(include_embeddings=False)
        
        # Check edges
        for edge in test_graph_data.edges:
            assert edge in loaded_graph.edges
        
        store.close()


class TestStorageEdgeCases:
    """Test edge cases for storage."""
    
    def test_save_empty_graph(self, temp_dir):
        """Test saving an empty graph."""
        import networkx as nx
        
        empty_graph = nx.DiGraph()
        store_path = str(temp_dir / "empty.json")
        store = open_store("json", store_path)
        
        store.save(empty_graph)
        loaded_graph = store.load(include_embeddings=False)
        
        assert len(loaded_graph.nodes) == 0
        assert len(loaded_graph.edges) == 0
        
        store.close()
    
    def test_overwrite_existing_index(self, temp_dir, test_graph_data):
        """Test overwriting an existing index."""
        import networkx as nx
        
        store_path = str(temp_dir / "test_index.json")
        store = open_store("json", store_path)
        
        # Save first graph
        store.save(test_graph_data)
        
        # Create and save second graph
        another_graph = nx.DiGraph()
        another_graph.add_node("new_node", type="test", name="new")
        store.save(another_graph)
        
        # Load and verify it's the new graph
        loaded_graph = store.load(include_embeddings=False)
        assert len(loaded_graph.nodes) == 1
        
        store.close()
    
    def test_store_path_with_subdirectories(self, temp_dir):
        """Test storing in nested subdirectories."""
        import os
        
        nested_path = str(temp_dir / "nested" / "path" / "index.json")
        
        # open_store should create directories if needed
        store = open_store("json", nested_path)
        assert store is not None
        
        store.close()
