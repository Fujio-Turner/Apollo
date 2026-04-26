"""Unit tests for MarkdownParser."""
import pytest

from graph_search.parser import MarkdownParser


class TestMarkdownParser:
    """Test cases for MarkdownParser."""
    
    def test_can_parse_markdown_file(self):
        """Test that MarkdownParser can parse .md files."""
        parser = MarkdownParser()
        assert parser.can_parse("README.md") is True
        assert parser.can_parse("docs/guide.markdown") is True
    
    def test_cannot_parse_other_files(self):
        """Test that MarkdownParser cannot parse non-markdown files."""
        parser = MarkdownParser()
        assert parser.can_parse("test.py") is False
        assert parser.can_parse("test.txt") is False
        assert parser.can_parse("test.js") is False
    
    def test_parse_simple_markdown(self, sample_markdown_file):
        """Test parsing a simple markdown file."""
        parser = MarkdownParser()
        result = parser.parse_file(str(sample_markdown_file))
        
        assert result is not None
        assert "file" in result
    
    def test_parse_nonexistent_file(self):
        """Test parsing a nonexistent markdown file."""
        parser = MarkdownParser()
        result = parser.parse_file("/nonexistent/file.md")
        assert result is None
    
    def test_parse_markdown_with_headings(self, temp_dir):
        """Headings are extracted as sections with titles."""
        md_file = temp_dir / "headings.md"
        md_file.write_text('''# Main Title
## Section One
### Subsection
## Section Two
#### Details
''')

        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))

        assert result is not None
        sections = result.get("sections", [])
        names = {s.get("name") for s in sections}
        assert "Main Title" in names
        assert "Section One" in names
        assert "Subsection" in names
        assert "Section Two" in names
        levels = {s.get("name"): s.get("level") for s in sections}
        assert levels["Main Title"] == 1
        assert levels["Subsection"] == 3
        assert result.get("title") == "Main Title"

    def test_parse_markdown_with_code_blocks(self, temp_dir):
        """Code blocks are extracted with their language tag."""
        md_file = temp_dir / "code_blocks.md"
        md_file.write_text('''# Code Examples

```python
def hello():
    print("Hello, World!")
```

```javascript
function hello() {
    console.log("Hello, World!");
}
```
''')

        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))

        assert result is not None
        code_blocks = result.get("code_blocks", [])
        assert len(code_blocks) == 2
        languages = {cb.get("language") or cb.get("lang") for cb in code_blocks}
        assert "python" in languages
        assert "javascript" in languages
    
    def test_parse_markdown_with_lists(self, temp_dir):
        """Test parsing markdown with lists."""
        md_file = temp_dir / "lists.md"
        md_file.write_text('''# Items

## Unordered List
- Item 1
- Item 2
  - Nested item
- Item 3

## Ordered List
1. First
2. Second
3. Third
''')
        
        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))
        
        assert result is not None
    
    def test_parse_markdown_with_links(self, temp_dir):
        """Links are extracted with url and text."""
        md_file = temp_dir / "links.md"
        md_file.write_text('''# References

[Link Text](https://example.com)
[Internal Link](./other.md)
''')

        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))

        assert result is not None
        links = result.get("links", [])
        urls = {l.get("url") for l in links}
        assert "https://example.com" in urls
        assert "./other.md" in urls
    
    def test_parse_markdown_with_frontmatter(self, temp_dir):
        """YAML frontmatter is extracted into the result."""
        md_file = temp_dir / "frontmatter.md"
        md_file.write_text('''---
title: My Document
author: Test Author
date: 2024-01-01
---

# Content

This is the content after frontmatter.
''')

        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))

        assert result is not None
        fm = result.get("frontmatter")
        assert fm is not None
        assert fm.get("title") == "My Document"
        assert fm.get("author") == "Test Author"

    def test_parse_markdown_with_tables(self, temp_dir):
        """Tables are extracted with headers and rows."""
        md_file = temp_dir / "tables.md"
        md_file.write_text('''# Data Table

| Name | Age | City |
|------|-----|------|
| Alice | 30 | NYC |
| Bob | 25 | LA |
''')

        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))

        assert result is not None
        tables = result.get("tables", [])
        assert len(tables) == 1
        assert tables[0]["headers"] == ["Name", "Age", "City"]
        assert ["Alice", "30", "NYC"] in tables[0]["rows"]
        assert ["Bob", "25", "LA"] in tables[0]["rows"]
    
    def test_parse_markdown_with_images(self, temp_dir):
        """Test parsing markdown with images."""
        md_file = temp_dir / "images.md"
        md_file.write_text('''# Images

![Alt Text](./image.png)
![Another](https://example.com/pic.jpg)
''')
        
        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))
        
        assert result is not None


class TestMarkdownParserEdgeCases:
    """Test edge cases for MarkdownParser."""
    
    def test_parse_empty_markdown(self, temp_dir):
        """Empty markdown returns None."""
        md_file = temp_dir / "empty.md"
        md_file.write_text("")

        parser = MarkdownParser()
        assert parser.parse_file(str(md_file)) is None

    def test_parse_markdown_only_whitespace(self, temp_dir):
        """Whitespace-only markdown returns None."""
        md_file = temp_dir / "whitespace.md"
        md_file.write_text("\n\n   \n\t\n")

        parser = MarkdownParser()
        assert parser.parse_file(str(md_file)) is None
    
    def test_parse_markdown_with_unicode(self, temp_dir):
        """Test parsing markdown with unicode characters."""
        md_file = temp_dir / "unicode.md"
        md_file.write_text('''# 中文标题

这是一个包含unicode字符的文件。

## 日本語
これはテストです。

## Emoji
😀 🎉 ✨
''')
        
        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))
        
        assert result is not None
    
    def test_parse_markdown_with_html(self, temp_dir):
        """Test parsing markdown with embedded HTML."""
        md_file = temp_dir / "html.md"
        md_file.write_text('''# HTML in Markdown

<div class="custom">
    <p>HTML content</p>
</div>

Regular markdown text.
''')
        
        parser = MarkdownParser()
        result = parser.parse_file(str(md_file))
        
        assert result is not None
