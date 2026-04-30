"""GitHub Actions plugin package for Apollo."""
from .parser import GitHubActionsParser

PLUGIN = GitHubActionsParser

__all__ = ["GitHubActionsParser", "PLUGIN"]
