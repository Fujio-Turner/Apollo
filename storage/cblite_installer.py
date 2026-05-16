"""Download / install / activate libcblite shared-library binaries.

The Web UI's *Settings → Storage* tab calls into this module to let the
user pick a Couchbase Lite C version + edition (Community / Enterprise)
without having to hunt down the right zip on packages.couchbase.com and
juggle ``CBLITE_LIB_PATH`` by hand.

Layout on disk
--------------

Everything lives in ``<repo-root>/storage_binary/``::

    storage_binary/
        libcblite-4.0.3-community-macos/
            libcblite-4.0.3/
                lib/libcblite.4.0.3.dylib   ← what we point CBLITE_LIB_PATH at
                lib/libcblite.dylib         (symlink)
                include/...
        libcblite-4.0.3-enterprise-windows-x86_64/
            libcblite-4.0.3/
                bin/cblite.dll              ← Windows lib
                lib/cblite.lib

The active library path is persisted in ``data/settings.json`` under
``storage.cblite_lib_path``. ``main.py`` and ``web/server.py`` read that
at startup and stuff it into ``os.environ['CBLITE_LIB_PATH']`` before
the first CBL load.
"""

from __future__ import annotations

import json
import platform
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_ROOT = REPO_ROOT / "storage_binary"
CBLITE_CONFIG_PATH = REPO_ROOT / "cblite_config.json"

PACKAGES_BASE = "https://packages.couchbase.com/releases/couchbase-lite-c"

# Versions surfaced in the UI dropdown. Keep the list short and curated —
# Apollo only ships ctypes bindings for the 4.x C API.
KNOWN_VERSIONS = ["4.0.3"]
DEFAULT_VERSION = "4.0.3"
EDITIONS = ("community", "enterprise")


# --------------------------------------------------------------------------- #
# Platform detection                                                          #
# --------------------------------------------------------------------------- #

