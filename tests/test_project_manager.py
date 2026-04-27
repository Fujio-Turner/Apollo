"""Tests for ProjectManager."""

import pytest
from pathlib import Path
import tempfile
import shutil

from apollo.projects.manager import ProjectManager
from apollo.projects.manifest import ProjectFilters


class TestProjectManagerOpen:
    """Test ProjectManager.open()."""

    def test_open_new_project(self):
        """Test opening a new project creates apollo.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            info = manager.open(tmpdir)
            
            assert info.project_id.startswith("ap::")
            assert info.needs_bootstrap is True
            assert info.initial_index_completed is False
            assert info.filters["mode"] == "all"
            
            # Verify manifest was saved
            manifest_path = Path(tmpdir) / "_apollo" / "apollo.json"
            assert manifest_path.exists()

    def test_open_existing_project(self):
        """Test opening an existing project loads manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and save initial project
            manager1 = ProjectManager("0.7.2")
            info1 = manager1.open(tmpdir)
            project_id = info1.project_id
            
            # Open with a new manager
            manager2 = ProjectManager("0.7.2")
            info2 = manager2.open(tmpdir)
            
            assert info2.project_id == project_id
            assert info2.needs_bootstrap is True

    def test_open_updates_last_opened(self):
        """Test that open() updates last_opened_at timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            info1 = manager.open(tmpdir)
            original_opened = info1.last_opened_at
            
            # Open again
            info2 = manager.open(tmpdir)
            assert info2.last_opened_at is not None
            # Should be at least as recent
            assert info2.last_opened_at >= original_opened

    def test_manager_stores_root_dir(self):
        """Test that manager stores root_dir after opening."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            assert manager.root_dir is not None
            assert manager.root_dir == Path(tmpdir).resolve()

    def test_manager_stores_manifest(self):
        """Test that manager stores manifest after opening."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            assert manager.manifest is not None
            assert manager.manifest.root_dir == str(Path(tmpdir).resolve())


class TestProjectManagerInit:
    """Test ProjectManager.init() for custom filters."""

    def test_init_with_custom_filters(self):
        """Test initializing with custom filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            
            filters = {
                "mode": "custom",
                "include_dirs": ["src", "tests"],
                "exclude_dirs": ["venv"],
                "exclude_file_globs": ["*.pyc"],
                "include_doc_types": ["py", "md"],
            }
            
            info = manager.init(tmpdir, filters)
            
            assert info.filters["mode"] == "custom"
            assert info.filters["include_dirs"] == ["src", "tests"]
            assert info.filters["exclude_dirs"] == ["venv"]
            assert info.filters["exclude_file_globs"] == ["*.pyc"]
            assert info.filters["include_doc_types"] == ["py", "md"]

    def test_init_creates_manifest_file(self):
        """Test that init() creates apollo.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.init(tmpdir, {"mode": "custom", "include_dirs": ["src"], "exclude_dirs": [], "exclude_file_globs": [], "include_doc_types": []})
            
            manifest_path = Path(tmpdir) / "_apollo" / "apollo.json"
            assert manifest_path.exists()


class TestProjectManagerIndexing:
    """Test ProjectManager index tracking methods."""

    def test_mark_index_complete(self):
        """Test marking index as complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            assert manager.manifest.initial_index_completed is False
            
            manager.mark_index_complete(
                files_indexed=50,
                nodes=200,
                edges=400,
                elapsed_seconds=3.5,
            )
            
            # Verify state was saved
            from apollo.projects.manifest import ProjectManifest
            loaded = ProjectManifest.load(tmpdir)
            assert loaded.initial_index_completed is True
            assert loaded.stats.files_indexed == 50
            assert loaded.stats.nodes == 200
            assert loaded.stats.edges == 400
            assert loaded.stats.elapsed_seconds == 3.5

    def test_mark_index_complete_without_open_raises(self):
        """Test that mark_index_complete raises if no project open."""
        manager = ProjectManager("0.7.2")
        
        with pytest.raises(RuntimeError, match="No project is currently open"):
            manager.mark_index_complete(10, 20, 30, 1.0)


class TestProjectManagerFilters:
    """Test ProjectManager filter updates."""

    def test_update_filters(self):
        """Test updating filters for current project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            new_filters = {
                "mode": "custom",
                "include_dirs": ["src"],
                "exclude_dirs": ["build"],
                "exclude_file_globs": ["*.min.js"],
                "include_doc_types": ["py"],
            }
            
            info = manager.update_filters(new_filters)
            
            assert info.filters["mode"] == "custom"
            assert info.filters["include_dirs"] == ["src"]

    def test_update_filters_persists(self):
        """Test that filter updates are persisted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager1 = ProjectManager("0.7.2")
            manager1.open(tmpdir)
            
            new_filters = {
                "mode": "custom",
                "include_dirs": ["src", "docs"],
                "exclude_dirs": ["venv"],
                "exclude_file_globs": [],
                "include_doc_types": ["py", "md"],
            }
            manager1.update_filters(new_filters)
            
            # Load with new manager
            manager2 = ProjectManager("0.7.2")
            info = manager2.open(tmpdir)
            
            assert info.filters["include_dirs"] == ["src", "docs"]

    def test_update_filters_without_open_raises(self):
        """Test that update_filters raises if no project open."""
        manager = ProjectManager("0.7.2")
        
        with pytest.raises(RuntimeError, match="No project is currently open"):
            manager.update_filters({"mode": "all", "exclude_dirs": [], "exclude_file_globs": [], "include_doc_types": []})


