"""Compatibility shim: expose top-level packages under the ``apollo`` namespace."""
import importlib
import importlib.abc
import importlib.machinery
import sys

_SUBPACKAGES = (
    "graph",
    "parser",
    "plugins",
    "storage",
    "embeddings",
    "spatial",
    "search",
    "web",
    "chat",
    "watcher",
    "file_inspect",
)

_PREFIX = __name__ + "."


class _ApolloAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        suffix = fullname[len(_PREFIX):]
        top = suffix.split(".", 1)[0]
        if top not in _SUBPACKAGES:
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec):
        target_name = spec.name[len(_PREFIX):]
        return importlib.import_module(target_name)

    def exec_module(self, module):
        return None


sys.meta_path.insert(0, _ApolloAliasFinder())
