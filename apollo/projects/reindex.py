"""Reindex orchestration and telemetry for projects."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from apollo.graph.incremental import IncrementalResult, ReindexStats
from apollo.graph.reindex_config import ReindexConfig


class ReindexHistory:
    """Manages reindex statistics history (last 100 runs)."""
    
    MAX_HISTORY_SIZE = 100
    
    def __init__(self, project_root: Path):
        """Initialize history manager.
        
        Args:
            project_root: Project root directory (where _apollo/ lives)
        """
        self.project_root = Path(project_root)
        self.history_file = self.project_root / "_apollo" / "reindex_history.json"
    
    def load(self) -> list[ReindexStats]:
        """Load reindex history from disk."""
        if not self.history_file.exists():
            return []
        
        try:
            with open(self.history_file) as f:
                data = json.load(f)
            return [ReindexStats.from_dict(item) for item in data]
        except (json.JSONDecodeError, ValueError):
            return []
    
    def append(self, stats: ReindexStats) -> None:
        """Add a reindex run to history and persist."""
        history = self.load()
        
        # Add new run
        history.append(stats)
        
        # Keep only last MAX_HISTORY_SIZE
        if len(history) > self.MAX_HISTORY_SIZE:
            history = history[-self.MAX_HISTORY_SIZE:]
        
        # Save to disk
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, "w") as f:
            json.dump([h.to_dict() for h in history], f, indent=2)
    
    def get_last(self) -> Optional[ReindexStats]:
        """Get the most recent reindex stats."""
        history = self.load()
        return history[-1] if history else None
    
    def get_summary(self) -> dict:
        """Get summary statistics of recent runs."""
        history = self.load()
        
        if not history:
            return {
                "total_runs": 0,
                "avg_duration_ms": 0,
                "total_files_indexed": 0,
            }
        
        return {
            "total_runs": len(history),
            "avg_duration_ms": sum(h.duration_ms for h in history) / len(history),
            "total_files_indexed": sum(h.files_total for h in history),
            "total_edges_added": sum(h.edges_added for h in history),
            "total_edges_removed": sum(h.edges_removed for h in history),
            "strategies": list(set(h.strategy for h in history)),
        }


class ReindexOrchestrator:
    """Orchestrates reindex runs using swappable strategies."""
    
    def __init__(
        self,
        project_root: str | Path,
        config: Optional[ReindexConfig] = None,
    ):
        """Initialize orchestrator.
        
        Args:
            project_root: Project root directory
            config: ReindexConfig (uses defaults if not provided)
        """
        self.project_root = Path(project_root)
        self.config = config or ReindexConfig()
        self.config.validate()
        self.history = ReindexHistory(self.project_root)
    
    def get_effective_strategy(self, is_foreground: bool = True) -> str:
        """Get the effective strategy for this context."""
        return self.config.get_effective_strategy(is_foreground)
    
    def record_run(self, result: IncrementalResult) -> None:
        """Record a reindex run in history."""
        self.history.append(result.stats)
    
    def should_force_full_rebuild(self) -> bool:
        """Check if a full rebuild should be forced based on run count."""
        history = self.history.load()
        
        # Count recent incremental runs (not "full" strategy)
        recent_incremental = sum(
            1 for s in history[-self.config.force_full_after_runs:]
            if s.strategy != "full"
        )
        
        return recent_incremental >= self.config.force_full_after_runs
    
    def get_last_reindex_info(self) -> dict:
        """Get info about the most recent reindex for UI display."""
        stats = self.history.get_last()
        
        if not stats:
            return {
                "has_run": False,
                "display": "No reindex yet",
            }
        
        duration_str = f"{stats.duration_ms}ms"
        changes_str = f"+{stats.edges_added}/-{stats.edges_removed} edges"
        files_str = f"{stats.files_parsed}/{stats.files_total} files"
        
        return {
            "has_run": True,
            "strategy": stats.strategy,
            "duration_ms": stats.duration_ms,
            "display": f"Last: {duration_str} ({files_str}, {changes_str})",
            "timestamp": datetime.fromtimestamp(stats.started_at).isoformat(),
        }
