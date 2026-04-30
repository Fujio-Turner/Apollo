"""plugins.docker_compose1 — Apollo plugin."""
from .parser import DockerComposeParser

PLUGIN = DockerComposeParser

__all__ = ["DockerComposeParser", "PLUGIN"]
