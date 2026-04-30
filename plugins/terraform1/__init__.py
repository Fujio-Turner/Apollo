"""Terraform plugin package for Apollo."""
from .parser import TerraformParser

PLUGIN = TerraformParser

__all__ = ["TerraformParser", "PLUGIN"]
