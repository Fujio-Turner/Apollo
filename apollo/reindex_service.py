"""
Background reindex service for periodic graph freshness.

Implements Phase D (Background Sweep) and Phase E (Telemetry) of the
incremental re-indexing system.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from graph.incremental import (
    FullBuildStrategy,
    ResolveFullStrategy,
    ResolveLocalStrategy,
    ReindexStats,
)

logger = logging.getLogger(__name__)

# Filenames for reindex state. The actual paths are resolved per-project
# under ``<root_dir>/_apollo/`` (see ``ReindexService._state_dir``); we
# keep just the basenames here so a single Apollo install can switch
# between projects without one project's hashes leaking into another's
# next sweep (which would silently skip files that look "unchanged" but
# weren't actually scanned for that project).
REINDEX_HISTORY_FILENAME = "reindex_history.json"
FILE_HASHES_FILENAME = "file_hashes.json"
MAX_HISTORY_ENTRIES = 100

# Legacy/global locations we still read from on first load so users
# upgrading from an earlier Apollo don't lose their history when the
# service migrates to per-project state. The data is *not* written
# back to these locations — the next save lands under ``_apollo/``.
_LEGACY_HISTORY_PATHS = [Path(".apollo/reindex_history.json")]
_LEGACY_HASHES_PATHS = [
    Path(".apollo/file_hashes.json"),
    Path("data/file_hashes.json"),
]


@dataclass
class ReindexConfig:
    """Configuration for reindex strategies."""
    strategy: str = "auto"  # "auto" | "full" | "resolve_full" | "resolve_local"
    sweep_interval_minutes: int = 30
    sweep_on_session_start: bool = True
    local_max_hops: int = 1
    force_full_after_runs: int = 50  # Safety: do full rebuild every Nth incremental


class ReindexService:
    """Service for managing background and incremental reindex operations."""
    
    def __init__(self, root_dir: str, store, config: Optional[ReindexConfig] = None):
        """
        Initialize the reindex service.
        
        Args:
            root_dir: Root directory of the indexed project
            store: GraphStore instance (JSON or CBL)
            config: ReindexConfig with strategy and timing options
        """
        self.root_dir = root_dir
        self.store = store
        self.config = config or ReindexConfig()
        self.reindex_history: list[ReindexStats] = []
        self._background_task: Optional[asyncio.Task] = None
        self._is_reindexing = False
        
        # Load history from disk
        self._load_history()
    
    def _state_dir(self) -> Path:
        """Return the per-project ``_apollo/`` directory the reindex
        service writes its hashes/history to. Always rooted at
        ``self.root_dir`` so opening a different project doesn't read
        the previous project's hash table (which would silently skip
        files that genuinely differ between the two trees)."""
        return Path(self.root_dir) / "_apollo"

    def _history_path(self) -> Path:
        return self._state_dir() / REINDEX_HISTORY_FILENAME

    def _hashes_path(self) -> Path:
        return self._state_dir() / FILE_HASHES_FILENAME

    def _load_history(self) -> None:
        """Load reindex history from disk.

        Reads the per-project file under ``<root>/_apollo/`` first; if
        that doesn't exist yet, falls back to legacy global locations
        so first-run after upgrade still surfaces prior telemetry. The
        legacy file is *not* deleted — the next ``_save_history()`` just
        writes the per-project copy and lets the legacy file age out.
        """
        candidates = [self._history_path()] + _LEGACY_HISTORY_PATHS
        for path in candidates:
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    self.reindex_history = [
                        ReindexStats.from_dict(entry) for entry in data
                    ][-MAX_HISTORY_ENTRIES:]  # Keep only last N
                    return
                except (json.JSONDecodeError, OSError, AttributeError):
                    continue
        self.reindex_history = []

    def _save_history(self) -> None:
        """Save reindex history to disk under ``<root>/_apollo/``."""
        path = self._history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(stats) for stats in self.reindex_history[-MAX_HISTORY_ENTRIES:]]
        path.write_text(json.dumps(data, indent=2, default=str))
    
    def get_last_stats(self) -> Optional[ReindexStats]:
        """Get the most recent reindex statistics."""
        return self.reindex_history[-1] if self.reindex_history else None
    
    def get_history(self, limit: int = 20) -> list[ReindexStats]:
        """Get the last N reindex runs."""
        return self.reindex_history[-limit:]
    
    def is_reindexing(self) -> bool:
        """Check if a reindex operation is currently running."""
        return self._is_reindexing
    
    async def start_background_sweep(self, delay_seconds: float = 5.0) -> None:
        """
        Start a background sweep task.
        
        Runs on session startup with a delay to avoid blocking app initialization.
        Then runs periodically every sweep_interval_minutes.
        
        Args:
            delay_seconds: Delay before first sweep (allows app to settle)
        """
        if self._background_task and not self._background_task.done():
            logger.info("Background sweep already running")
            return
        
        async def _sweep_loop():
            # Initial delay
            await asyncio.sleep(delay_seconds)
            
            while True:
                try:
                    logger.info(f"Starting background sweep (every {self.config.sweep_interval_minutes} min)")
                    await self.run_sweep()
                except Exception as e:
                    logger.error(f"Background sweep failed: {e}", exc_info=True)
                
                # Wait for next sweep
                await asyncio.sleep(self.config.sweep_interval_minutes * 60)
        
        self._background_task = asyncio.create_task(_sweep_loop())
        logger.info(f"Background sweep scheduled (first in {delay_seconds}s, every {self.config.sweep_interval_minutes}m)")
    
    async def run_sweep(self) -> ReindexStats:
        """
        Run a full background sweep using ResolveFullStrategy.
        
        This ensures any edge rot from fast incremental strategies is cleaned up.
        
        Returns:
            ReindexStats from the sweep run
        """
        if self._is_reindexing:
            logger.warning("Reindex already in progress, skipping sweep")
            return self.get_last_stats()
        
        self._is_reindexing = True
        try:
            import networkx as nx
            
            # Load current graph
            graph_in = self.store.load(include_embeddings=False)
            
            # Run full sweep strategy
            strategy = ResolveFullStrategy()
            result = strategy.run(
                root_dir=self.root_dir,
                graph_in=graph_in,
                prev_hashes=self._load_prev_hashes(),
            )
            
            # Save results
            self.store.save(result.graph_out)
            self._save_prev_hashes(result.new_hashes)
            self.reindex_history.append(result.stats)
            self._save_history()
            
            logger.info(
                f"Background sweep complete: "
                f"{result.stats.duration_ms}ms, "
                f"{result.stats.files_parsed} files parsed, "
                f"+{result.stats.edges_added} -{result.stats.edges_removed} edges"
            )
            
            return result.stats
        
        finally:
            self._is_reindexing = False
    
    def _load_prev_hashes(self) -> dict[str, dict]:
        """Load file hashes from previous run.

        Reads ``<root>/_apollo/file_hashes.json`` first; falls back to
        the legacy global locations so users upgrading from an earlier
        Apollo don't pay a full rebuild on the first sweep after the
        per-project migration.
        """
        candidates = [self._hashes_path()] + _LEGACY_HASHES_PATHS
        for path in candidates:
            if path.exists():
                try:
                    return json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
        return {}

    def _save_prev_hashes(self, hashes: dict[str, dict]) -> None:
        """Save file hashes for next run under ``<root>/_apollo/``."""
        path = self._hashes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hashes, indent=2, default=str))
