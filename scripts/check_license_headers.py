# SPDX-License-Identifier: BUSL-1.1
"""
Verify every first-party Python source file declares the project's
SPDX license identifier (``BUSL-1.1``) somewhere in its first few
lines.

Run locally:

    python3 scripts/check_license_headers.py

CI runs this as a required check (see ``.github/workflows/ci.yml``)
so a missing header fails the build before review.

Scope
-----
The check walks the working tree from the repo root and looks at every
``*.py`` file *except* those under directories listed in
``EXCLUDED_DIRS`` below — vendored or generated trees (``venv``,
``htmlcov``, ``target``…) are skipped to keep the signal clean.

A file passes if any of the first ``HEADER_LINES`` lines contains the
exact string ``HEADER_TOKEN``. We deliberately don't try to parse the
SPDX grammar — a substring match is enough for an enforced convention.

Exit codes:
    0  all files have the header
    1  one or more files missing the header (paths printed to stderr)
"""
from __future__ import annotations

import sys
from pathlib import Path

HEADER_TOKEN = "SPDX-License-Identifier: BUSL-1.1"
HEADER_LINES = 5  # only look at the top of each file

# Directory *names* (matched anywhere in the path) that should be
# skipped. These are either virtualenvs, build artifacts, generated
# coverage output, user-supplied content (``target/``), or local
# developer scratch space.
EXCLUDED_DIRS = {
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    "htmlcov",
    "target",
    "_dev_only",
    ".git",
    "build",
    "dist",
    ".apollo",
    ".graph_search",
    "node_modules",
}


def iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


def file_has_header(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(HEADER_LINES):
                line = fh.readline()
                if not line:
                    break
                if HEADER_TOKEN in line:
                    return True
    except OSError as exc:
        print(f"warning: could not read {path}: {exc}", file=sys.stderr)
        return False
    return False


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    missing: list[Path] = []
    checked = 0
    for py in iter_python_files(repo_root):
        checked += 1
        if not file_has_header(py):
            missing.append(py.relative_to(repo_root))

    if missing:
        print(
            f"License header check FAILED: {len(missing)} of {checked} "
            f"Python file(s) are missing '{HEADER_TOKEN}' in the first "
            f"{HEADER_LINES} lines.",
            file=sys.stderr,
        )
        for rel in sorted(missing):
            print(f"  {rel}", file=sys.stderr)
        print(
            "\nAdd this as the first non-shebang line of each listed file:\n"
            f"    # {HEADER_TOKEN}",
            file=sys.stderr,
        )
        return 1

    print(f"License header check OK: all {checked} Python files carry '{HEADER_TOKEN}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
