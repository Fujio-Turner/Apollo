"""Tests for ProjectManifest, ProjectFilters, ProjectStats."""

import json
import pytest
from pathlib import Path
from datetime import datetime
import tempfile
import shutil

from apollo.projects.manifest import ProjectManifest, ProjectFilters, ProjectStats


class TestProjectStats:
    """Test ProjectStats dataclass."""

    def test_create_default(self):
        stats = ProjectStats()
        assert stats.files_indexed == 0
        assert stats.nodes == 0
        assert stats.edges == 0
        assert stats.elapsed_seconds == 0.0

    def test_create_with_values(self):
        stats = ProjectStats(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.5)
        assert stats.files_indexed == 10
        assert stats.nodes == 20
        assert stats.edges == 30
        assert stats.elapsed_seconds == 1.5

    def test_to_dict(self):
        stats = ProjectStats(files_indexed=10, nodes=20, edges=30, elapsed_seconds=1.5)
        d = stats.to_dict()
        assert d == {
            "files_indexed": 10,
            "nodes": 20,
            "edges": 30,
            "elapsed_seconds": 1.5,
        }

    def test_from_dict(self):
        d = {"files_indexed": 5, "nodes": 15, "edges": 25, "elapsed_seconds": 2.0}
        stats = ProjectStats.from_dict(d)
        assert stats.files_indexed == 5
        assert stats.nodes == 15
        assert stats.edges == 25
        assert stats.elapsed_seconds == 2.0


class TestProjectFilters:
    """Test ProjectFilters dataclass."""

    def test_create_default(self):
        filters = ProjectFilters()
        assert filters.mode == "all"
        assert filters.include_dirs == []
        assert filters.exclude_dirs == []
        assert filters.exclude_file_globs == []
        assert filters.include_doc_types == []

    def test_create_custom(self):
        filters = ProjectFilters(
            mode="custom",
            include_dirs=["src", "docs"],
            exclude_dirs=["venv", "node_modules"],
            exclude_file_globs=["*.min.js"],
            include_doc_types=["py", "md"],
        )
        assert filters.mode == "custom"
        assert filters.include_dirs == ["src", "docs"]
        assert filters.exclude_dirs == ["venv", "node_modules"]
        assert filters.exclude_file_globs == ["*.min.js"]
        assert filters.include_doc_types == ["py", "md"]

    def test_to_dict(self):
        filters = ProjectFilters(
            mode="custom",
            include_dirs=["src"],
            exclude_file_globs=["*.lock"],
            include_doc_types=["py"],
        )
        d = filters.to_dict()
        assert d["mode"] == "custom"
        assert d["include_dirs"] == ["src"]
        assert d["exclude_file_globs"] == ["*.lock"]
        assert d["include_doc_types"] == ["py"]

    def test_from_dict(self):
        d = {
            "mode": "custom",
            "include_dirs": ["src", "tests"],
            "exclude_dirs": ["build"],
            "exclude_file_globs": ["*.pyc"],
            "include_doc_types": ["py", "md"],
        }
        filters = ProjectFilters.from_dict(d)
        assert filters.mode == "custom"
        assert filters.include_dirs == ["src", "tests"]
        assert filters.exclude_dirs == ["build"]


class TestProjectManifest:
    """Test ProjectManifest creation, serialization, and persistence."""

    def test_create_from_defaults(self):
        """Test ProjectManifest.create_default()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = ProjectManifest.create_default(tmpdir, "0.7.2")
            
            assert manifest.project_id.startswith("ap::")
            # macOS tmpdir resolves to /private symlink, so normalize paths
            assert Path(manifest.root_dir).resolve() == Path(tmpdir).resolve()
            assert manifest.created_by_version == "0.7.2"
            assert manifest.initial_index_completed is False
            assert manifest.filters.mode == "all"
            assert manifest.stats == ProjectStats()

    def test_to_dict_includes_schema(self):
        """Test that to_dict includes $schema field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = ProjectManifest.create_default(tmpdir, "0.7.2")
            d = manifest.to_dict()
            
            assert "$schema" in d
            assert d["$schema"] == "https://apollo.local/schema/apollo-project.schema.json"

    def test_save_and_load(self):
        """Test round-trip: create, save, load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            
            # Create and save
            manifest = ProjectManifest.create_default(tmppath, "0.7.2")
            manifest.filters = ProjectFilters(
                mode="custom",
                include_dirs=["src"],
                exclude_dirs=["venv"],
                include_doc_types=["py", "md"],
            )
            manifest.save()
            
            # Verify file exists
            assert (tmppath / "_apollo" / "apollo.json").exists()
            
            # Load and verify
            loaded = ProjectManifest.load(tmppath)
            assert loaded is not None
            assert loaded.project_id == manifest.project_id
            assert loaded.created_by_version == "0.7.2"
            assert loaded.filters.mode == "custom"
            assert loaded.filters.include_dirs == ["src"]
            assert loaded.filters.exclude_dirs == ["venv"]

    def test_load_nonexistent_returns_none(self):
        """Test that loading from non-existent path returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = ProjectManifest.load(tmpdir)
            assert manifest is None

    def test_save_creates_apollo_dir(self):
        """Test that save() creates _apollo/ directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            manifest = ProjectManifest.create_default(tmppath, "0.7.2")
            
            assert not (tmppath / "_apollo").exists()
            manifest.save()
            assert (tmppath / "_apollo").exists()
            assert (tmppath / "_apollo" / "apollo.json").exists()

    def test_manifest_json_structure(self):
        """Test that persisted JSON has correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            manifest = ProjectManifest.create_default(tmppath, "0.7.2")
            manifest.save()
            
            # Read raw JSON
            with open(tmppath / "_apollo" / "apollo.json") as f:
                data = json.load(f)
            
            # Verify required fields
            assert "project_id" in data
            assert "root_dir" in data
            assert "created_at" in data
            assert "created_by_version" in data
            assert "initial_index_completed" in data
            assert "filters" in data
            assert data["initial_index_completed"] is False
            assert data["filters"]["mode"] == "all"

    def test_save_with_stats(self):
        """Test saving manifest with stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            manifest = ProjectManifest.create_default(tmppath, "0.7.2")
            manifest.stats = ProjectStats(
                files_indexed=100,
                nodes=500,
                edges=1000,
                elapsed_seconds=5.5,
            )
            manifest.save()
            
            loaded = ProjectManifest.load(tmppath)
            assert loaded.stats is not None
            assert loaded.stats.files_indexed == 100
            assert loaded.stats.nodes == 500
            assert loaded.stats.edges == 1000
            assert loaded.stats.elapsed_seconds == 5.5

    def test_update_opened_timestamps(self):
        """Test that opened timestamps are updated on load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            manifest = ProjectManifest.create_default(tmppath, "0.7.2")
            original_created_at = manifest.created_at
            manifest.save()
            
            # Simulate opening after a delay
            manifest.last_opened_at = datetime.utcnow().isoformat() + "Z"
            manifest.last_opened_by_version = "0.7.3"
            manifest.save()
            
            loaded = ProjectManifest.load(tmppath)
            assert loaded.created_at == original_created_at
            assert loaded.last_opened_at is not None
            assert loaded.last_opened_by_version == "0.7.3"
