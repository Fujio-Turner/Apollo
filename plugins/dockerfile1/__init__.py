# SPDX-License-Identifier: BUSL-1.1
"""plugins.dockerfile1 — Apollo plugin."""
from .parser import DockerfileParser

PLUGIN = DockerfileParser

__all__ = ["DockerfileParser", "PLUGIN"]
