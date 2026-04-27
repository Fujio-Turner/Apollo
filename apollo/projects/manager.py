"""Project lifecycle management."""

from __future__ import annotations

import shutil
import json
import os
from pathlib import Path
from typing import Optional, Union, Literal
from datetime import datetime
import hashlib

from .manifest import ProjectManifest, ProjectFilters, ProjectStorage
from .info import ProjectInfo


class ProjectManager:
    """Manages the currently open Apollo project."""

    def __init__(self, version: str, default_backend: str = "json", settings_manager=None):
        """Initialize with Apollo version string and optional settings manager."""
        self.version = version
        self.default_backend = default_backend
        self._manifest: Optional[ProjectManifest] = None
        self._root_dir: Optional[Path] = None
        self._store = None  # Reference to currently open store (JSON or CBL)
        self._settings_manager = settings_manager  # For recent_projects cleanup

    @property
    def manifest(self) -> Optional[ProjectManifest]:
        """Get the current manifest."""
        return self._manifest

    @property
    def root_dir(self) -> Optional[Path]:
        """Get the current project's root directory."""
        return self._root_dir
    
    def _compute_db_hash(self, abspath: Union[str, Path]) -> str:
        """Compute MD5 hash of absolute path for CBL database naming."""
        abspath_str = str(Path(abspath).resolve())
        return hashlib.md5(abspath_str.encode("utf-8")).hexdigest()
    
    def _resolve_cbl_path(self, manifest: ProjectManifest) -> Optional[Path]:
        """Resolve the CBL database path from manifest storage config.
        
        Supports both project-local and global storage modes.
        """
        if manifest.storage.backend != "cblite":
            return None
        
        if manifest.storage.location_mode == "project" and manifest.storage.db_relpath:
            return Path(manifest.root_dir) / "_apollo" / manifest.storage.db_relpath
        elif manifest.storage.location_mode == "global" and manifest.storage.db_name:
            return Path.home() / ".apollo" / "cblite" / manifest.storage.db_name
        
        return None
    
    def _close_existing(self) -> None:
        """Close any open store handle (CBL or JSON)."""
        if self._store is not None:
            if hasattr(self._store, 'close'):
                try:
                    self._store.close()
                except Exception:
                    pass  # Store close may fail; log but continue
            self._store = None

    def open(self, path: Union[str, Path]) -> ProjectInfo:
        """Open an existing or new project.
        
        If apollo.json exists, load it.
        If not, create a new manifest with defaults (mode="all").
        
        Returns ProjectInfo with needs_bootstrap flag.
        """
        path = Path(path).resolve()
        self._close_existing()  # Close any previously open project
        
        # Try to load existing manifest
        manifest = ProjectManifest.load(path)
        
        if manifest is None:
            # New project: create _apollo/ and manifest
            manifest = ProjectManifest.create_default(path, self.version, backend=self.default_backend)
        else:
            # Check if project was moved (hash mismatch)
            if manifest.storage and manifest.storage.backend == "cblite":
                current_hash = self._compute_db_hash(path)
                if manifest.storage.db_hash != current_hash:
                    # Project was moved. Store move info for UI to handle.
                    manifest._move_info = {
                        "current_path": str(path),
                        "original_path": manifest.storage.origin_abspath,
                        "current_hash": current_hash,
                        "stored_hash": manifest.storage.db_hash,
                    }
        
        # Update timestamps
        manifest.last_opened_at = datetime.utcnow().isoformat() + "Z"
        manifest.last_opened_by_version = self.version
        
        # Persist
        manifest.save()
        
        self._manifest = manifest
        self._root_dir = path
        
        return ProjectInfo.from_manifest(manifest)

    def init(
        self,
        path: Union[str, Path],
        filters: Optional[dict] = None,
        backend: Optional[str] = None,
    ) -> ProjectInfo:
        """Initialize a new project with custom filters and backend.
        
        This is called when the user submits the bootstrap wizard with custom filters.
        """
        path = Path(path).resolve()
        self._close_existing()  # Close any previously open project
        
        # Use provided backend or fall back to default
        backend = backend or self.default_backend
        
        # Create manifest with custom filters if provided
        manifest = ProjectManifest.create_default(path, self.version, backend=backend)
        
        if filters:
            manifest.filters = ProjectFilters.from_dict(filters)
        
        # Create backend-specific directories
        if backend == "cblite":
            cbl_dir = path / "_apollo" / "cblite"
            cbl_dir.mkdir(parents=True, exist_ok=True)
        
        manifest.save()
        self._manifest = manifest
        self._root_dir = path
        
        return ProjectInfo.from_manifest(manifest)

    def update_filters(self, filters: dict) -> ProjectInfo:
        """Update filters for current project."""
        if not self._manifest:
            raise RuntimeError("No project is currently open")
        
        self._manifest.filters = ProjectFilters.from_dict(filters)
        self._manifest.save()
        
        return ProjectInfo.from_manifest(self._manifest)

    def mark_index_started(self) -> None:
        """Mark that initial indexing has started."""
        if not self._manifest:
            raise RuntimeError("No project is currently open")
        # State is transient; only mark as completed in mark_index_complete()

    def mark_index_complete(
        self,
        files_indexed: int,
        nodes: int,
        edges: int,
        elapsed_seconds: float,
    ) -> None:
        """Mark initial indexing as complete and update stats."""
        if not self._manifest:
            raise RuntimeError("No project is currently open")
        
        self._manifest.initial_index_completed = True
        self._manifest.last_indexed_at = datetime.utcnow().isoformat() + "Z"
        self._manifest.last_indexed_by_version = self.version
        
        from .manifest import ProjectStats
        self._manifest.stats = ProjectStats(
            files_indexed=files_indexed,
            nodes=nodes,
            edges=edges,
            elapsed_seconds=elapsed_seconds,
        )
        
        self._manifest.save()
    
    def reprocess(self, mode: Literal["incremental", "full"]) -> dict:
        """Reprocess the project.
        
        Args:
            mode: "incremental" for delta updates, "full" to delete and rebuild the graph.
        
        For "full" reprocess:
        - Deletes graph.json and embeddings.npy (or CBL database)
        - PRESERVES annotations.json, chat/, and apollo.json (user data)
        
        Returns:
            dict with reprocess info (mode, backend, db_path if applicable)
        """
        if not self._manifest or not self._root_dir:
            raise RuntimeError("No project is currently open")
        
        result = {
            "mode": mode,
            "backend": self._manifest.storage.backend,
            "project_id": self._manifest.project_id,
        }
        
        if mode == "full":
            apollo_dir = self._root_dir / "_apollo"
            
            # For JSON backend: delete graph.json and embeddings.npy
            if self._manifest.storage.backend == "json":
                graph_file = apollo_dir / "graph.json"
                embeddings_file = apollo_dir / "embeddings.npy"
                
                if graph_file.exists():
                    graph_file.unlink()
                    result["graph_deleted"] = str(graph_file)
                
                if embeddings_file.exists():
                    embeddings_file.unlink()
                    result["embeddings_deleted"] = str(embeddings_file)
            
            # For CBL backend: delete the database bundle, keep cblite/ dir
            elif self._manifest.storage.backend == "cblite":
                db_path = self._resolve_cbl_path(self._manifest)
                if db_path and db_path.exists():
                    self._close_existing()  # Close handle before deleting
                    shutil.rmtree(db_path, ignore_errors=True)
                    result["db_deleted"] = str(db_path)
                    # Recreate empty cblite directory for next indexing
                    db_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Reset index completion flag and stats for re-indexing
            self._manifest.last_indexed_at = None
            self._manifest.last_indexed_by_version = None
            self._manifest.stats = None
            self._manifest.save()
            result["manifest_reset"] = True
        
        return result
    
    def handle_move(self, new_path: Union[str, Path], rebind: bool = False) -> ProjectInfo:
        """Handle a moved project.
        
        Args:
            new_path: The new location of the project folder.
            rebind: If True, update db_hash and rename CBL bundle to new location.
                   If False, keep using existing DB via relpath.
        
        Returns:
            Updated ProjectInfo.
        """
        if not self._manifest:
            raise RuntimeError("No project is currently open")
        
        new_path = Path(new_path).resolve()
        self._manifest.root_dir = str(new_path)
        
        if rebind and self._manifest.storage.backend == "cblite":
            old_db_path = self._resolve_cbl_path(self._manifest)
            
            # Compute new hash
            new_hash = self._compute_db_hash(new_path)
            self._manifest.storage.db_hash = new_hash
            self._manifest.storage.db_name = f"apollo_{new_hash}.cblite2"
            self._manifest.storage.db_relpath = f"cblite/apollo_{new_hash}.cblite2"
            self._manifest.storage.origin_abspath = str(new_path)
            
            # Rename DBon disk if using project-local storage
            if self._manifest.storage.location_mode == "project" and old_db_path and old_db_path.exists():
                new_db_path = self._resolve_cbl_path(self._manifest)
                if new_db_path:
                    new_db_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_db_path), str(new_db_path))
        else:
            # Just keep the existing DB via relpath (for project mode)
            self._manifest.storage.origin_abspath = str(new_path)
        
        self._manifest.save()
        return ProjectInfo.from_manifest(self._manifest)

    def leave(self) -> list[str]:
        """Remove the current project (delete _apollo/ and _apollo_web/).
        
        Closes any open store handle first to allow safe deletion on Windows.
        Also removes project from recent_projects list if SettingsManager is available.
        
        Returns list of deleted paths.
        """
        if not self._root_dir:
            raise RuntimeError("No project is currently open")
        
        self._close_existing()  # Close any open DB handle first
        
        deleted = []
        
        # Delete _apollo/
        apollo_dir = self._root_dir / "_apollo"
        if apollo_dir.exists():
            shutil.rmtree(apollo_dir)
            deleted.append(str(apollo_dir))
        
        # Delete _apollo_web/ (DESIGN §14.3)
        apollo_web_dir = self._root_dir / "_apollo_web"
        if apollo_web_dir.exists():
            shutil.rmtree(apollo_web_dir)
            deleted.append(str(apollo_web_dir))
        
        # Remove from recent_projects if settings manager available
        if self._settings_manager and self._root_dir:
            self._settings_manager.remove_recent_project(self._root_dir)
        
        # Clear in-memory state
        self._manifest = None
        self._root_dir = None
        
        return deleted

    def current_info(self) -> Optional[ProjectInfo]:
        """Get info about currently open project, or None."""
        if not self._manifest:
            return None
        return ProjectInfo.from_manifest(self._manifest)
