# SPDX-License-Identifier: BUSL-1.1
"""Tests for ``apollo.git.branch`` — branch-aware path helpers.

These tests intentionally avoid shelling out to ``git`` so they work
on CI runners without git installed; they fake out a ``.git/HEAD``
text file (and an optional gitdir indirection for worktrees) which is
exactly what :func:`apollo.git.branch.current_branch` reads.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from apollo.git.branch import (
    branched_path,
    current_branch,
    head_watch_path,
    safe_branch_suffix,
)


def _make_repo(root: Path, head_contents: str) -> None:
    """Write a minimal ``.git/HEAD`` for tests."""
    git = root / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(head_contents, encoding="utf-8")


class TestCurrentBranch:
    def test_returns_none_for_non_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert current_branch(tmp) is None

    def test_returns_branch_from_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/issue/17\n")
            assert current_branch(tmp) == "issue/17"

    def test_returns_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/main\n")
            assert current_branch(tmp) == "main"

    def test_detached_head_uses_sha_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            sha = "abcdef0123456789" * 2  # 32 hex chars
            _make_repo(Path(tmp), sha + "\n")
            branch = current_branch(tmp)
            assert branch is not None
            assert branch.startswith("detached_")
            # First 12 chars of the SHA must be in the suffix.
            assert sha[:12] in branch

    def test_worktree_gitdir_indirection(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_root = Path(tmp) / "real"
            worktree = Path(tmp) / "wt"
            real_root.mkdir()
            worktree.mkdir()
            # Real gitdir for the worktree.
            real_gitdir = real_root / ".git" / "worktrees" / "wt"
            real_gitdir.mkdir(parents=True)
            (real_gitdir / "HEAD").write_text("ref: refs/heads/feature\n")
            # Worktree's .git is a *file* pointing at the gitdir.
            (worktree / ".git").write_text(f"gitdir: {real_gitdir}\n")

            assert current_branch(worktree) == "feature"


class TestSafeBranchSuffix:
    def test_slash_becomes_double_underscore(self):
        assert safe_branch_suffix("issue/17") == "issue__17"

    def test_simple_branch_unchanged(self):
        assert safe_branch_suffix("main") == "main"
        assert safe_branch_suffix("feature-x") == "feature-x"
        assert safe_branch_suffix("v1.2.3") == "v1.2.3"

    def test_unsafe_chars_collapse(self):
        assert safe_branch_suffix("hot fix?!") == "hot_fix__"

    def test_leading_dots_stripped(self):
        assert safe_branch_suffix(".hidden") == "hidden"

    def test_empty_returns_empty(self):
        assert safe_branch_suffix("") == ""


class TestBranchedPath:
    def test_non_git_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "_apollo" / "graph.json"
            assert branched_path(base, tmp) == base

    def test_inserts_branch_before_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/issue/17\n")
            base = Path(tmp) / "_apollo" / "graph.json"
            result = branched_path(base, tmp)
            assert result.name == "graph__issue__17.json"
            assert result.parent == base.parent

    def test_inserts_branch_for_cblite(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/main\n")
            base = Path(tmp) / "_apollo" / "cblite" / "apollo_abc.cblite2"
            result = branched_path(base, tmp)
            assert result.name == "apollo_abc__main.cblite2"

    def test_different_branches_yield_different_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/main\n")
            base = Path(tmp) / "_apollo" / "graph.json"
            a = branched_path(base, tmp)
            _make_repo(Path(tmp), "ref: refs/heads/feature\n")
            b = branched_path(base, tmp)
            assert a != b
            assert a.name == "graph__main.json"
            assert b.name == "graph__feature.json"


class TestHeadWatchPath:
    def test_none_for_non_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert head_watch_path(tmp) is None

    def test_returns_head_for_regular_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/main\n")
            p = head_watch_path(tmp)
            assert p is not None
            assert p.name == "HEAD"
            assert p.parent.name == ".git"


class TestProjectManagerBranchedPaths:
    """Integration: ProjectManager.resolve_cbl_path honours branch."""

    def test_cbl_path_includes_branch(self):
        from apollo.projects.manager import ProjectManager

        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(Path(tmp), "ref: refs/heads/issue/17\n")
            mgr = ProjectManager("test-version", default_backend="cblite")
            mgr.init(tmp, backend="cblite")
            # Resolve via the private helper used by reprocess/swap.
            resolved = mgr._resolve_cbl_path(mgr.manifest)
            assert resolved is not None
            assert resolved.name.endswith("__issue__17.cblite2")


class TestBranchWatcher:
    """The watcher uses watchdog under the hood; we use a small sleep
    plus polling to keep the test deterministic without abusing
    threading primitives."""

    def test_fires_on_branch_change(self):
        from apollo.git.watcher import BranchWatcher

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "ref: refs/heads/main\n")

            event = threading.Event()
            captured: list[str] = []

            def cb(branch: str) -> None:
                captured.append(branch)
                event.set()

            watcher = BranchWatcher(root, cb)
            watcher.start()
            try:
                assert watcher.running is True
                # Simulate a checkout by rewriting HEAD.
                (root / ".git" / "HEAD").write_text("ref: refs/heads/feature\n")
                # Wait up to 2 seconds for the debounced callback.
                assert event.wait(timeout=2.0), "BranchWatcher did not fire"
                assert captured == ["feature"]
            finally:
                watcher.stop()
