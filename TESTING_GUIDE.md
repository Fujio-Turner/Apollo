# Testing Guide for graph_search

## Quick Start

### Run all tests
```bash
pytest tests/
```

### Run with summary
```bash
pytest tests/ -v
```

### Run with coverage
```bash
pytest tests/ --cov=graph_search
```

### Run specific test file
```bash
pytest tests/test_python_parser.py -v
```

## Test Files Created

```
tests/
├── __init__.py                  # Package init
├── conftest.py                  # Shared pytest fixtures
├── test_python_parser.py        # Python parsing tests (19)
├── test_graph_builder.py        # Graph building tests (19)
├── test_graph_query.py          # Graph querying tests (18)
├── test_markdown_parser.py      # Markdown parsing tests (17)
├── test_text_parser.py          # Text file parsing tests (18)
├── test_storage.py              # Storage/serialization tests (14)
├── test_spatial.py              # Spatial computation tests (11)
├── README.md                    # Detailed test documentation
└── pytest.ini                   # Pytest configuration
```

## Test Statistics

| Metric | Value |
|--------|-------|
| Total Tests | 106 |
| Pass Rate | 100% |
| Execution Time | 0.5s |
| Lines of Test Code | ~2000+ |
| Modules Tested | 8 |
| Coverage | 25% overall, 70-100% for core modules |

## Test Categories

### 1. Parser Tests (53 tests)
- **Python Parser**: 19 tests
  - Functions, classes, imports, decorators
  - Syntax errors, docstrings, edge cases
  
- **Markdown Parser**: 17 tests
  - Headings, code blocks, tables
  - Lists, links, frontmatter, images
  - Edge cases: empty files, unicode, HTML
  
- **Text Parser**: 18 tests
  - JSON, YAML, CSV, TSV parsing
  - Edge cases: empty files, malformed input, unicode

### 2. Graph Tests (37 tests)
- **Graph Builder**: 19 tests
  - Single/multi-file building
  - Incremental builds
  - Virtual environment exclusion
  - File and function node creation
  
- **Graph Query**: 18 tests
  - Finding nodes by name
  - Caller/callee traversal
  - Statistics computation
  - Depth-based traversal

### 3. Storage Tests (14 tests)
- Save/load operations
- JSON serialization
- Node attributes preservation
- Edge preservation
- Factory pattern
- Edge cases: empty graphs, overwrites

### 4. Spatial Tests (11 tests)
- Coordinate computation
- Consistency checks
- Different node types
- Cyclic graphs
- Large graphs (100+ nodes)

## Running Specific Tests

### Run single test file
```bash
pytest tests/test_python_parser.py
```

### Run single test class
```bash
pytest tests/test_python_parser.py::TestPythonParser
```

### Run single test
```bash
pytest tests/test_python_parser.py::TestPythonParser::test_can_parse_py_file
```

### Run by pattern
```bash
pytest tests/ -k "parse"  # All tests with "parse" in name
pytest tests/ -k "edge"   # All edge case tests
```

## Advanced Options

### Verbose output with tracebacks
```bash
pytest tests/ -vv --tb=long
```

### Stop on first failure
```bash
pytest tests/ -x
```

### Run last failed tests
```bash
pytest tests/ --lf
```

### Run failed tests first, then pass
```bash
pytest tests/ --ff
```

### Show slowest 10 tests
```bash
pytest tests/ --durations=10
```

### Run in parallel (requires pytest-xdist)
```bash
pip install pytest-xdist
pytest tests/ -n auto
```

## Coverage Reports

### Terminal coverage report
```bash
pytest tests/ --cov=graph_search --cov-report=term-missing
```

### HTML coverage report
```bash
pytest tests/ --cov=graph_search --cov-report=html
# Open htmlcov/index.html in browser
```

### XML coverage report (for CI/CD)
```bash
pytest tests/ --cov=graph_search --cov-report=xml
```

## Test Dependencies

Install test dependencies:
```bash
pip install pytest pytest-cov
```

Full installation with all project dependencies:
```bash
pip install -r requirements.txt
pip install pytest pytest-cov
```

## Fixtures Available

All tests can use these shared fixtures from `conftest.py`:

### File Fixtures
- `temp_dir`: Temporary directory for test files
- `sample_python_file`: Sample .py file with functions/classes
- `sample_markdown_file`: Sample .md file
- `sample_json_file`: Sample .json config file

### Project Fixtures
- `multi_file_project`: Multi-file Python project structure

### Graph Fixtures
- `test_graph_data`: Pre-built NetworkX DiGraph for testing

## Adding New Tests

1. Create test file: `tests/test_<feature>.py`
2. Import fixtures from conftest
3. Define test class: `class Test<Feature>:`
4. Define test methods: `def test_<behavior>(self):`
5. Use descriptive docstrings
6. Group related tests in classes

Example:
```python
import pytest
from graph_search.parser import MyParser

class TestMyParser:
    """Tests for MyParser."""
    
    def test_can_parse_files(self):
        """Test that parser recognizes valid files."""
        parser = MyParser()
        assert parser.can_parse("file.ext") is True
    
    def test_parse_file(self, sample_python_file):
        """Test parsing a file."""
        parser = MyParser()
        result = parser.parse_file(str(sample_python_file))
        assert result is not None
```

## CI/CD Integration

### GitHub Actions Example
```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -r requirements.txt pytest pytest-cov
      - run: pytest tests/ --cov=graph_search --cov-report=xml
      - uses: codecov/codecov-action@v2
```

## Troubleshooting

### Tests not found
```bash
# Check pytest.ini configuration
pytest --version
pytest tests/ --collect-only
```

### Import errors
```bash
# Ensure graph_search is in PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest tests/
```

### Fixture not found
```bash
# Check conftest.py is in tests/
# Check fixture name matches exactly
pytest tests/ --fixtures | grep fixture_name
```

### Slow tests
```bash
# Find slowest tests
pytest tests/ --durations=10

# Run only fast tests
pytest tests/ --timeout=1
```

## Best Practices

✅ **Do**:
- Write one assertion per test when possible
- Use descriptive test names
- Use fixtures for common setup
- Test edge cases and error conditions
- Keep tests isolated and independent
- Use pytest markers for categorization

❌ **Don't**:
- Depend on test execution order
- Share state between tests
- Test multiple behaviors in one test
- Use hardcoded paths
- Make assumptions about file system

## Contact & Support

For issues running tests, see [TEST_RESULTS.md](TEST_RESULTS.md) for detailed results.

All tests are passing: **106/106** ✅
