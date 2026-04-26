# Unit Tests for graph_search

This directory contains comprehensive unit tests for the graph_search project.

## Overview

- **Total Tests**: 131 tests
- **Pass Rate**: 100% ✅
- **Code Coverage**: 25% (core modules: 70-100%)

## Test Structure

### Test Modules

1. **test_python_parser.py** (16 tests)
   - Tests for `PythonParser` class
   - Covers parsing Python functions, classes, imports, and edge cases
   - Tests file parsing, syntax error handling, and docstring extraction

2. **test_graph_builder.py** (20 tests)
   - Tests for `GraphBuilder` class
   - Covers single and multi-file graph building
   - Tests incremental builds and directory skipping logic
   - Tests graph node/edge creation, including resolved `calls` edges

3. **test_graph_query.py** (16 tests)
   - Tests for `GraphQuery` class
   - Covers finding nodes, callers/callees analysis (verifies specific node IDs)
   - Tests graph statistics and traversal functions
   - Includes integration tests with built graphs

4. **test_markdown_parser.py** (15 tests)
   - Tests for `MarkdownParser` class
   - Verifies extracted sections, code-block languages, links, tables, and frontmatter values

5. **test_text_parser.py** (15 tests)
   - Tests for `TextFileParser` class
   - Covers JSON, YAML, CSV, and plain text parsing
   - Tests edge cases: empty files, unicode, large files, malformed JSON

6. **test_storage.py** (12 tests)
   - Tests for storage module (JsonStore)
   - Covers save/load operations, including loading a missing file (raises)
   - Tests graph serialization and deserialization
   - Tests edge cases with empty graphs and overwrites

7. **test_spatial.py** (11 tests)
   - Tests for `SpatialMapper` class
   - Covers spatial coordinate computation (verifies dict with numeric x/y/z)
   - Tests consistency, cyclic graphs, and large graphs

8. **test_chat_providers.py** (26 tests)
   - Tests for the multi-provider AI chat configuration
     (xAI, OpenAI, Gemini, Anthropic, Llama-via-Groq).
   - `chat.providers` registry: required fields, env-var uniqueness,
     `get_provider`/`env_key`/`has_api_key`/`public_registry` helpers.
   - `chat.service.ChatService`:
     - active provider/model resolution from `settings_provider`
     - graceful fallback when settings_provider raises or returns junk
     - legacy `default_model` setting compatibility
     - `available` flag tied to the active provider's env var
     - `_get_client` builds the OpenAI client with the correct
       `base_url`/`api_key`, caches it, and invalidates on provider
       switch, key rotation, and `reset_client()` calls
     - `RuntimeError` when the active provider's API key is missing
   - `web.server` HTTP endpoints (FastAPI `TestClient`):
     - `GET /api/settings` returns provider registry + masked keys
     - `PUT /api/settings` persists `active_provider` + per-provider
       `model`, writes API keys to `.env` only (never to `settings.json`),
       ignores masked/unknown providers, rejects invalid `active_provider`
     - `GET /api/chat/status` reflects the active provider, model,
       label, and `available` based on env-var presence

   *Note*: these tests use `unittest.mock.patch.dict(os.environ, ...)`
   to isolate API-key state and a temp `.env`/`settings.json` so the
   real project files are never touched.

### Test Fixtures (conftest.py)

Shared pytest fixtures for all tests:

- **temp_dir**: Temporary directory for test files
- **sample_python_file**: Sample Python file with functions and classes
- **sample_markdown_file**: Sample Markdown file
- **sample_json_file**: Sample JSON configuration file
- **multi_file_project**: Multi-file Python project structure
- **test_graph_data**: Pre-built NetworkX graph for testing

## Running Tests

### Run all tests
```bash
pytest tests/
```

### Run with verbose output
```bash
pytest tests/ -v
```

### Run specific test file
```bash
pytest tests/test_python_parser.py -v
```

### Run specific test class
```bash
pytest tests/test_python_parser.py::TestPythonParser -v
```

### Run specific test
```bash
pytest tests/test_python_parser.py::TestPythonParser::test_can_parse_py_file -v
```

### Generate coverage report
```bash
pytest tests/ --cov=graph_search --cov-report=html
```

This generates an HTML coverage report in `htmlcov/index.html`

## Test Coverage

### Core Modules (High Coverage)
- `parser/__init__.py`: 100%
- `graph/__init__.py`: 100%
- `storage/__init__.py`: 100%
- `parser/markdown_parser.py`: 88%
- `parser/text_parser.py`: 86%
- `storage/json_store.py`: 87%
- `parser/base.py`: 89%

### Well-Tested Modules
- `graph/builder.py`: 75% (304 statements)
- `parser/python_parser.py`: 75% (276 statements)
- `spatial.py`: 70% (137 statements)
- `storage/factory.py`: 78%

### Untested Modules (Optional features)
- `web/server.py`: 0% (web interface)
- `chat/service.py`: 0% (AI chat features)
- `embeddings/embedder.py`: 0% (embedding generation)
- `search/semantic.py`: 0% (semantic search)
- `watcher.py`: 0% (file watching)

## Test Categories

### Unit Tests
Individual function/class testing with isolated dependencies

### Integration Tests
Tests that combine multiple components:
- `TestGraphQueryIntegration`: Tests querying on built graphs
- Graph building + query combinations

### Edge Case Tests
- Empty files and graphs
- Syntax errors and malformed input
- Unicode and special characters
- Large graphs (100+ nodes)
- Cyclic dependencies

## Continuous Integration

To run tests in CI/CD:

```bash
# Install dependencies
pip install -r requirements.txt pytest pytest-cov

# Run tests with coverage
pytest tests/ --cov=graph_search --cov-report=xml --cov-report=term

# Generate coverage badge
coverage-badge -o coverage.svg -f
```

## Adding New Tests

When adding new features:

1. Create test file: `tests/test_<feature>.py`
2. Use existing fixtures from `conftest.py`
3. Follow naming convention: `Test<ClassName>` for classes, `test_<method>` for functions
4. Add docstrings to each test
5. Group related tests in test classes
6. Test edge cases and error conditions

Example:
```python
def test_new_feature(self, sample_python_file):
    """Test description."""
    # Setup
    parser = NewParser()
    
    # Execute
    result = parser.parse_file(str(sample_python_file))
    
    # Assert
    assert result is not None
    assert result.get("key") == "expected_value"
```

## Test Quality Notes

- ✅ All core parsing functionality is covered
- ✅ Graph building and querying are well tested
- ✅ Storage serialization is verified
- ✅ Edge cases and error handling are tested
- ⚠️ Web server and API endpoints not covered (requires integration tests)
- ⚠️ Embedding generation not covered (requires external dependencies)
- ⚠️ File watching not covered (requires integration tests)

## Dependencies

Tests require:
- pytest >= 8.0
- pytest-cov >= 7.0
- networkx >= 3.0
- All packages in requirements.txt

Install with:
```bash
pip install -r requirements.txt pytest pytest-cov
```
