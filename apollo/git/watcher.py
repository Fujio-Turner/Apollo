# SPDX-License-Identifier: BUSL-1.1
"""Watch ``.git/HEAD`` for branch checkouts.

When the user runs ``git checkout <other-branch>`` the working tree
swaps out underneath Apollo. We rely on the per-branch store layout
defined in :mod:`apollo.git.branch` to keep each branch's index
isolated; this watcher is the trigger that tells the web server to
swap to the right store as soon as the branch changes.

Design notes
------------
* We deliberately do **not** depend on GitPython or shell out to
  ``git``. ``HEAD`` is a tiny text file and reading it directly keeps
  the dependency surface (and test setup) trivial.
* ``watchdog.Observer`` is already a runtime dependency (file
  watcher), so reusing it costs nothing.
* The watcher polls/observes the parent directory of ``HEAD`` rather
  than ``HEAD`` itself because some git commands replace the file
  atomically (``rename`` over the top), which can confuse a
  file-level subscription on some platforms.
* The callback fires on a watchdog thread; the caller is responsible
  for marshalling back to its own thread/event loop if needed (the
  web server schedules a store swap, which is thread-safe because
  it acquires the store lock).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from .branch import current_branch, head_watch_path

logger = logging.getLogger(__name__)

# How long to wait after an event before re-reading HEAD. git itself
# does HEAD updates as multi-step writes (e.g. lock file → rename) so
# we collapse a burst into one re-read.
_DEBOUNCE_SECONDS = 0.25


class BranchWatcher:
    """Watch the active project's ``.git/HEAD`` and fire on branch swap.

    ``on_branch_change`` is invoked with the new branch name (string)
    whenever HEAD resolves to a *different* branch than the last one
    we observed. Identical-branch events (e.g. ``git commit`` updates
    HEAD's SHA but not the branch) are intentionally suppressed so
    the caller doesn't trigger a spurious store swap.
    """

    def __init__(
        self,
        root_dir: str | Path,
        on_branch_change: Callable[[str], None],
    ) -> None:
        self.root = Path(root_dir).resolve()
        self.on_branch_change = on_branch_change
        self._observer = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._last_branch: Optional[str] = current_branch(self.root)
        self._head_path: Optional[Path] = head_watch_path(self.root)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_branch(self) -> Optional[str]:
        return self._last_branch

    def start(self) -> None:
        """Begin watching ``HEAD``. No-op for non-git folders."""
        if self._head_path is None:
            logger.debug("BranchWatcher: %s is not a git checkout; not starting", self.root)
            return

        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self
        target_name = self._head_path.name

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # noqa: D401 — watchdog interface
                # Watchdog reports moves/renames separately; the easiest
                # cover-all is "if any event names HEAD, schedule a
                # re-read". Filter on basename so unrelated files in
                # ``.git/`` (objects/, logs/, …) don't wake us up.
                src = getattr(event, "src_path", "") or ""
                dest = getattr(event, "dest_path", "") or ""
                if Path(src).name == target_name or Path(dest).name == target_name:
                    watcher._schedule_check()

        observer = Observer()
        # Subscribe to the directory containing HEAD (non-recursive) so
        # ref-pack rewrites and atomic renames are both caught.
        observer.schedule(_Handler(), str(self._head_path.parent), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        self._running = True
        logger.info("BranchWatcher started for %s (HEAD=%s, branch=%s)",
                    self.root, self._head_path, self._last_branch)

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                logger.exception("BranchWatcher: error stopping observer")
            self._observer = None
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()
            self._debounce_timer = None
        self._running = False

    # -- internals -----------------------------------------------------

    def _schedule_check(self) -> None:
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(_DEBOUNCE_SECONDS, self._check_branch)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _check_branch(self) -> None:
        new_branch = current_branch(self.root)
        if new_branch is None:
            # Repo became non-git mid-flight (e.g. ``rm -rf .git``).
            # Nothing useful we can do; leave the last branch alone.
            return
        if new_branch == self._last_branch:
            return
        old = self._last_branch
        self._last_branch = new_branch
        logger.info("BranchWatcher: branch changed %s -> %s", old, new_branch)
        try:
            self.on_branch_change(new_branch)
        except Exception:
            logger.exception("BranchWatcher: on_branch_change callback raised")
