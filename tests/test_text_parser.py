"""Unit tests for TextFileParser."""
import pytest

from apollo.parser import TextFileParser


class TestTextFileParser:
    """Test cases for TextFileParser."""
    
    def test_can_parse_common_text_files(self):
        """Test that TextFileParser can parse common text file types."""
        parser = TextFileParser()
        assert parser.can_parse("config.json") is True
        assert parser.can_parse("data.yaml") is True
        assert parser.can_parse("settings.yml") is True
        assert parser.can_parse("data.csv") is True
        assert parser.can_parse("README.txt") is True
    
    def test_cannot_parse_code_files(self):
        """TextFileParser should not claim Python or Markdown files."""
        parser = TextFileParser()
        assert parser.can_parse("test.py") is False
        assert parser.can_parse("README.md") is False
    
    def test_parse_json_file(self, sample_json_file):
        """Test parsing a JSON file."""
        parser = TextFileParser()
        result = parser.parse_file(str(sample_json_file))
        
        assert result is not None
    
    def test_parse_text_file(self, temp_dir):
        """Test parsing a plain text file."""
        txt_file = temp_dir / "data.txt"
        txt_file.write_text("""Line 1
Line 2
Line 3
""")
        
        parser = TextFileParser()
        result = parser.parse_file(str(txt_file))
        
        assert result is not None
    
    def test_parse_csv_file(self, temp_dir):
        """Test parsing a CSV file."""
        csv_file = temp_dir / "data.csv"
        csv_file.write_text("""name,age,city
Alice,30,NYC
Bob,25,LA
Charlie,35,Chicago
""")
        
        parser = TextFileParser()
        result = parser.parse_file(str(csv_file))
        
        assert result is not None
    
    def test_parse_yaml_file(self, temp_dir):
        """Test parsing a YAML file."""
        yaml_file = temp_dir / "config.yaml"
        yaml_file.write_text("""app:
  name: MyApp
  version: 1.0.0
  debug: true
database:
  host: localhost
  port: 5432
""")
        
        parser = TextFileParser()
        result = parser.parse_file(str(yaml_file))
        
        assert result is not None
    
    def test_parse_nonexistent_file(self):
        """Test parsing a nonexistent file."""
        parser = TextFileParser()
        result = parser.parse_file("/nonexistent/file.txt")
        assert result is None
    
    def test_parse_json_with_nested_structure(self, temp_dir):
        """Test parsing JSON with nested structure."""
        json_file = temp_dir / "nested.json"
        json_file.write_text('''{
  "users": [
    {
      "name": "Alice",
      "roles": ["admin", "user"],
      "metadata": {
        "created": "2024-01-01",
        "updated": "2024-01-15"
      }
    }
  ]
}''')
        
        parser = TextFileParser()
        result = parser.parse_file(str(json_file))
        
        assert result is not None


class TestTextFileParserEdgeCases:
    """Test edge cases for TextFileParser."""
    
    def test_parse_empty_json(self, temp_dir):
        """Test parsing empty JSON object."""
        json_file = temp_dir / "empty.json"
        json_file.write_text("{}")
        
        parser = TextFileParser()
        result = parser.parse_file(str(json_file))
        
        assert result is not None
    
    def test_parse_empty_text_file(self, temp_dir):
        """Empty text files return None."""
        txt_file = temp_dir / "empty.txt"
        txt_file.write_text("")

        parser = TextFileParser()
        result = parser.parse_file(str(txt_file))

        assert result is None
    
    def test_parse_file_with_unicode(self, temp_dir):
        """Test parsing file with unicode content."""
        txt_file = temp_dir / "unicode.txt"
        txt_file.write_text("""Hello World
你好世界
こんにちは
مرحبا بالعالم
""", encoding="utf-8")
        
        parser = TextFileParser()
        result = parser.parse_file(str(txt_file))
        
        assert result is not None
    
    def test_parse_large_text_file(self, temp_dir):
        """Test parsing a large text file."""
        txt_file = temp_dir / "large.txt"
        # Create a file with 10000 lines
        content = "\n".join(f"Line {i}" for i in range(10000))
        txt_file.write_text(content)
        
        parser = TextFileParser()
        result = parser.parse_file(str(txt_file))
        
        assert result is not None
    
    def test_parse_malformed_json(self, temp_dir):
        """Test parsing malformed JSON."""
        json_file = temp_dir / "malformed.json"
        json_file.write_text('{"incomplete": ')
        
        parser = TextFileParser()
        # Should handle gracefully (may return None or parse as text)
        result = parser.parse_file(str(json_file))
        # Parser should handle gracefully
        assert result is None or isinstance(result, dict)
    
    def test_parse_file_with_special_characters(self, temp_dir):
        """Test parsing file with special characters."""
        txt_file = temp_dir / "special.txt"
        txt_file.write_text("""Special Characters: !@#$%^&*()
Quotes: "double" and 'single'
Symbols: © ® ™ € £ ¥
Newlines:
Line1

Line3
""")
        
        parser = TextFileParser()
        result = parser.parse_file(str(txt_file))
        
        assert result is not None
    
    def test_parse_json_content_extracted(self, temp_dir):
        """Parsed JSON results contain a documents entry with file content."""
        json_file = temp_dir / "data.json"
        json_file.write_text('{"name": "apollo", "version": "1.0.0"}')

        parser = TextFileParser()
        result = parser.parse_file(str(json_file))

        assert result is not None
        docs = result.get("documents", [])
        assert len(docs) == 1
        assert docs[0]["doc_type"] == "json"
        assert "apollo" in docs[0]["content"]
