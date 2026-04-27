"""Tests for Phase 6: Storage backend and CBL lifecycle."""

import hashlib
import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from apollo.projects import (
    ProjectManager,
    ProjectManifest,
    ProjectStorage,
    SettingsManager,
    SettingsData,
)


class TestProjectStorageManifest:
    """Tests for ProjectStorage dataclass and manifest integration."""

    def test_create_json_storage(self):
        """Test creating a JSON storage config."""
        storage = ProjectStorage(backend="json")
        assert storage.backend == "json"
        assert storage.db_hash is None
        assert storage.db_name is None

    def test_create_cblite_storage(self):
        """Test creating a CBL storage config with hash."""
        abspath = "/Users/alice/projects/myapp"
        db_hash = hashlib.md5(abspath.encode("utf-8")).hexdigest()
        
        storage = ProjectStorage(
            backend="cblite",
            db_hash=db_hash,
            db_name=f"apollo_{db_hash}.cblite2",
            db_relpath=f"cblite/apollo_{db_hash}.cblite2",
            origin_abspath=abspath,
        )
        
        assert storage.backend == "cblite"
        assert storage.db_hash == db_hash
        assert storage.db_name == f"apollo_{db_hash}.cblite2"
        assert storage.origin_abspath == abspath

    def test_storage_to_dict(self):
        """Test serializing storage config."""
        storage = ProjectStorage(backend="cblite", db_hash="abc123")
        data = storage.to_dict()
        
        assert data["backend"] == "cblite"
        assert data["db_hash"] == "abc123"

    def test_storage_from_dict(self):
        """Test deserializing storage config."""
        data = {
            "backend": "cblite",
            "db_hash": "abc123",
            "db_name": "apollo_abc123.cblite2",
        }
        storage = ProjectStorage.from_dict(data)
        
        assert storage.backend == "cblite"
        assert storage.db_hash == "abc123"

    def test_manifest_with_storage_roundtrip(self, tmp_path):
        """Test that manifest with storage config persists correctly."""
        abspath = str(tmp_path.resolve())
        db_hash = hashlib.md5(abspath.encode("utf-8")).hexdigest()
        
        manifest = ProjectManifest.create_default(
            tmp_path,
            version="0.7.0",
            backend="cblite"
        )
        
        assert manifest.storage.backend == "cblite"
        assert manifest.storage.db_hash == db_hash
        assert manifest.storage.origin_abspath == abspath
        
        # Save and reload
        manifest.save()
        loaded = ProjectManifest.load(tmp_path)
        
        assert loaded is not None
        assert loaded.storage.backend == "cblite"
        assert loaded.storage.db_hash == db_hash


