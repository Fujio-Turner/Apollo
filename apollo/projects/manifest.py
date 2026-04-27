"""Project manifest data structures and persistence."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Union
import jsonschema


@dataclass
class ProjectStats:
    """Summary statistics from last completed index."""
    files_indexed: int = 0
    nodes: int = 0
    edges: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectStats":
        return cls(**data)


@dataclass
class ProjectFilters:
    """Project-level filtering configuration."""
    mode: str = "all"  # "all" or "custom"
    include_dirs: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    exclude_file_globs: list[str] = field(default_factory=list)
    include_doc_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectFilters":
        return cls(**data)


@dataclass
class ProjectStorage:
    """Storage configuration for the project's backend."""
    backend: str = "json"  # "json" or "cblite"
    db_hash: Optional[str] = None  # MD5 of origin_abspath (for cblite only)
    db_name: Optional[str] = None  # "apollo_<hash>.cblite2" (for cblite only)
    location_mode: str = "project"  # "project" or "global"
    db_relpath: Optional[str] = None  # relative to _apollo/ (project mode only)
    origin_abspath: Optional[str] = None  # path used to compute db_hash
    cblite_version: Optional[str] = None  # libcblite version at create time
    schema_version: int = 1  # Apollo CBL schema version

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectStorage":
        if not data:
            return cls()
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ProjectManifest:
    """Apollo project manifest (apollo.json)."""
    project_id: str
    root_dir: str
    created_at: str
    created_by_version: str
    initial_index_completed: bool
    filters: ProjectFilters
    storage: ProjectStorage = field(default_factory=ProjectStorage)
    last_opened_at: Optional[str] = None
    last_opened_by_version: Optional[str] = None
    last_indexed_at: Optional[str] = None
    last_indexed_by_version: Optional[str] = None
    stats: Optional[ProjectStats] = None

    @property
    def path(self) -> Path:
        """Path to apollo.json."""
        return Path(self.root_dir) / "_apollo" / "apollo.json"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        if self.filters:
            data["filters"] = self.filters.to_dict()
        if self.storage:
            data["storage"] = self.storage.to_dict()
        if self.stats:
            data["stats"] = self.stats.to_dict()
        data["$schema"] = "https://apollo.local/schema/apollo-project.schema.json"
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectManifest":
        """Create from dictionary."""
        filters_data = data.pop("filters", {})
        filters = ProjectFilters.from_dict(filters_data)
        
        storage_data = data.pop("storage", {})
        storage = ProjectStorage.from_dict(storage_data) if storage_data else ProjectStorage()
        
        stats_data = data.pop("stats", None)
        stats = ProjectStats.from_dict(stats_data) if stats_data else None
        
        data.pop("$schema", None)  # Remove schema directive
        
        return cls(filters=filters, storage=storage, stats=stats, **data)

    def save(self) -> None:
        """Persist manifest to disk with schema validation."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        data = self.to_dict()
        
        # Validate against schema
        schema_path = Path(__file__).parent.parent.parent / "schema" / "apollo-project.schema.json"
        if schema_path.exists():
            with open(schema_path) as f:
                schema = json.load(f)
            try:
                jsonschema.validate(instance=data, schema=schema)
            except jsonschema.ValidationError as e:
                raise ValueError(f"Manifest validation failed: {e.message}")
        
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, root_dir: Union[str, Path]) -> Optional["ProjectManifest"]:
        """Load manifest from disk."""
        root_dir = Path(root_dir)
        manifest_path = root_dir / "_apollo" / "apollo.json"
        
        if not manifest_path.exists():
            return None
        
        try:
            with open(manifest_path) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Failed to load manifest: {e}")

    @classmethod
    def create_default(cls, root_dir: Union[str, Path], version: str, backend: str = "json") -> "ProjectManifest":
        """Create a new manifest with defaults."""
        from ulid import ULID
        import hashlib
        
        root_dir = Path(root_dir).resolve()
        project_id = f"ap::{ULID()}"
        
        # Initialize storage config
        storage = ProjectStorage(backend=backend)
        
        # If CBL backend, compute db_hash and db_name
        if backend == "cblite":
            abspath_str = str(root_dir)
            db_hash = hashlib.md5(abspath_str.encode("utf-8")).hexdigest()
            storage.db_hash = db_hash
            storage.db_name = f"apollo_{db_hash}.cblite2"
            storage.db_relpath = f"cblite/apollo_{db_hash}.cblite2"
            storage.origin_abspath = abspath_str
            storage.cblite_version = "3.2.0"  # Default; will be updated if libcblite is available
        
        return cls(
            project_id=project_id,
            root_dir=str(root_dir),
            created_at=datetime.utcnow().isoformat() + "Z",
            created_by_version=version,
            initial_index_completed=False,
            filters=ProjectFilters(mode="all"),
            storage=storage,
            stats=ProjectStats(),
        )
