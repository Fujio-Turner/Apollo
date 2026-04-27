---
name: pdf_pypdf
description: PDF plugin for Apollo, powered by the `pypdf` library. Extracts page text from `.pdf` files and self-disables (falls back to the generic text indexer) when `pypdf` is not importable.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/pdf_pypdf
author: Fujio Turner
---

# PDF Plugin (pypdf)

Built-in reference plugin. Targets `.pdf` files via the third-party
`pypdf` package — install with
`pip install -r plugins/pdf_pypdf/requirements.txt`.

This file is the plugin's **manifest**. The header above (everything
between the `---` markers) is parsed by Apollo to populate the
**Settings → Plugins** tab. The body below is free-form documentation.
