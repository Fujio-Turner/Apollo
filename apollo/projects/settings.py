"""Global settings management for Apollo."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


@dataclass
class RecentProject:
    """Entry in the recent projects list."""
    path: str
    project_id: str
    last_opened_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RecentProject":
        return cls(**data)


@dataclass
class SettingsData:
    """Global Apollo settings."""
    chat: dict = field(default_factory=lambda: {"default_model": "grok-4-1-fast-non-reasoning"})
    default_backend: str = "json"  # "json" or "cblite"
    cblite_storage_root: Optional[str] = None  # Optional path for global CBL storage
    recent_projects: list[RecentProject] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["recent_projects"] = [p.to_dict() for p in self.recent_projects]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SettingsData":
        recent = [RecentProject.from_dict(p) for p in data.pop("recent_projects", [])]
        return cls(recent_projects=recent, **data)


class SettingsManager:
    """Manages global Apollo settings (data/settings.json)."""

    def __init__(self, settings_path: Optional[Union[str, Path]] = None):
        """Initialize settings manager.
        
        Args:
            settings_path: Path to settings.json. Defaults to data/settings.json in Apollo root.
        """
        if settings_path is None:
            # Default to data/settings.json
            root = Path(__file__).parent.parent.parent  # Project root
            settings_path = root / "data" / "settings.json"
        
        self.path = Path(settings_path)
        self._data = self._load()

    def _load(self) -> SettingsData:
        """Load settings from disk or create defaults."""
        if self.path.exists():
            try:
                with open(self.path) as f:
                    data = json.load(f)
                return SettingsData.from_dict(data)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Failed to load settings: {e}. Using defaults.")
        
        return SettingsData()

    def save(self) -> None:
        """Persist settings to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data.to_dict(), f, indent=2)

    @property
    def data(self) -> SettingsData:
        """Get current settings."""
        return self._data

    def add_recent_project(self, path: Union[str, Path], project_id: str) -> None:
        """Add or update a project in the recent projects list.
        
        Keeps the list to a maximum of 10 entries, with most recently opened first.
        """
        path = str(Path(path).resolve())
        
        # Remove if already exists (will re-add to front)
        self._data.recent_projects = [
            p for p in self._data.recent_projects if p.path != path
        ]
        
        # Add to front with current timestamp
        recent = RecentProject(
            path=path,
            project_id=project_id,
            last_opened_at=datetime.utcnow().isoformat() + "Z",
        )
        self._data.recent_projects.insert(0, recent)
        
        # Trim to 10 entries
        self._data.recent_projects = self._data.recent_projects[:10]
        
        self.save()

    def remove_recent_project(self, path: Union[str, Path]) -> None:
        """Remove a project from recent projects."""
        path = str(Path(path).resolve())
        self._data.recent_projects = [
            p for p in self._data.recent_projects if p.path != path
        ]
        self.save()

    def set_default_backend(self, backend: str) -> None:
        """Set the default storage backend for new projects."""
        if backend not in ("json", "cblite"):
            raise ValueError(f"Unknown backend: {backend}")
        self._data.default_backend = backend
        self.save()

    def set_cblite_storage_root(self, path: Optional[Union[str, Path]]) -> None:
        """Set the global CBL storage root (for global mode)."""
        if path:
            self._data.cblite_storage_root = str(Path(path).resolve())
        else:
            self._data.cblite_storage_root = None
        self.save()