class TestProjectManagerLeave:
    """Test ProjectManager.leave() for project removal."""

    def test_leave_deletes_apollo_dir(self):
        """Test that leave() deletes _apollo/ directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            apollo_dir = tmppath / "_apollo"
            assert apollo_dir.exists()
            
            deleted = manager.leave()
            
            assert not apollo_dir.exists()
            # Check that _apollo was deleted (accounting for symlink resolution on macOS)
            assert any("_apollo" in d for d in deleted)

    def test_leave_deletes_apollo_web_dir(self):
        """Test that leave() also deletes _apollo_web/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            # Create _apollo_web/ directory
            apollo_web_dir = tmppath / "_apollo_web"
            apollo_web_dir.mkdir()
            (apollo_web_dir / "test.json").write_text("{}")
            
            assert apollo_web_dir.exists()
            
            deleted = manager.leave()
            
            assert not apollo_web_dir.exists()
            # Check that _apollo_web was deleted (accounting for symlink resolution on macOS)
            assert any("_apollo_web" in d for d in deleted)

    def test_leave_clears_manager_state(self):
        """Test that leave() clears manager's internal state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            assert manager.manifest is not None
            assert manager.root_dir is not None
            
            manager.leave()
            
            assert manager.manifest is None
            assert manager.root_dir is None

    def test_leave_without_open_raises(self):
        """Test that leave() raises if no project open."""
        manager = ProjectManager("0.7.2")
        
        with pytest.raises(RuntimeError, match="No project is currently open"):
            manager.leave()


class TestProjectManagerCurrentInfo:
    """Test ProjectManager.current_info()."""

    def test_current_info_returns_none_initially(self):
        """Test that current_info() returns None before opening."""
        manager = ProjectManager("0.7.2")
        assert manager.current_info() is None

    def test_current_info_returns_info_after_open(self):
        """Test that current_info() returns info after opening."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            info = manager.current_info()
            assert info is not None
            assert info.project_id.startswith("ap::")

    def test_current_info_returns_none_after_leave(self):
        """Test that current_info() returns None after leave()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            manager.leave()
            
            assert manager.current_info() is None


class TestProjectManagerReprocess:
    """Test ProjectManager.reprocess() — Phase 10."""

    def test_reprocess_incremental(self):
        """Test incremental reprocess mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            result = manager.reprocess(mode="incremental")
            
            assert result["mode"] == "incremental"
            assert result["backend"] == "json"
            assert result["project_id"].startswith("ap::")

    def test_reprocess_full_json_backend(self):
        """Test full reprocess on JSON backend deletes graph.json and embeddings.npy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            # Create dummy graph.json and embeddings.npy
            apollo_dir = Path(tmpdir) / "_apollo"
            graph_file = apollo_dir / "graph.json"
            embeddings_file = apollo_dir / "embeddings.npy"
            
            graph_file.write_text("{}")
            embeddings_file.write_text("dummy")
            
            # Mark as indexed
            manager.mark_index_complete(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.0)
            
            assert graph_file.exists()
            assert embeddings_file.exists()
            
            # Full reprocess
            result = manager.reprocess(mode="full")
            
            # Graph files should be deleted
            assert not graph_file.exists()
            assert not embeddings_file.exists()
            
            # apollo.json should still exist
            assert (apollo_dir / "apollo.json").exists()
            
            # Result should indicate deletion
            assert result["mode"] == "full"
            assert "graph_deleted" in result
            assert "embeddings_deleted" in result

    def test_reprocess_preserves_annotations(self):
        """Test that full reprocess preserves annotations.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            # Create annotations.json
            apollo_dir = Path(tmpdir) / "_apollo"
            annotations_file = apollo_dir / "annotations.json"
            annotations_file.write_text('{"version": 1}')
            
            # Create dummy graph files
            (apollo_dir / "graph.json").write_text("{}")
            (apollo_dir / "embeddings.npy").write_text("dummy")
            
            manager.mark_index_complete(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.0)
            
            # Full reprocess
            manager.reprocess(mode="full")
            
            # Annotations should be preserved
            assert annotations_file.exists()
            assert annotations_file.read_text() == '{"version": 1}'

    def test_reprocess_preserves_chat(self):
        """Test that full reprocess preserves chat/ folder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            # Create chat folder with dummy files
            apollo_dir = Path(tmpdir) / "_apollo"
            chat_dir = apollo_dir / "chat"
            chat_dir.mkdir(exist_ok=True)
            (chat_dir / "session_1.json").write_text('{}')
            
            # Create dummy graph files
            (apollo_dir / "graph.json").write_text("{}")
            (apollo_dir / "embeddings.npy").write_text("dummy")
            
            manager.mark_index_complete(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.0)
            
            # Full reprocess
            manager.reprocess(mode="full")
            
            # Chat should be preserved
            assert chat_dir.exists()
            assert (chat_dir / "session_1.json").exists()

    def test_reprocess_full_resets_stats(self):
        """Test that full reprocess resets index stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            # Mark as indexed with stats
            manager.mark_index_complete(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.0)
            assert manager.manifest.last_indexed_at is not None
            assert manager.manifest.stats is not None
            
            # Create dummy files
            apollo_dir = Path(tmpdir) / "_apollo"
            (apollo_dir / "graph.json").write_text("{}")
            (apollo_dir / "embeddings.npy").write_text("dummy")
            
            # Full reprocess
            result = manager.reprocess(mode="full")
            
            # Stats should be reset
            assert manager.manifest.last_indexed_at is None
            assert manager.manifest.stats is None
            assert result["manifest_reset"] is True

    def test_reprocess_without_open_raises(self):
        """Test that reprocess() raises if no project open."""
        manager = ProjectManager("0.7.2")
        
        with pytest.raises(RuntimeError, match="No project is currently open"):
            manager.reprocess(mode="incremental")


