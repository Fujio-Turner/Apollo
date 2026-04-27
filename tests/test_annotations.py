"""Unit tests for Phase 11 annotation manager and models."""

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from apollo.projects.annotations import (
    AnnotationManager,
    AnnotationsData,
    Annotation,
    AnnotationCollection,
    HighlightRange,
    AnnotationType,
    ColorScheme,
)


PROJECT_ID = "ap::testproject"


# ──────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────

class TestAnnotationModel:
    def test_highlight_range_round_trip(self):
        hr = HighlightRange(start_line=1, end_line=10, start_col=4, end_col=20)
        d = hr.to_dict()
        hr2 = HighlightRange.from_dict(d)
        assert hr == hr2

    def test_annotation_round_trip(self):
        ann = Annotation(
            id="an::abc",
            type="highlight",
            target={"type": "file", "file_path": "src/foo.py"},
            created_at="2026-04-27T00:00:00Z",
            content="hello",
            tags=["bug", "todo"],
            color="yellow",
            highlight_range=HighlightRange(start_line=2, end_line=5),
        )
        d = ann.to_dict()
        ann2 = Annotation.from_dict(d)
        assert ann2.id == ann.id
        assert ann2.target == ann.target
        assert ann2.tags == ann.tags
        assert ann2.highlight_range.start_line == 2

    def test_annotations_data_round_trip(self):
        data = AnnotationsData(project_id=PROJECT_ID)
        data.annotations.append(Annotation(
            id="an::a",
            type="bookmark",
            target={"type": "node", "node_id": "n1"},
            created_at="2026-04-27T00:00:00Z",
        ))
        d = data.to_dict()
        data2 = AnnotationsData.from_dict(d)
        assert data2.project_id == PROJECT_ID
        assert len(data2.annotations) == 1
        assert data2.annotations[0].target["node_id"] == "n1"

    def test_collection_round_trip(self):
        c = AnnotationCollection(
            id="coll::a",
            name="bugs",
            created_at="2026-04-27T00:00:00Z",
            annotation_ids=["an::1", "an::2"],
        )
        d = c.to_dict()
        c2 = AnnotationCollection.from_dict(d)
        assert c2.name == "bugs"
        assert c2.annotation_ids == ["an::1", "an::2"]


# ──────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────

class TestAnnotationSchema:
    @pytest.fixture(scope="class")
    def schema(self):
        path = Path(__file__).parents[1] / "schema" / "annotations.schema.json"
        return json.loads(path.read_text())

    def test_minimal_doc_validates(self, schema):
        doc = {
            "version": "1.0",
            "project_id": PROJECT_ID,
            "annotations": [],
        }
        jsonschema.validate(doc, schema)

    def test_full_doc_validates(self, schema):
        doc = {
            "version": "1.0",
            "project_id": PROJECT_ID,
            "created_at": "2026-04-27T00:00:00Z",
            "annotations": [{
                "id": "an::abc",
                "type": "highlight",
                "target": {"type": "file", "file_path": "src/foo.py"},
                "highlight_range": {"start_line": 1, "end_line": 5},
                "color": "yellow",
                "tags": ["bug"],
                "content": "note",
                "created_at": "2026-04-27T00:00:00Z",
            }],
            "collections": [{
                "id": "coll::x",
                "name": "todos",
                "annotation_ids": ["an::abc"],
                "created_at": "2026-04-27T00:00:00Z",
            }],
        }
        jsonschema.validate(doc, schema)

    def test_invalid_color_rejected(self, schema):
        doc = {
            "version": "1.0",
            "project_id": PROJECT_ID,
            "annotations": [{
                "id": "an::abc",
                "type": "highlight",
                "target": {"type": "file", "file_path": "x.py"},
                "color": "neon",
                "created_at": "2026-04-27T00:00:00Z",
            }],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, schema)

    def test_invalid_id_prefix_rejected(self, schema):
        doc = {
            "version": "1.0",
            "project_id": "no-prefix",
            "annotations": [],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, schema)


# ──────────────────────────────────────────────────────────────────
# Manager
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mgr(tmp_path):
    return AnnotationManager(project_root=tmp_path, project_id=PROJECT_ID)


