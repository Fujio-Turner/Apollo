"""Branch-aware path helpers.

Apollo indexes a project once per *branch* (DESIGN §4.2.3). Switching
branches changes the file tree under us, but the per-project store
(``_apollo/cblite/apollo_<md5>.cblite2`` or ``_apollo/graph.json``) is
a single snapshot. The fix is to suffix every store path with the
current branch so each branch gets its own store and switching is
O(1) after the first index.

This module provides the small primitives:

* :func:`current_branch` — read the active branch from ``.git/HEAD``
  without shelling out (so tests work without git installed).
* :func:`safe_branch_suffix` — make a branch name safe for a filename
  (``feature/x`` → ``feature__x``).
* :func:`branched_path` — given a base path like
  ``_apollo/graph.json`` and a project root, return the branch-keyed
  variant (``_apollo/graph__feature__x.json``). Non-git projects pass
  through unchanged so opening a folder without ``.git/`` behaves
  exactly like before.
* :func:`head_watch_path` — locate the ``HEAD`` file the
  :class:`apollo.git.watcher.BranchWatcher` should watch (resolves
  ``.git`` files for git worktrees).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


def _resolve_git_dir(root: PathLike) -> Optional[Path]:
    """Return the directory holding ``HEAD`` for the repo at ``root``.

    For a regular checkout this is ``<root>/.git``. For a worktree
    ``<root>/.git`` is a *file* whose contents look like
    ``gitdir: /abs/path/to/.git/worktrees/<name>`` — we follow that so
    branch lookups (and the watcher) target the real HEAD file.

    Returns ``None`` if ``root`` is not inside a git checkout.
    """
    root = Path(root)
    git_path = root / ".git"
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        try:
            text = git_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        # ``gitdir: <path>`` per git-worktree(1).
        if text.startswith("gitdir:"):
            candidate = Path(text.split(":", 1)[1].strip())
            if not candidate.is_absolute():
                candidate = (root / candidate).resolve()
            if candidate.is_dir():
                return candidate
    return None


def current_branch(root: PathLike) -> Optional[str]:
    """Return the current branch name for the git checkout at ``root``.

    Returns ``None`` for non-git folders. For a detached HEAD, returns
    ``"detached_<short-sha>"`` so each detached state gets a stable
    distinct store path instead of colliding under ``"detached"``.
    """
    git_dir = _resolve_git_dir(root)
    if git_dir is None:
        return None

    head = git_dir / "HEAD"
    try:
        text = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None

    # Standard form: "ref: refs/heads/<branch>"
    if text.startswith("ref:"):
        ref = text.split(":", 1)[1].strip()
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            return ref[len(prefix):]
        # Other refs (e.g. tags) — keep the leaf name.
        return ref.rsplit("/", 1)[-1] or None

    # Detached HEAD — text is the raw commit SHA.
    sha = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]{4,40}", sha):
        return f"detached_{sha[:12]}"
    return None


# Anything not in this set gets collapsed to "_" in safe_branch_suffix so
# the result is always a portable filename component (no slashes,
# colons, spaces, …).
_SAFE_BRANCH_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def safe_branch_suffix(branch: str) -> str:
    """Make a branch name safe to embed in a filename.

    * ``/`` is replaced with ``__`` (the common case — ``feature/x``,
      ``issue/17`` — stay human-readable).
    * Anything else outside ``[A-Za-z0-9._-]`` collapses to ``_``.
    * Leading dots are stripped because filenames starting with ``.``
      get hidden on every platform we care about.
    """
    if not branch:
        return ""
    s = branch.replace("/", "__")
    s = _SAFE_BRANCH_CHARS.sub("_", s)
    return s.lstrip(".") or "_"


def branched_path(base: PathLike, root: PathLike) -> Path:
    """Return ``base`` with ``__<branch>`` inserted before its suffix.

    Examples (assume the repo at ``root`` is on branch ``issue/17``):

    * ``graph.json`` → ``graph__issue__17.json``
    * ``cblite/apollo_<md5>.cblite2`` →
      ``cblite/apollo_<md5>__issue__17.cblite2``

    Non-git projects (``current_branch`` returns ``None``) get
    ``base`` back unchanged so opening a non-git folder keeps the
    legacy single-store layout. Callers should treat the result as
    the authoritative on-disk path for the *current* branch.
    """
    base = Path(base)
    branch = current_branch(root)
    if not branch:
        return base
    suffix_safe = safe_branch_suffix(branch)
    if not suffix_safe:
        return base
    # ``Path.suffix`` gives the *last* extension. Inserting before it
    # keeps the rest of the name intact and works equally well for
    # ``graph.json`` and ``apollo_<md5>.cblite2``.
    stem_with_dirs = base.with_suffix("")
    return Path(f"{stem_with_dirs}__{suffix_safe}{base.suffix}")


def head_watch_path(root: PathLike) -> Optional[Path]:
    """Return the absolute path of the ``HEAD`` file to watch, if any.

    The :class:`apollo.git.watcher.BranchWatcher` uses this to know
    which file to subscribe to: a normal ``.git/HEAD``, or the
    worktree-specific HEAD reached through a ``.git`` gitdir pointer.
    Returns ``None`` when the project is not a git checkout.
    """
    git_dir = _resolve_git_dir(root)
    if git_dir is None:
        return None
    head = git_dir / "HEAD"
    return head if head.exists() else None
