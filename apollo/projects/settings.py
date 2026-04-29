"""Global settings management for Apollo."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


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


# Metadata fields read from each plugin's ``plugin.md`` front-matter.
# Anything outside this set is ignored to keep the surface area small.
_PLUGIN_MANIFEST_FIELDS = ("description", "version", "url", "author")


def _sha256_of_file(path: Path) -> Optional[str]:
    """Return the SHA-256 hex digest of ``path``, or ``None`` on error.

    SHA-256 (not SHA-1) is used because SHA-1 has known practical
    collisions (SHAttered, 2017) and is no longer recommended for
    integrity / supply-chain verification.
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _read_plugin_config(config_path: Path) -> dict:
    """Read a plugin's ``config.json`` and return it as a dict.

    Returns an empty dict for missing or unreadable files. Malformed
    JSON is logged and treated as empty so a broken config never blocks
    plugin discovery — the plugin will fall back to its
    ``DEFAULT_CONFIG``.
    """
    if not config_path.exists():
        return {}
    try:
        with open(config_path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("plugin config %s is not a JSON object; ignoring", config_path)
        return {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("failed to read plugin config %s: %s", config_path, exc)
        return {}


def _read_plugin_manifest(manifest_path: Path) -> dict:
    """Parse a plugin's ``plugin.md`` and return the metadata dict.

    The file uses Markdown with a YAML front-matter block::

        ---
        description: Short, one-paragraph summary.
        version: 1.0.0
        url: https://example.com/plugin
        author: Author Name or Company
        ---

        Optional human-readable body...

    Missing or unreadable manifests yield empty strings for every field
    so the UI always has something to render.
    """
    meta = {k: "" for k in _PLUGIN_MANIFEST_FIELDS}
    if not manifest_path.exists():
        return meta
    try:
        import frontmatter  # python-frontmatter; already a project dep
        post = frontmatter.load(str(manifest_path))
        for key in _PLUGIN_MANIFEST_FIELDS:
            value = post.metadata.get(key)
            if value is not None:
                meta[key] = str(value).strip()
    except Exception:
        # Malformed front-matter must not break settings load — leave
        # the fields empty and let the UI surface that.
        pass
    return meta


def detect_installed_plugins() -> dict:
    """Inspect ``plugins/`` and return one entry per detected plugin.

    Each entry has the shape::

        {
            "installed":   True,
            "description": "...",
            "version":     "...",
            "url":         "...",
            "author":      "...",
            "sha256":      "<sha256 of parser.py>",  # "" if unreadable
            "config":      {...},  # contents of the plugin's config.json
        }

    Subpackage plugins (``plugins/<name>/__init__.py``) and single-file
    plugins (``plugins/<name>.py``) are both recognised. Subpackage
    plugins should ship a ``plugin.md`` manifest (see
    ``guides/making_plugins.md``); single-file plugins look for a
    sibling ``<name>.plugin.md``.

    The ``config`` field is the on-disk ``config.json`` for the plugin
    (or ``<name>.config.json`` next to a single-file plugin). Missing
    or malformed configs become an empty dict. User overrides stored
    under ``data/settings.json`` → ``plugins[<name>].config`` are NOT
    merged here; use :func:`load_plugin_config` for the merged view.
    """
    root = Path(__file__).parent.parent.parent  # Apollo project root
    plugins_dir = root / "plugins"
    result: dict = {}
    if not plugins_dir.exists():
        return result

    for entry in sorted(plugins_dir.iterdir()):
        name = entry.name
        if name.startswith("_") or name.startswith("."):
            continue

        if entry.is_dir() and (entry / "__init__.py").exists():
            manifest = entry / "plugin.md"
            parser_file = entry / "parser.py"
            config_file = entry / "config.json"
            meta = _read_plugin_manifest(manifest)
            digest = _sha256_of_file(parser_file) if parser_file.exists() else ""
            cfg = _read_plugin_config(config_file)
            result[name] = {"installed": True, **meta, "sha256": digest or "", "config": cfg}

        elif entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
            stem = entry.stem
            manifest = plugins_dir / f"{stem}.plugin.md"
            config_file = plugins_dir / f"{stem}.config.json"
            meta = _read_plugin_manifest(manifest)
            digest = _sha256_of_file(entry) or ""
            cfg = _read_plugin_config(config_file)
            result[stem] = {"installed": True, **meta, "sha256": digest, "config": cfg}

    return result


def load_plugin_config(name: str, settings_path: Optional[Union[str, Path]] = None) -> dict:
    """Return the merged config for plugin *name*.

    Order of precedence (later wins):

    1. The plugin's on-disk ``config.json`` (or ``<name>.config.json``
       for single-file plugins).
    2. User overrides stored in ``data/settings.json`` under
       ``plugins[<name>].config``.

    Returns ``{}`` if the plugin has neither an on-disk config nor a
    user override (e.g. a third-party plugin that doesn't ship one).

    User overrides intentionally live in the global settings file —
    NOT in the plugin's own folder — so that upgrading a plugin (which
    overwrites its bundled ``config.json``) cannot clobber what the
    user chose. Stale keys carried over from an older plugin version
    are silently dropped during merge; a one-line warning is logged.

    **Description siblings (`_<key>`)** are dropped from the returned
    dict. By convention each runtime knob can ship a sibling key
    prefixed with ``_`` whose value is a human-readable description
    used by the Settings UI for tooltips/labels. Those keys are not
    runtime data, so the parser must never see them.
    """
    root = Path(__file__).parent.parent.parent
    plugins_dir = root / "plugins"

    on_disk: dict = {}
    sub_cfg = plugins_dir / name / "config.json"
    single_cfg = plugins_dir / f"{name}.config.json"
    if sub_cfg.exists():
        on_disk = _read_plugin_config(sub_cfg)
    elif single_cfg.exists():
        on_disk = _read_plugin_config(single_cfg)

    # Read user overrides directly from settings.json so we don't pull
    # in the full SettingsManager (avoids circular init in tests).
    override: dict = {}
    if settings_path is None:
        settings_path = root / "data" / "settings.json"
    settings_path = Path(settings_path)
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                data = json.load(f)
            override = (
                data.get("plugins", {}).get(name, {}).get("config", {}) or {}
            )
            if not isinstance(override, dict):
                override = {}
        except (OSError, json.JSONDecodeError, ValueError):
            override = {}

    merged = dict(on_disk)
    stale: list[str] = []
    for k, v in override.items():
        if on_disk and k not in on_disk:
            # Drop stale keys from older config versions, but warn.
            stale.append(k)
            continue
        merged[k] = v
    if stale:
        logger.warning(
            "plugin %r: dropping stale override keys not in on-disk config.json: %s",
            name, sorted(stale),
        )
    # Strip `_<key>` description siblings — they are documentation for
    # the Settings UI, never runtime values.
    return {k: v for k, v in merged.items() if not k.startswith("_")}


@dataclass
class SettingsData:
    """Global Apollo settings."""
    chat: dict = field(default_factory=lambda: {"default_model": "grok-4-1-fast-non-reasoning"})
    default_backend: str = "json"  # "json" or "cblite"
    cblite_storage_root: Optional[str] = None  # Optional path for global CBL storage
    recent_projects: list[RecentProject] = field(default_factory=list)
    # Map of plugin name → metadata (currently just ``{"installed": True}``).
    # Auto-populated from the ``plugins/`` directory on load.
    plugins: dict = field(default_factory=dict)

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
        """Load settings from disk or create defaults.

        Always refreshes the ``plugins`` section from the live
        ``plugins/`` directory so the file mirrors what is actually
        installed. If that section changed, it is persisted back.
        """
        loaded: SettingsData
        if self.path.exists():
            try:
                with open(self.path) as f:
                    data = json.load(f)
                loaded = SettingsData.from_dict(data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("failed to load settings from %s: %s; using defaults", self.path, e)
                loaded = SettingsData()
        else:
            loaded = SettingsData()

        # See ``web.server._load_settings`` for the rationale: the
        # ``config`` slot inside each plugin entry is reserved for user
        # overrides (PATCH-driven), so we must NOT mirror the on-disk
        # ``config.json`` into it — that would clobber overrides on the
        # next load. Strip ``config`` from detected metadata and only
        # carry forward whatever the user previously set.
        detected = detect_installed_plugins()
        existing = loaded.plugins or {}
        new_plugins: dict = {}
        for name, meta in detected.items():
            entry = {k: v for k, v in meta.items() if k != "config"}
            user_override = (existing.get(name) or {}).get("config")
            if isinstance(user_override, dict) and user_override:
                entry["config"] = user_override
            new_plugins[name] = entry
        if loaded.plugins != new_plugins:
            loaded.plugins = new_plugins
            self._data = loaded
            try:
                self.save()
            except OSError:
                # Saving is best-effort here; the in-memory value is correct.
                pass
        return loaded

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
