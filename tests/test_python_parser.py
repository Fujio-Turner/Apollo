"""Unit tests for PythonParser."""
import pytest

from apollo.parser import PythonParser


class TestPythonParser:
    """Test cases for PythonParser."""
    
    def test_can_parse_py_file(self):
        """Test that PythonParser can parse .py files."""
        parser = PythonParser()
        assert parser.can_parse("test.py") is True
        assert parser.can_parse("module.py") is True
    
    def test_cannot_parse_other_files(self):
        """Test that PythonParser cannot parse non-.py files."""
        parser = PythonParser()
        assert parser.can_parse("test.js") is False
        assert parser.can_parse("test.md") is False
        assert parser.can_parse("test.txt") is False
    
    def test_parse_simple_function(self, sample_python_file):
        """Test parsing a simple function."""
        parser = PythonParser()
        result = parser.parse_file(str(sample_python_file))
        
        assert result is not None
        assert "functions" in result
        assert any(f["name"] == "add" for f in result["functions"])
        assert any(f["name"] == "multiply" for f in result["functions"])
    
    def test_parse_class(self, sample_python_file):
        """Test parsing a class."""
        parser = PythonParser()
        result = parser.parse_file(str(sample_python_file))
        
        assert result is not None
        assert "classes" in result
        assert any(c["name"] == "Calculator" for c in result["classes"])
    
    def test_parse_imports(self, sample_python_file):
        """Test parsing imports."""
        parser = PythonParser()
        result = parser.parse_file(str(sample_python_file))
        
        assert result is not None
        assert "imports" in result
        assert any(imp["module"] == "os" for imp in result["imports"])
        assert any(imp["module"] == "json" for imp in result["imports"])
    
    def test_parse_nonexistent_file(self):
        """Test parsing a nonexistent file."""
        parser = PythonParser()
        result = parser.parse_file("/nonexistent/file.py")
        assert result is None
    
    def test_parse_syntax_error(self, temp_dir):
        """Test parsing a file with syntax errors."""
        bad_file = temp_dir / "bad.py"
        bad_file.write_text("def foo(\n    invalid syntax here")
        
        parser = PythonParser()
        result = parser.parse_file(str(bad_file))
        assert result is None
    
    def test_parse_with_module_docstring(self, temp_dir):
        """Test parsing a file with module docstring."""
        py_file = temp_dir / "with_docstring.py"
        py_file.write_text('"""This is a module docstring."""\n\ndef foo():\n    pass')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        assert result.get("module_docstring") == "This is a module docstring."
    
    def test_parse_function_with_docstring(self, temp_dir):
        """Test parsing a function with docstring."""
        py_file = temp_dir / "docstring_func.py"
        py_file.write_text('''
def greet(name):
    """Greet someone by name."""
    return f"Hello, {name}!"
''')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        func = next((f for f in result["functions"] if f["name"] == "greet"), None)
        assert func is not None
        assert "docstring" in func or "description" in str(func)
    
    def test_parse_from_source_string(self):
        """Test parsing from a source string directly."""
        source = '''
def hello():
    """Say hello."""
    print("Hello, World!")

class Greeter:
    """A greeter class."""
    pass
'''
        parser = PythonParser()
        result = parser.parse_source(source, "test.py")
        
        assert result is not None
        assert any(f["name"] == "hello" for f in result["functions"])
        assert any(c["name"] == "Greeter" for c in result["classes"])
    
    def test_parse_class_methods(self, temp_dir):
        """Test parsing class methods."""
        py_file = temp_dir / "class_methods.py"
        py_file.write_text('''
class MyClass:
    """A test class."""
    
    def __init__(self):
        self.value = 0
    
    def method1(self):
        """First method."""
        return self.value
    
    def method2(self):
        """Second method."""
        return self.method1() + 1
''')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        classes = result.get("classes", [])
        my_class = next((c for c in classes if c["name"] == "MyClass"), None)
        assert my_class is not None
        assert "methods" in my_class
    
    def test_parse_comments(self, temp_dir):
        """Test parsing comments with tags."""
        py_file = temp_dir / "with_comments.py"
        py_file.write_text('''
def incomplete():
    # TODO: Implement this
    pass

def buggy():
    # FIXME: This is broken
    return None
''')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        # Just verify it parses without error
        assert result is not None
        assert "functions" in result
    
    def test_parse_empty_file(self, temp_dir):
        """Test parsing an empty file."""
        empty_file = temp_dir / "empty.py"
        empty_file.write_text("")
        
        parser = PythonParser()
        result = parser.parse_file(str(empty_file))
        
        assert result is not None
        assert result.get("functions", []) == []
        assert result.get("classes", []) == []


class TestPythonParserAdvanced:
    """Advanced test cases for PythonParser."""
    
    def test_parse_nested_classes(self, temp_dir):
        """Test parsing nested classes."""
        py_file = temp_dir / "nested.py"
        py_file.write_text('''
class Outer:
    """Outer class."""
    
    class Inner:
        """Inner class."""
        pass
''')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        classes = result.get("classes", [])
        assert any(c["name"] == "Outer" for c in classes)
    
    def test_parse_decorators(self, temp_dir):
        """Test parsing decorated functions."""
        py_file = temp_dir / "decorated.py"
        py_file.write_text('''
def decorator(func):
    return func

@decorator
def decorated_func():
    """A decorated function."""
    pass
''')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        assert result is not None
        assert any(f["name"] == "decorated_func" for f in result["functions"])
    
    def test_parse_lambda_and_comprehensions(self, temp_dir):
        """Test parsing files with lambdas and comprehensions."""
        py_file = temp_dir / "functional.py"
        py_file.write_text('''
data = [1, 2, 3]
squared = [x**2 for x in data]
mapper = lambda x: x * 2
result = list(map(mapper, data))
''')
        
        parser = PythonParser()
        result = parser.parse_file(str(py_file))
        
        # Just verify it parses without error
        assert result is not None
