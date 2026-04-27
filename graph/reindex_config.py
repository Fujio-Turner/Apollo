"""Configuration for incremental re-indexing strategies and background sweeps."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class ReindexConfig:
    """Configuration for reindex behavior."""
    
    strategy: str = "auto"  # "auto" | "full" | "resolve_full" | "resolve_local"
    sweep_interval_minutes: int = 30
    sweep_on_session_start: bool = True
    local_max_hops: int = 1  # Dependency expansion depth for Option 2
    force_full_after_runs: int = 50  # Safety: every Nth run, do full
    
    # Validation
    def validate(self) -> None:
        """Validate configuration values."""
        valid_strategies = {"auto", "full", "resolve_full", "resolve_local"}
        if self.strategy not in valid_strategies:
            raise ValueError(f"Invalid strategy: {self.strategy}. Must be one of {valid_strategies}")
        
        if self.sweep_interval_minutes < 1:
            raise ValueError("sweep_interval_minutes must be >= 1")
        
        if self.local_max_hops < 1:
            raise ValueError("local_max_hops must be >= 1")
        
        if self.force_full_after_runs < 1:
            raise ValueError("force_full_after_runs must be >= 1")
    
    @classmethod
    def from_dict(cls, data: dict) -> ReindexConfig:
        """Create from dictionary (e.g., from config file)."""
        valid_fields = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in (data or {}).items() if k in valid_fields}
        return cls(**filtered)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def get_effective_strategy(self, is_foreground: bool = True) -> str:
        """Get the effective strategy based on config and context.
        
        Args:
            is_foreground: True for interactive updates, False for background sweep
        
        Returns:
            Concrete strategy name ("full", "resolve_full", or "resolve_local")
        """
        if self.strategy == "auto":
            if is_foreground:
                return "resolve_local"
            else:
                return "resolve_full"
        return self.strategy
