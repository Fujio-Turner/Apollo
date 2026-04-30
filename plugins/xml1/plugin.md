---
name: xml1
description: XML file plugin for Apollo. Parses .xml files to extract elements, attributes, namespace declarations, and internal id/href references for document structure analysis.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/xml1
author: Fujio Turner
---

# XML 1 Plugin

Parser for XML files (`.xml`). Extracts:

- **Variables**: Root element, all element tags, id attributes, xmlns namespaces
- **Imports**: External hrefs, namespace URIs
- **Structure**: Element hierarchy, attributes, namespace declarations

Enables queries like:
- "Show all XML files with a `svg` root element"
- "Find all internal id references across XML documents"
- "What namespaces are declared?"
