"""Git-aware helpers for Apollo.

This subpackage exists so the rest of the codebase can stay
git-agnostic and we keep all the "what branch are we on?" / "where is
HEAD?" logic in one place. See ``branch.py`` for the path-naming rules
(branch-keyed stores, §1 + §2 of the project-switch design) and
``watcher.py`` for the ``.git/HEAD`` watcher that triggers a store
swap when the user runs ``git checkout``.
"""

from .branch import (
    branched_path,
    current_branch,
    head_watch_path,
    safe_branch_suffix,
)

__all__ = [
    "branched_path",
    "current_branch",
    "head_watch_path",
    "safe_branch_suffix",
]