class TestProjectManagerStorageOps:
    """Tests for ProjectManager storage-related operations."""

    def test_compute_db_hash(self, tmp_path):
        """Test computing MD5 hash of project path."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        path = tmp_path.resolve()
        expected = hashlib.md5(str(path).encode("utf-8")).hexdigest()
        actual = manager._compute_db_hash(path)
        
        assert actual == expected

    def test_resolve_cbl_path_project_mode(self, tmp_path):
        """Test resolving CBL path in project-local mode."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        manifest = ProjectManifest.create_default(tmp_path, version="0.7.0", backend="cblite")
        assert manifest.storage.location_mode == "project"
        
        resolved = manager._resolve_cbl_path(manifest)
        assert resolved is not None
        assert "_apollo/cblite/apollo_" in str(resolved)
        assert str(resolved).endswith(".cblite2")

    def test_resolve_cbl_path_global_mode(self, tmp_path):
        """Test resolving CBL path in global mode."""
        manager = ProjectManager(version="0.7.0", default_backend="json")
        
        manifest = ProjectManifest.create_default(tmp_path, version="0.7.0", backend="cblite")
        manifest.storage.location_mode = "global"
        manifest.storage.db_name = "apollo_abc123.cblite2"
        
        resolved = manager._resolve_cbl_path(manifest)
        assert resolved is not None
        assert ".apollo/cblite" in str(resolved)

    def test_open_new_project_cblite_backend(self, tmp_path):
        """Test opening new project with CBL backend."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        info = manager.open(tmp_path)
        
        assert info.needs_bootstrap is True
        assert manager.manifest.storage.backend == "cblite"
        assert manager.manifest.storage.db_hash is not None

    def test_init_creates_cblite_directory(self, tmp_path):
        """Test that init() creates the cblite directory."""
        manager = ProjectManager(version="0.7.0", default_backend="json")
        
        manager.init(tmp_path, backend="cblite")
        
        cblite_dir = tmp_path / "_apollo" / "cblite"
        assert cblite_dir.exists()
        assert cblite_dir.is_dir()

    def test_reprocess_full_cblite(self, tmp_path):
        """Test full reprocess on CBL project (would delete DB)."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        # Initialize project
        manager.open(tmp_path)
        
        # Simulate a reprocess full
        result = manager.reprocess(mode="full")
        
        assert result["mode"] == "full"
        assert result["backend"] == "cblite"

    def test_handle_move_keep_existing_db(self, tmp_path):
        """Test handling project move with 'keep existing DB' option."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        # Initialize at original path
        manager.open(tmp_path)
        original_hash = manager.manifest.storage.db_hash
        
        # Move to new path
        new_path = tmp_path.parent / "moved_project"
        info = manager.handle_move(new_path, rebind=False)
        
        # Hash should still be the original (not rebound)
        assert manager.manifest.storage.db_hash == original_hash
        assert manager.manifest.root_dir == str(new_path.resolve())

    def test_handle_move_rebind(self, tmp_path):
        """Test handling project move with rebind option."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        # Initialize at original path
        manager.open(tmp_path)
        original_hash = manager.manifest.storage.db_hash
        original_path = manager.manifest.storage.origin_abspath
        
        # Move to new path
        new_path = tmp_path.parent / "moved_project"
        new_path.mkdir(parents=True, exist_ok=True)
        
        info = manager.handle_move(new_path, rebind=True)
        
        # Hash should be recomputed
        new_hash = manager.manifest.storage.db_hash
        assert new_hash != original_hash
        assert manager.manifest.storage.origin_abspath == str(new_path.resolve())

    def test_close_existing_on_open(self, tmp_path):
        """Test that open() closes previous project's store."""
        manager = ProjectManager(version="0.7.0")
        
        project1 = tmp_path / "project1"
        project1.mkdir()
        project2 = tmp_path / "project2"
        project2.mkdir()
        
        # Open first project
        manager.open(project1)
        assert manager.root_dir == project1.resolve()
        
        # Open second project (should close first)
        manager.open(project2)
        assert manager.root_dir == project2.resolve()

    def test_leave_closes_store(self, tmp_path):
        """Test that leave() closes the store before deleting."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        manager.open(tmp_path)
        assert manager.manifest is not None
        
        deleted = manager.leave()
        
        assert manager.manifest is None
        assert manager.root_dir is None
        assert len(deleted) > 0


class TestSettingsManager:
    """Tests for global settings management."""

    def test_create_default_settings(self, tmp_path):
        """Test creating default settings."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        assert mgr.data.default_backend == "json"
        assert len(mgr.data.recent_projects) == 0

    def test_add_recent_project(self, tmp_path):
        """Test adding a project to recent list."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        project_path = tmp_path / "myproject"
        mgr.add_recent_project(project_path, "ap::proj123")
        
        assert len(mgr.data.recent_projects) == 1
        assert mgr.data.recent_projects[0].project_id == "ap::proj123"
        assert mgr.data.recent_projects[0].path == str(project_path.resolve())

    def test_add_recent_project_persists(self, tmp_path):
        """Test that recent projects persist to disk."""
        settings_file = tmp_path / "settings.json"
        
        # Add project
        mgr1 = SettingsManager(settings_path=settings_file)
        project_path = tmp_path / "myproject"
        mgr1.add_recent_project(project_path, "ap::proj123")
        
        # Reload and verify
        mgr2 = SettingsManager(settings_path=settings_file)
        assert len(mgr2.data.recent_projects) == 1
        assert mgr2.data.recent_projects[0].project_id == "ap::proj123"

    def test_recent_projects_max_10(self, tmp_path):
        """Test that recent projects list is capped at 10."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        # Add 15 projects
        for i in range(15):
            project_path = tmp_path / f"project{i}"
            mgr.add_recent_project(project_path, f"ap::proj{i}")
        
        # Should be capped at 10
        assert len(mgr.data.recent_projects) == 10

    def test_recent_project_moves_to_front(self, tmp_path):
        """Test that opening existing project moves it to front."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        proj1 = tmp_path / "project1"
        proj2 = tmp_path / "project2"
        
        mgr.add_recent_project(proj1, "ap::proj1")
        mgr.add_recent_project(proj2, "ap::proj2")
        
        # Now open project1 again
        mgr.add_recent_project(proj1, "ap::proj1")
        
        # proj1 should be at front
        assert mgr.data.recent_projects[0].path == str(proj1.resolve())

    def test_remove_recent_project(self, tmp_path):
        """Test removing a project from recent list."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        proj1 = tmp_path / "project1"
        proj2 = tmp_path / "project2"
        
        mgr.add_recent_project(proj1, "ap::proj1")
        mgr.add_recent_project(proj2, "ap::proj2")
        
        mgr.remove_recent_project(proj1)
        
        assert len(mgr.data.recent_projects) == 1
        assert mgr.data.recent_projects[0].project_id == "ap::proj2"

    def test_set_default_backend(self, tmp_path):
        """Test setting default backend."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        mgr.set_default_backend("cblite")
        assert mgr.data.default_backend == "cblite"
        
        # Reload and verify
        mgr2 = SettingsManager(settings_path=settings_file)
        assert mgr2.data.default_backend == "cblite"

    def test_set_cblite_storage_root(self, tmp_path):
        """Test setting global CBL storage root."""
        settings_file = tmp_path / "settings.json"
        mgr = SettingsManager(settings_path=settings_file)
        
        storage_root = tmp_path / "cblite_storage"
        mgr.set_cblite_storage_root(storage_root)
        
        assert mgr.data.cblite_storage_root == str(storage_root.resolve())
        
        # Clear it
        mgr.set_cblite_storage_root(None)
        assert mgr.data.cblite_storage_root is None


class TestPhase6IntegrationCBL:
    """Integration tests for Phase 6 CBL backend lifecycle."""

    def test_init_and_open_cblite_project(self, tmp_path):
        """Test full lifecycle: init, open, reprocess with CBL."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        # Initialize new project with custom filters
        filters = {
            "mode": "custom",
            "include_dirs": ["src"],
            "exclude_dirs": ["venv"],
            "exclude_file_globs": ["*.pyc"],
            "include_doc_types": ["py", "md"],
        }
        
        info = manager.init(tmp_path, filters=filters, backend="cblite")
        assert info.needs_bootstrap is True
        assert manager.manifest.storage.backend == "cblite"
        
        # Verify files exist
        assert (tmp_path / "_apollo" / "apollo.json").exists()
        assert (tmp_path / "_apollo" / "cblite").exists()
        
        # Open the project
        manager2 = ProjectManager(version="0.7.0")
        info2 = manager2.open(tmp_path)
        assert info2.needs_bootstrap is True
        assert manager2.manifest.storage.backend == "cblite"

    def test_project_portable_with_cblite(self, tmp_path):
        """Test that CBL project is portable (DB stored in _apollo/)."""
        manager = ProjectManager(version="0.7.0", default_backend="cblite")
        
        # Initialize project
        manager.init(tmp_path, backend="cblite")
        
        db_path = manager._resolve_cbl_path(manager.manifest)
        
        # DB should be inside _apollo/
        assert "_apollo" in str(db_path)
        assert tmp_path.resolve() in Path(db_path).parents
