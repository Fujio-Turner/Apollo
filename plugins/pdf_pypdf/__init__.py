"""
PDF plugin package (powered by `pypdf`).

Self-contained subpackage style:

    plugins/pdf_pypdf/
    ├── __init__.py      ← this file: re-exports the parser and PLUGIN
    ├── parser.py        ← the BaseParser implementation
    └── requirements.txt ← third-party deps (pypdf)

Add helper modules (extra extractors, vendored support code, etc.)
alongside ``parser.py`` and import them from there. Everything the
plugin needs lives inside this folder.

Install the runtime dependency with::

    pip install -r plugins/pdf_pypdf/requirements.txt

If ``pypdf`` is not importable, the plugin self-disables: ``can_parse``
returns ``False`` and Apollo falls back to its generic text indexer.
"""
from .parser import PdfParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = PdfParser

__all__ = ["PdfParser", "PLUGIN"]
