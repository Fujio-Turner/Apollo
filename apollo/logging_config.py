"""
Apollo's central logging configuration.

This is the only place that touches the root logger, attaches handlers,
or reads ``APOLLO_LOG_*`` environment variables. Library code never
calls :func:`logging.basicConfig` and never reads these env vars
directly. See ``guides/LOGGING.md`` for the full standard.

Entrypoints (``main.py``, ``web/server.py``, ``watcher.py`` if launched
standalone) call :func:`configure_logging` exactly once at startup.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Optional


_ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%S"
_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DEFAULT_DATEFMT = _ISO8601_FORMAT

# Default file destination if ``APOLLO_LOG_FILE`` is not set. Lives under
# the project's ``.apollo/`` directory (which is gitignored) so logs are
# always captured for support without operators having to opt in.
DEFAULT_LOG_FILE = ".apollo/logs/apollo.log"

# Default rotation settings, matching guides/LOGGING.md § 9.
DEFAULT_MAX_SIZE_MB = 100
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_ROTATED_TOTAL_MB = 1024

# Sentinel values that disable file logging when assigned to APOLLO_LOG_FILE.
_DISABLED_VALUES = {"", "off", "none", "disable", "disabled", "0", "false", "no"}

# Third-party loggers that are noisy by default. We pin them at WARNING
# unless the operator explicitly bumps the root level lower than that.
_NOISY_THIRD_PARTY = ("watchdog", "urllib3", "httpx", "uvicorn.access")


class _JsonFormatter(logging.Formatter):
    """One-record-per-line JSON formatter for log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, _ISO8601_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ManagedRotatingFileHandler(logging.handlers.BaseRotatingHandler):
    """
    Size-based rollover with two extra caps applied after each rotation:

    - ``max_age_days``: prune rotated files older than this.
    - ``total_mb_cap``: keep the sum of all rotated files under this budget.

    The active file is never pruned, only renamed on rollover. Rotated
    files are named ``<base>.<YYYYMMDD-HHMMSS>.log`` so they sort
    lexicographically by rotation time, matching the oldest-first
    deletion logic.

    See ``guides/LOGGING.md § 9`` for the design rationale.
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int,
        max_age_days: int,
        total_mb_cap: int,
        encoding: str = "utf-8",
    ) -> None:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(filename, mode="a", encoding=encoding, delay=False)
        self._max_bytes = max(0, int(max_bytes))
        self._max_age_seconds = max(0, int(max_age_days)) * 86_400
        self._total_bytes_cap = max(0, int(total_mb_cap)) * 1024 * 1024

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._max_bytes <= 0 or self.stream is None:
            return False
        try:
            msg = self.format(record) + self.terminator
            self.stream.seek(0, 2)  # end of file
            return self.stream.tell() + len(msg.encode(self.encoding or "utf-8")) >= self._max_bytes
        except Exception:
            return False

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        # Use ISO-8601 format with dashes instead of colons (colons invalid in filenames on some systems)
        ts_format = _ISO8601_FORMAT.replace(":", "-")
        rotated = f"{self.baseFilename}.{time.strftime(ts_format)}.log"
        try:
            os.replace(self.baseFilename, rotated)
        except FileNotFoundError:
            pass
        self.stream = self._open()
        self._enforce_caps()

    def _enforce_caps(self) -> None:
        base = Path(self.baseFilename)
        rotated = sorted(
            base.parent.glob(f"{base.name}.*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        now = time.time()

        # 1. Age-based pruning.
        if self._max_age_seconds > 0:
            for p in list(rotated):
                try:
                    if now - p.stat().st_mtime > self._max_age_seconds:
                        p.unlink(missing_ok=True)
                        rotated.remove(p)
                except OSError:
                    continue

        # 2. Total-size budget (delete oldest first).
        if self._total_bytes_cap > 0:
            try:
                total = sum(p.stat().st_size for p in rotated)
            except OSError:
                return
            for p in rotated:
                if total <= self._total_bytes_cap:
                    break
                try:
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    total -= size
                except OSError:
                    continue


def _build_formatter() -> logging.Formatter:
    if os.environ.get("APOLLO_LOG_JSON", "0") == "1":
        return _JsonFormatter()
    return logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT)


def resolve_log_file_path(settings: Optional[dict] = None) -> Optional[str]:
    """Return the active log file path, or ``None`` if file logging is disabled.

    Precedence (highest first):

    1. ``settings["path"]`` from the UI / ``data/settings.json``, when non-empty.
    2. ``APOLLO_LOG_FILE`` environment variable.
    3. :data:`DEFAULT_LOG_FILE` (``.apollo/logs/apollo.log``).

    Any value matching :data:`_DISABLED_VALUES` (``""``, ``off``, ``none``,
    ``disable``, ``0``, ``false``, ``no``) disables file logging entirely.
    """
    if settings:
        ui_path = settings.get("path")
        if isinstance(ui_path, str) and ui_path.strip():
            if ui_path.strip().lower() in _DISABLED_VALUES:
                return None
            return ui_path

    raw = os.environ.get("APOLLO_LOG_FILE")
    if raw is None:
        return DEFAULT_LOG_FILE
    if raw.strip().lower() in _DISABLED_VALUES:
        return None
    return raw


def _resolve_int(settings: Optional[dict], key: str, env_name: str, default: int) -> int:
    """Resolve a numeric setting using settings.json → env var → default."""
    if settings is not None:
        v = settings.get(key)
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
        if isinstance(v, str) and v.strip():
            try:
                return max(0, int(v))
            except ValueError:
                pass
    try:
        return int(os.environ.get(env_name, str(default)))
    except ValueError:
        return default


def _resolve_level(settings: Optional[dict]) -> str:
    if settings:
        v = settings.get("level")
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return os.environ.get("APOLLO_LOG_LEVEL", "INFO").upper()


def _resolve_json_mode(settings: Optional[dict]) -> bool:
    if settings is not None and "json_mode" in settings:
        return bool(settings.get("json_mode"))
    return os.environ.get("APOLLO_LOG_JSON", "0") == "1"


_FILE_HANDLER_TAG = "_apollo_rotating_file_handler"
_STDERR_HANDLER_TAG = "_apollo_stderr_handler"


def _attach_rotating_file_handler(
    root: logging.Logger,
    formatter: logging.Formatter,
    settings: Optional[dict] = None,
) -> None:
    path = resolve_log_file_path(settings)
    if not path:
        return
    try:
        handler = ManagedRotatingFileHandler(
            filename=path,
            max_bytes=_resolve_int(settings, "max_size_mb", "APOLLO_LOG_MAX_SIZE_MB",
                                   DEFAULT_MAX_SIZE_MB) * 1024 * 1024,
            max_age_days=_resolve_int(settings, "max_age_days", "APOLLO_LOG_MAX_AGE_DAYS",
                                      DEFAULT_MAX_AGE_DAYS),
            total_mb_cap=_resolve_int(settings, "rotated_total_mb",
                                      "APOLLO_LOG_ROTATED_TOTAL_MB",
                                      DEFAULT_ROTATED_TOTAL_MB),
        )
    except Exception as exc:  # pragma: no cover - filesystem failures
        # Falling back to stderr-only is preferable to crashing startup.
        root.warning("could not attach log file handler for %s: %s", path, exc)
        return
    handler.setFormatter(formatter)
    setattr(handler, _FILE_HANDLER_TAG, True)
    root.addHandler(handler)


def _remove_tagged_handler(root: logging.Logger, tag: str) -> None:
    for h in list(root.handlers):
        if getattr(h, tag, False):
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)


def apply_settings(settings: Optional[dict]) -> None:
    """Reapply logging configuration from a UI / settings.json snapshot.

    Removes the previously-attached rotating file handler (and stderr
    handler) and rebuilds them from the current effective settings. Safe
    to call at any time — typically invoked from the ``PUT /api/settings``
    handler so browser changes take effect without a restart.
    """
    root = logging.getLogger()

    formatter = _build_formatter() if not _resolve_json_mode(settings) else _JsonFormatter()

    # Replace stderr handler so the formatter (text/JSON) reflects the new setting.
    _remove_tagged_handler(root, _STDERR_HANDLER_TAG)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    setattr(stderr_handler, _STDERR_HANDLER_TAG, True)
    root.addHandler(stderr_handler)

    # Replace rotating file handler.
    _remove_tagged_handler(root, _FILE_HANDLER_TAG)
    _attach_rotating_file_handler(root, formatter, settings)

    # Apply level last so partial failure above doesn't leave us silenced.
    root.setLevel(_resolve_level(settings))

    root._apollo_configured = True  # type: ignore[attr-defined]


def get_logging_info(settings: Optional[dict] = None) -> dict:
    """Return a snapshot of the logging configuration and on-disk state.

    Used by the web UI's Settings → Logging tab so users can see where
    Apollo is writing logs, how big the active file is, and how many
    rotated files are retained. The returned dict is JSON-serialisable.

    Pass ``settings`` (the ``"logging"`` section from ``data/settings.json``)
    to reflect the user's UI choices in the snapshot.
    """
    level = _resolve_level(settings)
    json_mode = _resolve_json_mode(settings)
    path = resolve_log_file_path(settings)

    info: dict = {
        "level": level,
        "json_mode": json_mode,
        "path": path,
        "default_path": DEFAULT_LOG_FILE,
        "enabled": path is not None,
        "settings": {
            "max_size_mb": _resolve_int(
                settings, "max_size_mb", "APOLLO_LOG_MAX_SIZE_MB", DEFAULT_MAX_SIZE_MB
            ),
            "max_age_days": _resolve_int(
                settings, "max_age_days", "APOLLO_LOG_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS
            ),
            "rotated_total_mb": _resolve_int(
                settings, "rotated_total_mb", "APOLLO_LOG_ROTATED_TOTAL_MB",
                DEFAULT_ROTATED_TOTAL_MB
            ),
        },
        "active": None,
        "rotated": [],
        "rotated_total_bytes": 0,
        "directory": None,
        "configured": bool(getattr(logging.getLogger(), "_apollo_configured", False)),
        "env_overrides": {
            "APOLLO_LOG_FILE": os.environ.get("APOLLO_LOG_FILE"),
            "APOLLO_LOG_LEVEL": os.environ.get("APOLLO_LOG_LEVEL"),
            "APOLLO_LOG_JSON": os.environ.get("APOLLO_LOG_JSON"),
            "APOLLO_LOG_MAX_SIZE_MB": os.environ.get("APOLLO_LOG_MAX_SIZE_MB"),
            "APOLLO_LOG_MAX_AGE_DAYS": os.environ.get("APOLLO_LOG_MAX_AGE_DAYS"),
            "APOLLO_LOG_ROTATED_TOTAL_MB": os.environ.get("APOLLO_LOG_ROTATED_TOTAL_MB"),
        },
    }

    if path is None:
        return info

    base = Path(path)
    info["directory"] = str(base.parent)

    try:
        if base.exists():
            stat = base.stat()
            info["active"] = {
                "name": base.name,
                "path": str(base),
                "size_bytes": stat.st_size,
                "modified": int(stat.st_mtime),
            }
    except OSError as exc:
        info["active"] = {"name": base.name, "path": str(base), "error": str(exc)}

    try:
        if base.parent.exists():
            rotated_paths = sorted(
                base.parent.glob(f"{base.name}.*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,  # newest first for display
            )
            total = 0
            for p in rotated_paths:
                try:
                    stat = p.stat()
                except OSError:
                    continue
                total += stat.st_size
                info["rotated"].append({
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": stat.st_size,
                    "modified": int(stat.st_mtime),
                })
            info["rotated_total_bytes"] = total
    except OSError:
        pass

    return info


def configure_logging(
    level: Optional[str] = None,
    settings: Optional[dict] = None,
) -> None:
    """Set up Apollo's root logger. Idempotent — safe to call twice.

    Reads (in order of precedence):

    - explicit ``level`` argument
    - ``settings["level"]`` from the UI / settings.json
    - ``APOLLO_LOG_LEVEL`` env var
    - default ``"INFO"``

    Additional env vars: ``APOLLO_LOG_FILE``, ``APOLLO_LOG_JSON``,
    ``APOLLO_LOG_MAX_SIZE_MB``, ``APOLLO_LOG_MAX_AGE_DAYS``,
    ``APOLLO_LOG_ROTATED_TOTAL_MB``. See ``guides/LOGGING.md`` for the
    full reference.

    Pass ``settings`` (the ``"logging"`` section from
    ``data/settings.json``) so the browser-configured values take effect
    at startup. To re-apply settings later (e.g. after the user saves
    the form in the UI), call :func:`apply_settings` instead.
    """
    root = logging.getLogger()

    # Quiet down noisy third-party loggers we don't own (idempotent).
    for noisy in _NOISY_THIRD_PARTY:
        logging.getLogger(noisy).setLevel("WARNING")

    # Build / rebuild the handler chain from current settings.
    apply_settings(settings)

    if level:
        root.setLevel(level.upper())
