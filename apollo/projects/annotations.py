"""User annotations: highlights, bookmarks, notes, tags.

Stored as `<project>/_apollo/annotations.json`. Simple JSON-backed
manager with atomic writes. Targets can be either file ranges or
graph nodes; on graph rebuild, node IDs can be remapped via
`reindex_targets()`.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Union


# ──────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────

class AnnotationType(str, Enum):
    HIGHLIGHT = "highlight"
    BOOKMARK = "bookmark"
    NOTE = "note"
    TAG = "tag"


class ColorScheme(str, Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    GRAY = "gray"


# ──────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────

@dataclass
class HighlightRange:
    start_line: int
    end_line: int
    start_col: Optional[int] = None
    end_col: Optional[int] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None or k in ("start_line", "end_line")}

    @classmethod
    def from_dict(cls, data: dict) -> "HighlightRange":
        return cls(
            start_line=data["start_line"],
            end_line=data["end_line"],
            start_col=data.get("start_col"),
            end_col=data.get("end_col"),
        )


@dataclass
class Annotation:
    id: str
    type: str  # AnnotationType value
    target: dict  # {"type": "file", "file_path": "..."} | {"type": "node", "node_id": "..."}
    created_at: str
    content: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    color: Optional[str] = None
    highlight_range: Optional[HighlightRange] = None
    stale: bool = False
    last_modified_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "type": self.type,
            "target": self.target,
            "created_at": self.created_at,
            "content": self.content,
            "tags": list(self.tags),
            "color": self.color,
            "stale": self.stale,
            "last_modified_at": self.last_modified_at,
        }
        if self.highlight_range is not None:
            d["highlight_range"] = self.highlight_range.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Annotation":
        hr = data.get("highlight_range")
        return cls(
            id=data["id"],
            type=data["type"],
            target=data["target"],
            created_at=data["created_at"],
            content=data.get("content"),
            tags=list(data.get("tags", [])),
            color=data.get("color"),
            highlight_range=HighlightRange.from_dict(hr) if hr else None,
            stale=bool(data.get("stale", False)),
            last_modified_at=data.get("last_modified_at"),
        )


@dataclass
class AnnotationCollection:
    id: str
    name: str
    created_at: str
    description: Optional[str] = None
    annotation_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "annotation_ids": list(self.annotation_ids),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnnotationCollection":
        return cls(
            id=data["id"],
            name=data["name"],
            created_at=data.get("created_at") or _now(),
            description=data.get("description"),
            annotation_ids=list(data.get("annotation_ids", [])),
        )


@dataclass
class AnnotationsData:
    project_id: str
    annotations: list[Annotation] = field(default_factory=list)
    collections: list[AnnotationCollection] = field(default_factory=list)
    version: str = "1.0"
    created_at: str = field(default_factory=lambda: _now())
    last_modified_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "last_modified_at": self.last_modified_at,
            "annotations": [a.to_dict() for a in self.annotations],
            "collections": [c.to_dict() for c in self.collections],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnnotationsData":
        return cls(
            project_id=data["project_id"],
            version=data.get("version", "1.0"),
            created_at=data.get("created_at") or _now(),
            last_modified_at=data.get("last_modified_at"),
            annotations=[Annotation.from_dict(a) for a in data.get("annotations", [])],
            collections=[AnnotationCollection.from_dict(c) for c in data.get("collections", [])],
        )


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _new_id(prefix: str) -> str:
    # 16 hex chars (~64 bits) is plenty for per-project uniqueness
    return f"{prefix}::{secrets.token_hex(8)}"


# ──────────────────────────────────────────────────────────────────
# Manager
# ──────────────────────────────────────────────────────────────────

class AnnotationManager:
    """CRUD over `<project>/_apollo/annotations.json`."""

    FILE_NAME = "annotations.json"

    def __init__(self, project_root: Union[str, Path], project_id: str):
        self.project_root = Path(project_root)
        self.project_id = project_id
        self._apollo_dir = self.project_root / "_apollo"
        self._path = self._apollo_dir / self.FILE_NAME

    # ── persistence ────────────────────────────────────────────

    def load(self) -> AnnotationsData:
        if not self._path.exists():
            return AnnotationsData(project_id=self.project_id)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return AnnotationsData.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            # Corrupt file: start fresh, but don't lose it
            backup = self._path.with_suffix(f".bak.{int(datetime.utcnow().timestamp())}")
            try:
                self._path.rename(backup)
            except OSError:
                pass
            return AnnotationsData(project_id=self.project_id)

    def save(self, data: AnnotationsData) -> None:
        self._apollo_dir.mkdir(parents=True, exist_ok=True)
        data.last_modified_at = _now()
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    # ── annotation CRUD ────────────────────────────────────────

    def create(
        self,
        type: str,
        target: dict,
        content: Optional[str] = None,
        tags: Optional[list[str]] = None,
        color: Optional[str] = None,
        highlight_range: Optional[Union[dict, HighlightRange]] = None,
    ) -> Annotation:
        _validate_type(type)
        _validate_target(target)
        if color is not None:
            _validate_color(color)

        hr = None
        if highlight_range is not None:
            hr = highlight_range if isinstance(highlight_range, HighlightRange) else HighlightRange.from_dict(highlight_range)

        ann = Annotation(
            id=_new_id("an"),
            type=type,
            target=target,
            created_at=_now(),
            content=content,
            tags=list(tags or []),
            color=color,
            highlight_range=hr,
        )
        data = self.load()
        data.annotations.append(ann)
        self.save(data)
        return ann

    def get(self, annotation_id: str) -> Optional[Annotation]:
        for a in self.load().annotations:
            if a.id == annotation_id:
                return a
        return None

    def update(self, annotation_id: str, **changes) -> Optional[Annotation]:
        data = self.load()
        for a in data.annotations:
            if a.id == annotation_id:
                if "type" in changes:
                    _validate_type(changes["type"])
                    a.type = changes["type"]
                if "target" in changes:
                    _validate_target(changes["target"])
                    a.target = changes["target"]
                if "content" in changes:
                    a.content = changes["content"]
                if "tags" in changes:
                    a.tags = list(changes["tags"] or [])
                if "color" in changes:
                    if changes["color"] is not None:
                        _validate_color(changes["color"])
                    a.color = changes["color"]
                if "highlight_range" in changes:
                    hr = changes["highlight_range"]
                    a.highlight_range = (
                        hr if isinstance(hr, HighlightRange)
                        else (HighlightRange.from_dict(hr) if hr else None)
                    )
                if "stale" in changes:
                    a.stale = bool(changes["stale"])
                a.last_modified_at = _now()
                self.save(data)
                return a
        return None

    def delete(self, annotation_id: str) -> bool:
        data = self.load()
        before = len(data.annotations)
        data.annotations = [a for a in data.annotations if a.id != annotation_id]
        # Also drop from any collections
        for c in data.collections:
            c.annotation_ids = [aid for aid in c.annotation_ids if aid != annotation_id]
        if len(data.annotations) == before:
            return False
        self.save(data)
        return True

    # ── search ────────────────────────────────────────────────

    def list_all(self) -> list[Annotation]:
        return self.load().annotations

    def find_by_target_file(self, file_path: str) -> list[Annotation]:
        return [
            a for a in self.load().annotations
            if a.target.get("type") == "file" and a.target.get("file_path") == file_path
        ]

    def find_by_target_node(self, node_id: str) -> list[Annotation]:
        return [
            a for a in self.load().annotations
            if a.target.get("type") == "node" and a.target.get("node_id") == node_id
        ]

    def find_by_tag(self, tag: str) -> list[Annotation]:
        return [a for a in self.load().annotations if tag in a.tags]

    # ── collections ───────────────────────────────────────────

    def create_collection(
        self,
        name: str,
        annotation_ids: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> AnnotationCollection:
        coll = AnnotationCollection(
            id=_new_id("coll"),
            name=name,
            description=description,
            annotation_ids=list(annotation_ids or []),
            created_at=_now(),
        )
        data = self.load()
        data.collections.append(coll)
        self.save(data)
        return coll

    def list_collections(self) -> list[AnnotationCollection]:
        return self.load().collections

    def delete_collection(self, collection_id: str) -> bool:
        data = self.load()
        before = len(data.collections)
        data.collections = [c for c in data.collections if c.id != collection_id]
        if len(data.collections) == before:
            return False
        self.save(data)
        return True

    # ── reindex / remap ───────────────────────────────────────

    def reindex_targets(
        self,
        file_moves: Optional[dict[str, str]] = None,
        node_remap: Optional[dict[str, str]] = None,
    ) -> dict:
        """Apply a path/node-id remap and flag unmappable refs as stale.

        Args:
            file_moves: old_rel_path → new_rel_path
            node_remap: old_node_id → new_node_id (or None to mark stale)

        Returns:
            {"remapped": int, "stale": int}
        """
        file_moves = file_moves or {}
        node_remap = node_remap or {}
        data = self.load()
        remapped = 0
        stale = 0

        for a in data.annotations:
            t = a.target
            if t.get("type") == "file":
                fp = t.get("file_path")
                if fp in file_moves:
                    a.target = {"type": "file", "file_path": file_moves[fp]}
                    a.last_modified_at = _now()
                    remapped += 1
            elif t.get("type") == "node":
                nid = t.get("node_id")
                if node_remap and nid in node_remap:
                    new = node_remap[nid]
                    if new is None:
                        a.stale = True
                        stale += 1
                    else:
                        a.target = {"type": "node", "node_id": new}
                        a.stale = False
                        remapped += 1
                    a.last_modified_at = _now()

        self.save(data)
        return {"remapped": remapped, "stale": stale}

    def validate_file_targets(self, root: Optional[Path] = None) -> int:
        """Mark file-target annotations stale if their file no longer exists.

        Returns count newly marked stale.
        """
        root = Path(root) if root else self.project_root
        data = self.load()
        newly_stale = 0
        for a in data.annotations:
            if a.target.get("type") == "file":
                fp = a.target.get("file_path", "")
                full = (root / fp).resolve()
                exists = full.exists()
                if not exists and not a.stale:
                    a.stale = True
                    a.last_modified_at = _now()
                    newly_stale += 1
                elif exists and a.stale:
                    a.stale = False
                    a.last_modified_at = _now()
        self.save(data)
        return newly_stale


# ──────────────────────────────────────────────────────────────────
# Validators
# ──────────────────────────────────────────────────────────────────

_VALID_TYPES = {t.value for t in AnnotationType}
_VALID_COLORS = {c.value for c in ColorScheme}


def _validate_type(t: str) -> None:
    if t not in _VALID_TYPES:
        raise ValueError(f"Invalid annotation type: {t!r}. Must be one of {sorted(_VALID_TYPES)}")


def _validate_color(c: str) -> None:
    if c not in _VALID_COLORS:
        raise ValueError(f"Invalid color: {c!r}. Must be one of {sorted(_VALID_COLORS)}")


def _validate_target(target: dict) -> None:
    if not isinstance(target, dict):
        raise ValueError("target must be a dict")
    ttype = target.get("type")
    if ttype == "file":
        if not target.get("file_path"):
            raise ValueError("file target requires file_path")
    elif ttype == "node":
        if not target.get("node_id"):
            raise ValueError("node target requires node_id")
    else:
        raise ValueError(f"target.type must be 'file' or 'node' (got {ttype!r})")