class TestAnnotationManager:
    def test_load_empty_when_missing(self, mgr):
        data = mgr.load()
        assert data.project_id == PROJECT_ID
        assert data.annotations == []
        assert data.collections == []

    def test_create_persists_to_disk(self, mgr, tmp_path):
        ann = mgr.create(
            type="highlight",
            target={"type": "file", "file_path": "src/foo.py"},
            color="yellow",
            tags=["todo"],
            highlight_range={"start_line": 1, "end_line": 3},
        )
        assert ann.id.startswith("an::")
        path = tmp_path / "_apollo" / "annotations.json"
        assert path.exists()
        on_disk = json.loads(path.read_text())
        assert on_disk["project_id"] == PROJECT_ID
        assert len(on_disk["annotations"]) == 1
        assert on_disk["annotations"][0]["color"] == "yellow"

    def test_get_returns_none_for_missing(self, mgr):
        assert mgr.get("an::nope") is None

    def test_get_returns_existing(self, mgr):
        ann = mgr.create(type="bookmark", target={"type": "file", "file_path": "a.py"})
        got = mgr.get(ann.id)
        assert got is not None
        assert got.id == ann.id

    def test_update_modifies_fields(self, mgr):
        ann = mgr.create(type="note", target={"type": "file", "file_path": "a.py"})
        updated = mgr.update(ann.id, content="new note", tags=["x"], color="red")
        assert updated.content == "new note"
        assert updated.tags == ["x"]
        assert updated.color == "red"
        assert updated.last_modified_at is not None

    def test_update_returns_none_for_missing(self, mgr):
        assert mgr.update("an::missing", content="x") is None

    def test_delete_removes_annotation(self, mgr):
        ann = mgr.create(type="bookmark", target={"type": "file", "file_path": "a.py"})
        assert mgr.delete(ann.id) is True
        assert mgr.get(ann.id) is None
        assert mgr.delete(ann.id) is False

    def test_find_by_target_file(self, mgr):
        a = mgr.create(type="highlight", target={"type": "file", "file_path": "a.py"})
        mgr.create(type="highlight", target={"type": "file", "file_path": "b.py"})
        found = mgr.find_by_target_file("a.py")
        assert len(found) == 1
        assert found[0].id == a.id

    def test_find_by_target_node(self, mgr):
        a = mgr.create(type="bookmark", target={"type": "node", "node_id": "n1"})
        mgr.create(type="bookmark", target={"type": "node", "node_id": "n2"})
        found = mgr.find_by_target_node("n1")
        assert len(found) == 1
        assert found[0].id == a.id

    def test_find_by_tag(self, mgr):
        mgr.create(type="note", target={"type": "file", "file_path": "a.py"}, tags=["bug", "p1"])
        mgr.create(type="note", target={"type": "file", "file_path": "b.py"}, tags=["bug"])
        mgr.create(type="note", target={"type": "file", "file_path": "c.py"}, tags=["other"])
        bugs = mgr.find_by_tag("bug")
        assert len(bugs) == 2

    def test_invalid_type_rejected(self, mgr):
        with pytest.raises(ValueError):
            mgr.create(type="bogus", target={"type": "file", "file_path": "a.py"})

    def test_invalid_color_rejected(self, mgr):
        with pytest.raises(ValueError):
            mgr.create(
                type="highlight",
                target={"type": "file", "file_path": "a.py"},
                color="neon",
            )

    def test_invalid_target_rejected(self, mgr):
        with pytest.raises(ValueError):
            mgr.create(type="highlight", target={"type": "junk"})
        with pytest.raises(ValueError):
            mgr.create(type="highlight", target={"type": "file"})

    def test_corrupt_file_recovered(self, mgr, tmp_path):
        apollo_dir = tmp_path / "_apollo"
        apollo_dir.mkdir()
        (apollo_dir / "annotations.json").write_text("not json")
        # Should not raise; backs up and starts fresh
        data = mgr.load()
        assert data.annotations == []


class TestAnnotationCollections:
    def test_create_and_list_collection(self, mgr):
        a = mgr.create(type="bookmark", target={"type": "file", "file_path": "a.py"})
        coll = mgr.create_collection(name="critical", annotation_ids=[a.id])
        assert coll.id.startswith("coll::")
        listed = mgr.list_collections()
        assert len(listed) == 1
        assert listed[0].name == "critical"

    def test_delete_collection(self, mgr):
        coll = mgr.create_collection(name="x")
        assert mgr.delete_collection(coll.id) is True
        assert mgr.delete_collection(coll.id) is False

    def test_delete_annotation_drops_from_collection(self, mgr):
        a = mgr.create(type="bookmark", target={"type": "file", "file_path": "a.py"})
        coll = mgr.create_collection(name="x", annotation_ids=[a.id])
        mgr.delete(a.id)
        # Reload from disk
        listed = mgr.list_collections()
        assert listed[0].annotation_ids == []


class TestAnnotationReindex:
    def test_file_move_remap(self, mgr):
        a = mgr.create(type="highlight", target={"type": "file", "file_path": "src/old.py"})
        result = mgr.reindex_targets(file_moves={"src/old.py": "src/new.py"})
        assert result["remapped"] == 1
        assert mgr.get(a.id).target == {"type": "file", "file_path": "src/new.py"}

    def test_node_remap(self, mgr):
        a = mgr.create(type="bookmark", target={"type": "node", "node_id": "n_old"})
        result = mgr.reindex_targets(node_remap={"n_old": "n_new"})
        assert result["remapped"] == 1
        assert mgr.get(a.id).target["node_id"] == "n_new"
        assert mgr.get(a.id).stale is False

    def test_node_remap_to_none_marks_stale(self, mgr):
        a = mgr.create(type="bookmark", target={"type": "node", "node_id": "n_old"})
        result = mgr.reindex_targets(node_remap={"n_old": None})
        assert result["stale"] == 1
        assert mgr.get(a.id).stale is True

    def test_validate_file_targets_marks_missing(self, mgr, tmp_path):
        # File that exists
        (tmp_path / "exists.py").write_text("x")
        a_exists = mgr.create(
            type="highlight", target={"type": "file", "file_path": "exists.py"}
        )
        # File that does NOT exist
        a_missing = mgr.create(
            type="highlight", target={"type": "file", "file_path": "missing.py"}
        )
        marked = mgr.validate_file_targets()
        assert marked == 1
        assert mgr.get(a_exists.id).stale is False
        assert mgr.get(a_missing.id).stale is True
