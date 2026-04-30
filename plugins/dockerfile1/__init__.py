"""plugins.dockerfile1 — Apollo plugin."""
from .parser import DockerfileParser

PLUGIN = DockerfileParser

__all__ = ["DockerfileParser", "PLUGIN"]
