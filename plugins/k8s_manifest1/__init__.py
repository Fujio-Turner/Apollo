"""Kubernetes manifest plugin package for Apollo."""
from .parser import K8sManifestParser

PLUGIN = K8sManifestParser

__all__ = ["K8sManifestParser", "PLUGIN"]
