"""Pytest configuration and fixtures for apollo tests."""
import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_python_file(temp_dir):
    """Create a sample Python file for testing."""
    test_file = temp_dir / "sample.py"
    content = '''
"""Sample module for testing."""

import os
import json
from pathlib import Path

MY_CONST = 42

def add(a, b):
    """Add two numbers."""
    return a + b

def multiply(a, b):
    """Multiply two numbers."""
    result = add(a, 0) + a * (b - 1)
    return result

class Calculator:
    """Simple calculator class."""
    
    def __init__(self):
        self.value = 0
    
    def reset(self):
        """Reset calculator."""
        self.value = 0
    
    def compute(self, x, y):
        """Compute result."""
        self.value = add(x, y)
        return self.value
'''
    test_file.write_text(content)
    return test_file


@pytest.fixture
def sample_markdown_file(temp_dir):
    """Create a sample Markdown file for testing."""
    md_file = temp_dir / "README.md"
    content = '''# Sample Project

## Overview
This is a test markdown file.

## Functions
- `add(a, b)`: Adds two numbers
- `multiply(a, b)`: Multiplies two numbers

## Classes
- `Calculator`: A simple calculator class
'''
    md_file.write_text(content)
    return md_file


@pytest.fixture
def sample_json_file(temp_dir):
    """Create a sample JSON file for testing."""
    json_file = temp_dir / "config.json"
    config = {
        "name": "apollo",
        "version": "1.0.0",
        "debug": True,
        "max_depth": 10,
    }
    json_file.write_text(json.dumps(config, indent=2))
    return json_file


@pytest.fixture
def multi_file_project(temp_dir):
    """Create a multi-file Python project for testing."""
    # Create directory structure
    src_dir = temp_dir / "src"
    src_dir.mkdir()
    
    # main.py
    main_py = src_dir / "main.py"
    main_py.write_text('''
from utils import helper

def main():
    result = helper.process("data")
    print(result)

if __name__ == "__main__":
    main()
''')
    
    # utils.py
    utils_py = src_dir / "utils.py"
    utils_py.write_text('''
"""Utility functions."""

def process(data):
    """Process data."""
    return data.upper()

def format_output(text):
    """Format output."""
    return f"Result: {text}"

class Helper:
    """Helper class."""
    
    @staticmethod
    def transform(value):
        """Transform value."""
        return str(value).strip()
''')
    
    # config.json
    config_json = src_dir / "config.json"
    config_json.write_text('{"timeout": 30, "retries": 3}')
    
    return src_dir


@pytest.fixture
def test_graph_data():
    """Create sample graph data for testing."""
    import networkx as nx
    
    G = nx.DiGraph()
    
    # Add nodes
    G.add_node("func::test.py::add", type="function", name="add", path="test.py", line_start=1)
    G.add_node("func::test.py::multiply", type="function", name="multiply", path="test.py", line_start=5)
    G.add_node("class::test.py::Calculator", type="class", name="Calculator", path="test.py", line_start=10)
    G.add_node("method::test.py::Calculator::compute", type="method", name="compute", path="test.py", line_start=15)
    
    # Add edges (calls)
    G.add_edge("func::test.py::multiply", "func::test.py::add", type="calls")
    G.add_edge("method::test.py::Calculator::compute", "func::test.py::add", type="calls")
    
    return G
