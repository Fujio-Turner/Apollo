"""
Apollo language plugins.

Each plugin lives under ``plugins/`` and exposes a ``PLUGIN`` attribute
pointing to a class that subclasses ``apollo.parser.base.BaseParser``.

Two layouts are supported, both discovered automatically:

1. **Subpackage style (recommended, self-contained)**::

       plugins/<name>/
       ├── __init__.py     ← exports PLUGIN
       ├── parser.py       ← the BaseParser subclass
       └── ...             ← any helper modules / vendored code

   The ``__init__.py`` does ``from .parser import MyParser`` and then
   ``PLUGIN = MyParser``. This keeps everything one plugin needs in
   exactly one folder. See ``plugins/python3/`` and
   ``plugins/markdown_gfm/`` for working examples.

2. **Single-file style (fine for trivial plugins)**::

       plugins/<name>.py   ← defines the parser and assigns ``PLUGIN``

To add support for a new language, drop a new folder (or file) here
following the conventions documented in ``guides/making_plugins.md``.

Use :func:`discover_plugins` to instantiate every plugin found in this
package. The order of returned plugins is alphabetical by name; if
ordering matters for your use case (e.g. tree-sitter before AST), pass
an explicit list to ``GraphBuilder`` instead.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Iterator

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# Names that look like plugins to ``pkgutil.iter_modules`` but aren't.
_NON_PLUGIN_NAMES: frozenset[str] = frozenset()


def iter_plugin_modules() -> Iterator[str]:
    """Yield the dotted module name of every plugin in this package.

    Both single-file plugins (``plugins/foo.py``) and subpackage plugins
    (``plugins/foo/__init__.py``) are returned. Names starting with an
    underscore are skipped so internal helpers can live alongside.
    """
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        if info.name in _NON_PLUGIN_NAMES:
            continue
        # ``info.ispkg`` is True for subpackages — we want both kinds.
        yield f"{__name__}.{info.name}"


def _accepts_config_kwarg(plugin_cls: type) -> bool:
    """Return ``True`` if ``plugin_cls.__init__`` accepts a ``config`` kwarg.

    We use this so old plugins (whose ``__init__`` takes no arguments)
    keep working without modification, while new plugins can opt in to
    receiving their merged config dict.
    """
    try:
        sig = inspect.signature(plugin_cls.__init__)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    if "config" in params:
        return True
    # ``**kwargs`` is also acceptable since the plugin can pluck config out.
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def discover_plugins() -> list[BaseParser]:
    """Import every plugin module and instantiate its ``PLUGIN`` class.

    Returns a list of fresh ``BaseParser`` instances, one per plugin.
    Modules without a valid ``PLUGIN`` attribute are skipped silently.

    Each plugin is given its **merged config** (see
    :func:`apollo.projects.settings.load_plugin_config`) at construction
    time when the constructor accepts a ``config`` kwarg. Plugins
    whose merged config has ``"enabled": False`` are skipped entirely
    so they never appear in the parser list and their ignore set is
    not contributed to the indexer.
    """
    # Local import to avoid a circular dependency: apollo.projects depends
    # on this package being importable.
    from apollo.projects.settings import load_plugin_config

    parsers: list[BaseParser] = []
    for module_name in iter_plugin_modules():
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            logger.exception("failed to import plugin module %s", module_name)
            continue
        plugin_cls = getattr(mod, "PLUGIN", None)
        if plugin_cls is None:
            continue

        # Plugin name is the last component of the module path.
        short_name = module_name.rsplit(".", 1)[-1]

        try:
            merged_config = load_plugin_config(short_name)
        except Exception:
            logger.exception("failed to load config for plugin %s", short_name)
            merged_config = {}

        # Skip plugins explicitly disabled in config or user overrides.
        if merged_config.get("enabled") is False:
            logger.info("plugin %r disabled via config; skipping", short_name)
            continue

        try:
            if _accepts_config_kwarg(plugin_cls):
                instance = plugin_cls(config=merged_config)
            else:
                instance = plugin_cls()
        except Exception:
            logger.exception("failed to instantiate plugin %s", short_name)
            continue
        if isinstance(instance, BaseParser):
            parsers.append(instance)
    return parsers


__all__ = ["discover_plugins", "iter_plugin_modules"]
