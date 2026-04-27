"""ProjectInfo data structure for API responses."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
from .manifest import ProjectManifest, ProjectFilters, ProjectStats


@dataclass
class ProjectInfo:
    """API response structure containing manifest + derived fields."""
    project_id: str
    root_dir: str
    created_at: str
    created_by_version: str
    last_opened_at: Optional[str]
    last_opened_by_version: Optional[str]
    last_indexed_at: Optional[str]
    last_indexed_by_version: Optional[str]
    initial_index_completed: bool
    needs_bootstrap: bool
    filters: dict
    stats: Optional[dict] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON responses."""
        return asdict(self)

    @classmethod
    def from_manifest(cls, manifest: ProjectManifest) -> "ProjectInfo":
        """Create from ProjectManifest."""
        return cls(
            project_id=manifest.project_id,
            root_dir=manifest.root_dir,
            created_at=manifest.created_at,
            created_by_version=manifest.created_by_version,
            last_opened_at=manifest.last_opened_at,
            last_opened_by_version=manifest.last_opened_by_version,
            last_indexed_at=manifest.last_indexed_at,
            last_indexed_by_version=manifest.last_indexed_by_version,
            initial_index_completed=manifest.initial_index_completed,
            needs_bootstrap=not manifest.initial_index_completed,
            filters=manifest.filters.to_dict() if manifest.filters else {},
            stats=manifest.stats.to_dict() if manifest.stats else None,
        )
