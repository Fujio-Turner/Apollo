---
name: python3
description: Python 3 source-file plugin for Apollo. Parses .py files with the standard library `ast` module to extract functions, classes, methods, decorators, calls, complexity, exceptions, and framework patterns.
version: 1.0.0
url: https://github.com/fujio-turner/Apollo/tree/main/plugins/python3
author: Fujio Turner
---

# Python 3 Plugin

Built-in reference plugin. Targets Python 3 source files (`.py`) and
emits Apollo's standard parse-result dict — see
`guides/making_plugins.md` for the schema.

This file is the plugin's **manifest**. The header above (everything
between the `---` markers) is parsed by Apollo to populate the
**Settings → Plugins** tab. The body below is free-form documentation.
