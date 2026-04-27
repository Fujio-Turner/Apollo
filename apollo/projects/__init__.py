"""Apollo project management module."""

from .manager import ProjectManager
from .info import ProjectInfo
from .manifest import ProjectManifest, ProjectFilters, ProjectStats, ProjectStorage
from .routes import register_project_routes
from .settings import SettingsManager, SettingsData, RecentProject
from .annotations import (
    AnnotationManager,
    AnnotationsData,
    Annotation,
    AnnotationCollection,
    AnnotationType,
    ColorScheme,
    HighlightRange,
)

__all__ = [
    "ProjectManager",
    "ProjectInfo",
    "ProjectManifest",
    "ProjectFilters",
    "ProjectStats",
    "ProjectStorage",
    "register_project_routes",
    "SettingsManager",
    "SettingsData",
    "RecentProject",
    "AnnotationManager",
    "AnnotationsData",
    "Annotation",
    "AnnotationCollection",
    "AnnotationType",
    "ColorScheme",
    "HighlightRange",
]