def detect_platform() -> dict:
    """Return the running OS / architecture and the libcblite *platform tag*
    used in the packages.couchbase.com URL.

    ``platform_tag`` is the substring that goes between ``{VERSION}-`` and
    ``.zip``/``.tar.gz`` in the download URL — e.g. ``macos`` or
    ``windows-x86_64``. ``None`` means the current platform isn't supported
    by Apollo's installer (e.g. Linux ARM64).
    """
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin":
        tag = "macos"  # universal binary
        archive_ext = "zip"
    elif system == "Windows":
        # Couchbase only publishes x86_64 desktop builds; ARM64 Windows
        # users fall back to the manual download flow.
        tag = "windows-x86_64" if machine in ("amd64", "x86_64") else None
        archive_ext = "zip"
    elif system == "Linux":
        tag = "linux-x86_64" if machine in ("x86_64", "amd64") else None
        archive_ext = "tar.gz"
    else:
        tag, archive_ext = None, None

    return {
        "system": system,            # 'Darwin' | 'Windows' | 'Linux'
        "os_label": {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(system, system),
        "machine": machine,
        "platform_tag": tag,         # None if unsupported
        "archive_ext": archive_ext,  # 'zip' | 'tar.gz'
        "supported": tag is not None,
    }


# --------------------------------------------------------------------------- #
# URL + path helpers                                                          #
# --------------------------------------------------------------------------- #

def download_url(version: str, edition: str, platform_tag: str, archive_ext: str) -> str:
    return f"{PACKAGES_BASE}/{version}/couchbase-lite-c-{edition}-{version}-{platform_tag}.{archive_ext}"


def install_dir_for(version: str, edition: str, platform_tag: str) -> Path:
    """Where the archive for one (version, edition, platform) is extracted."""
    return INSTALL_ROOT / f"libcblite-{version}-{edition}-{platform_tag}"


def lib_path_for(version: str, edition: str, platform_tag: str) -> Path:
    """Absolute path to the actual shared library inside an extracted install."""
    base = install_dir_for(version, edition, platform_tag) / f"libcblite-{version}"
    if platform_tag.startswith("windows"):
        return base / "bin" / "cblite.dll"
    if platform_tag.startswith("linux"):
        # Multi-arch dir; pick the only subdir under lib/.
        lib = base / "lib"
        for sub in sorted(lib.iterdir()) if lib.exists() else []:
            if sub.is_dir():
                cand = sub / f"libcblite.so.{version.split('.')[0]}.{version.split('.')[1]}.{version.split('.')[2]}"
                if cand.exists():
                    return cand
        return base / "lib" / "x86_64-linux-gnu" / f"libcblite.so.{version}"
    # macOS
    return base / "lib" / f"libcblite.{version}.dylib"


# --------------------------------------------------------------------------- #
# Listing / activation                                                        #
# --------------------------------------------------------------------------- #

def list_installed() -> list[dict]:
    """Scan ``storage_binary/`` and return one entry per detected install.

    Each entry has ``version``, ``edition``, ``platform_tag``, ``lib_path``,
    and ``exists`` (True if the .dylib/.dll/.so is actually present).
    """
    out: list[dict] = []
    if not INSTALL_ROOT.exists():
        return out
    for child in sorted(INSTALL_ROOT.iterdir()):
        if not child.is_dir() or not child.name.startswith("libcblite-"):
            continue
        # name = libcblite-{version}-{edition}-{platform_tag}
        rest = child.name[len("libcblite-"):]
        try:
            version, edition, *tag_parts = rest.split("-")
            platform_tag = "-".join(tag_parts)
        except ValueError:
            continue
        if edition not in EDITIONS or not platform_tag:
            continue
        lib = lib_path_for(version, edition, platform_tag)
        out.append({
            "version": version,
            "edition": edition,
            "platform_tag": platform_tag,
            "install_dir": str(child),
            "lib_path": str(lib),
            "exists": lib.exists(),
        })
    return out


def get_active_lib_path() -> str | None:
    """Read ``data/settings.json`` for the persisted active library path.

    Falls back to ``$CBLITE_LIB_PATH`` so that users who set the env var
    manually still see something sensible in the UI.
    """
    settings_path = REPO_ROOT / "data" / "settings.json"
    try:
        with open(settings_path) as f:
            s = json.load(f) or {}
        path = ((s.get("storage") or {}).get("cblite_lib_path") or "").strip()
        if path:
            return path
    except (OSError, ValueError):
        pass
    import os
    return os.environ.get("CBLITE_LIB_PATH") or None


def set_active_lib_path(lib_path: str, *, edition: str | None = None, version: str | None = None) -> None:
    """Persist ``lib_path`` to ``data/settings.json`` and update
    ``cblite_config.json`` so the mismatch detector stays in sync.

    Note: this does *not* mutate the current process's
    ``os.environ['CBLITE_LIB_PATH']`` — once libcblite has been loaded in
    a Python process, ctypes can't unload it. Activation takes effect on
    the next server restart.
    """
    settings_path = REPO_ROOT / "data" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(settings_path) as f:
            settings = json.load(f) or {}
    except (OSError, ValueError):
        settings = {}

    storage = settings.setdefault("storage", {})
    storage["cblite_lib_path"] = str(lib_path)
    if edition:
        storage["edition"] = edition
    if version:
        storage["version"] = version

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    # Mirror into cblite_config.json so the EE/CE mismatch banner agrees
    # with what the user just chose.
    if edition or version:
        try:
            with open(CBLITE_CONFIG_PATH) as f:
                cfg = json.load(f) or {}
        except (OSError, ValueError):
            cfg = {}
        if edition:
            cfg["edition"] = edition
        if version:
            cfg["version"] = version
        with open(CBLITE_CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)


def apply_env_from_settings() -> str | None:
    """Set ``CBLITE_LIB_PATH`` in the current process from settings.json.

    Called from ``main.py`` and ``web/server.py`` *before* CBL is first
    loaded. A pre-existing env var wins so users can still override on
    the command line.
    """
    import os
    if os.environ.get("CBLITE_LIB_PATH"):
        return os.environ["CBLITE_LIB_PATH"]
    path = get_active_lib_path()
    if path and Path(path).exists():
        os.environ["CBLITE_LIB_PATH"] = path
        return path
    return None


# --------------------------------------------------------------------------- #
# Download + extract                                                          #
# --------------------------------------------------------------------------- #

def install(version: str, edition: str, *, progress=None) -> dict:
    """Download + extract one (version, edition) for the current platform.

    Returns a dict with the resolved ``lib_path`` and ``install_dir``.
    Raises ``RuntimeError`` on any failure (HTTP error, unsupported
    platform, missing files inside archive, …).

    ``progress`` is an optional callable taking ``(stage: str, pct: int)``.
    """
    plat = detect_platform()
    if not plat["supported"]:
        raise RuntimeError(
            f"Automatic install is not supported on {plat['os_label']} / {plat['machine']}. "
            "Download libcblite manually from https://www.couchbase.com/downloads/?family=couchbase-lite "
            "and set CBLITE_LIB_PATH."
        )
    if edition not in EDITIONS:
        raise ValueError(f"edition must be one of {EDITIONS}, got {edition!r}")

    tag = plat["platform_tag"]
    ext = plat["archive_ext"]
    url = download_url(version, edition, tag, ext)
    target_dir = install_dir_for(version, edition, tag)
    target_dir.mkdir(parents=True, exist_ok=True)

    if progress: progress("downloading", 0)
    archive_path = target_dir / f"_download.{ext}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(archive_path, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            read = 0
            chunk = 1 << 15  # 32 KiB
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                out.write(buf)
                read += len(buf)
                if progress and total:
                    pct = min(95, int(read * 95 / total))
                    progress("downloading", pct)
    except Exception as exc:
        # Leave the empty target_dir behind so the UI can clean it up.
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RuntimeError(f"Download failed: {url}\n  {exc}") from exc

    if progress: progress("extracting", 96)
    try:
        if ext == "zip":
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(target_dir)
        else:
            with tarfile.open(archive_path) as tf:
                tf.extractall(target_dir)
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to extract {archive_path.name}: {exc}") from exc
    finally:
        try:
            archive_path.unlink()
        except OSError:
            pass

    lib = lib_path_for(version, edition, tag)
    if not lib.exists():
        # Archive layout changed — surface enough info to debug.
        found = [str(p.relative_to(target_dir)) for p in target_dir.rglob("*")
                 if p.suffix in (".dylib", ".dll", ".so") or ".so." in p.name]
        raise RuntimeError(
            f"Extracted archive but couldn't find the expected library at {lib}. "
            f"Found instead: {found[:5]}"
        )

    if progress: progress("done", 100)
    return {
        "version": version,
        "edition": edition,
        "platform_tag": tag,
        "install_dir": str(target_dir),
        "lib_path": str(lib),
    }


def uninstall(install_dir: str) -> None:
    """Delete an extracted install directory after a safety check."""
    p = Path(install_dir).resolve()
    if INSTALL_ROOT.resolve() not in p.parents:
        raise ValueError(f"Refusing to delete {p}: outside {INSTALL_ROOT}")
    shutil.rmtree(p, ignore_errors=False)


# --------------------------------------------------------------------------- #
# First-run bootstrap                                                         #
# --------------------------------------------------------------------------- #

def ensure_default_install(*, log=print) -> str | None:
    """If nothing is installed yet, download the default CE build for the
    current platform. Returns the active lib path (or None on failure).

    Called from ``main.py`` at startup so a fresh clone *just works* the
    first time the user runs ``python main.py serve --backend cblite``.
    """
    active = get_active_lib_path()
    if active and Path(active).exists():
        return active

    installed = [i for i in list_installed() if i["exists"]]
    if installed:
        # Auto-activate the first existing install if nothing is selected.
        pick = installed[0]
        set_active_lib_path(pick["lib_path"], edition=pick["edition"], version=pick["version"])
        return pick["lib_path"]

    plat = detect_platform()
    if not plat["supported"]:
        log(f"[cblite-installer] {plat['os_label']} is not supported by the auto-installer; skipping.")
        return None

    log(f"[cblite-installer] First-run: downloading libcblite {DEFAULT_VERSION} community for {plat['os_label']}…")
    try:
        info = install(DEFAULT_VERSION, "community")
    except Exception as exc:
        log(f"[cblite-installer] Auto-install failed: {exc}")
        return None
    set_active_lib_path(info["lib_path"], edition="community", version=DEFAULT_VERSION)
    log(f"[cblite-installer] Installed at {info['lib_path']}")
    return info["lib_path"]