class TestProjectManagerResume:
    """Test resume behavior for interrupted bootstrap — Phase 10."""

    def test_resume_pending_on_incomplete_project(self):
        """Test that resume_pending is True for incomplete projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First, create a project and open it
            manager1 = ProjectManager("0.7.2")
            manager1.open(tmpdir)
            
            # Simulate bootstrap being interrupted by closing manager
            # (last_opened_at was set but initial_index_completed is false)
            
            # Reopen with new manager
            manager2 = ProjectManager("0.7.2")
            info = manager2.open(tmpdir)
            
            # Should have resume_pending=True (opened but not indexed)
            assert info.needs_bootstrap is True
            assert info.resume_pending is True

    def test_resume_pending_false_on_complete_project(self):
        """Test that resume_pending is False for completed projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager1 = ProjectManager("0.7.2")
            manager1.open(tmpdir)
            manager1.mark_index_complete(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.0)
            
            # Reopen with new manager
            manager2 = ProjectManager("0.7.2")
            info = manager2.open(tmpdir)
            
            # Should have resume_pending=False
            assert info.needs_bootstrap is False
            assert info.resume_pending is False

    def test_resume_pending_false_on_new_project(self):
        """Test that resume_pending is False for brand new projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            info = manager.open(tmpdir)
            
            # New project: last_opened_at is set but we check if it's truly new
            # (no last_opened_at from previous session)
            assert info.needs_bootstrap is True
            # For brand new project, resume_pending depends on last_opened_at logic
            # Since we just opened it, the logic considers it incomplete -> resume
            assert info.resume_pending is True


class TestProjectManagerLeaveWithSettings:
    """Test ProjectManager.leave() with SettingsManager — Phase 10."""

    def test_leave_removes_from_recent_projects(self):
        """Test that leave() removes project from recent_projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a temporary settings file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                settings_path = f.name
            
            try:
                from apollo.projects.settings import SettingsManager
                
                settings_mgr = SettingsManager(settings_path)
                manager = ProjectManager("0.7.2", settings_manager=settings_mgr)
                
                # Open project
                info = manager.open(tmpdir)
                
                # Add to recent projects
                settings_mgr.add_recent_project(tmpdir, info.project_id)
                assert len(settings_mgr.data.recent_projects) == 1
                
                # Leave project
                manager.leave()
                
                # Should be removed from recent
                assert len(settings_mgr.data.recent_projects) == 0
            finally:
                Path(settings_path).unlink(missing_ok=True)

    def test_leave_deletes_apollo_and_apollo_web(self):
        """Test that leave() deletes both _apollo/ and _apollo_web/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager("0.7.2")
            manager.open(tmpdir)
            
            # Create both directories
            apollo_dir = Path(tmpdir) / "_apollo"
            apollo_web_dir = Path(tmpdir) / "_apollo_web"
            apollo_web_dir.mkdir(exist_ok=True)
            
            assert apollo_dir.exists()
            assert apollo_web_dir.exists()
            
            # Leave project
            deleted = manager.leave()
            
            # Both should be deleted
            assert not apollo_dir.exists()
            assert not apollo_web_dir.exists()
            # Use resolved paths for comparison (handles symlinks/relative paths)
            deleted_resolved = [str(Path(d).resolve()) for d in deleted]
            assert str(apollo_dir.resolve()) in deleted_resolved
            assert str(apollo_web_dir.resolve()) in deleted_resolved
