# Release Checklist — `release-x.x.x`

Use this checklist every time you cut a new release. Replace `x.x.x` with the
actual version number (e.g. `1.0.0`).

* * *

## 1. Create the release branch

```bash
git checkout main && git pull
git checkout -b release-x.x.x
```

* * *

## 2. Bump version strings

### Python

| File | Location | What to change |
| --- | --- | --- |
| `main.py` | line ~24 | `__version__ = "x.x.x"` |

### Web UI (Browser)

| File | Location | What to change |
| --- | --- | --- |
| `web/static/js/sidebar.js` | ~line 50 | `version: "vx.x.x"` or version display element |

### Documentation

| File | What to change |
| --- | --- |
| `README.md` | Any mentions of current version in title or usage |

* * *

## 3. Update RELEASE_NOTES.md (if exists)

Create or update `RELEASE_NOTES.md` at the root with a new section **at the top**:

```markdown
## vx.x.x — YYYY-MM-DD

### New Features
- …

### Bug Fixes
- …

### Changes
- **Version bump** — All version references updated from vOLD to vx.x.x.
```

* * *

## 4. Update README.md

- If the README has a version badge or title, update it to reflect `vx.x.x`.
- Add / revise any sections describing new features, changed behavior, or removed functionality.
- Verify architecture diagrams (if any) are still accurate.

* * *

## 5. Run & verify unit tests

```bash
# Full suite with verbose output
pytest tests/ -v --tb=short

# With coverage (optional)
pytest tests/ -v --tb=short --cov=apollo --cov-report=term-missing
```

**All tests must pass.** Fix any failures before continuing.

* * *

## 6. Run linters (if using ruff/black)

```bash
ruff check . --select=E9,F63,F7,F82
ruff format --check .
```

Fix any issues before continuing.

* * *

## 7. Verify Docker build (if applicable)

```bash
docker compose build
docker compose up -d apollo
# Smoke-test: confirm the service starts and logs the new version
docker compose logs apollo | head -20
docker compose down
```

* * *

## 8. Final review checklist

- [ ] `__version__` in `main.py` matches `x.x.x`
- [ ] Browser version display (if any) shows `vx.x.x`
- [ ] `README.md` updated with new version
- [ ] `RELEASE_NOTES.md` has new section at the top
- [ ] All HTML/JS files checked for stale version strings
- [ ] `pytest` passes (all green)
- [ ] `ruff` passes (if applicable)
- [ ] Docker image builds cleanly (if applicable)
- [ ] Startup logs show correct version
- [ ] No unrelated / uncommitted changes in the worktree

* * *

## 9. Merge & tag

```bash
# Commit release changes
git add main.py README.md web/static/js/sidebar.js guides/RELEASE.md
# Include any other changed files (RELEASE_NOTES.md, docs, images, etc.)
git commit -m "release: vx.x.x"

# Merge into main
git checkout main && git merge release-x.x.x

# Tag
git tag -a vx.x.x -m "vx.x.x"
git push origin main --tags
```

* * *

## 10. Post-release

- Create a GitHub Release from the tag, paste the `RELEASE_NOTES.md` section.
- Delete the `release-x.x.x` branch if no longer needed.
- Optionally bump `__version__` on `main` to the next dev version (e.g., `x.x+1.0-dev`).

* * *

# Best Practices

## Semantic Versioning

Follow [semver](https://semver.org/) strictly:

| Bump | When |
| --- | --- |
| **MAJOR** (`2.0.0`) | Breaking changes — schema changes, removed CLI flags, renamed API endpoints. |
| **MINOR** (`1.1.0`) | New features that are backward-compatible — new config keys, new output modes. |
| **PATCH** (`1.0.1`) | Bug fixes, doc corrections — no new behavior. |

## Version Display

The Apollo CLI and web UI automatically log/display their version on startup from `__version__` in `main.py`. Make sure both the Python backend and browser client report the same version to users.

## Backward Compatibility

- New config keys should have sensible defaults.
- If renaming or restructuring config, add migration logic and document it in release notes.
- The graph index format should remain backward-compatible if possible.

## Testing

- **Unit tests** — run the full suite (`pytest tests/ -v`)
- **Smoke tests** — verify the app starts and logs the correct version
- **Admin UI** — click through the browser dashboard if applicable
- **Docker** — test the Docker image separately if you changed the Dockerfile or dependencies

## Git Tags

- **Always use annotated tags**: `git tag -a vx.x.x -m "vx.x.x"`
- **GitHub Releases** should include the release notes section for easy browsing

## Hotfix Process

For critical bugs discovered after release:

```bash
git checkout vx.x.x          # Start from the release tag
git checkout -b hotfix-x.x.1
# Fix, test, bump to x.x.1
git tag -a vx.x.1 -m "vx.x.1"
git checkout main && git merge hotfix-x.x.1
git push origin main --tags
```

Hotfix releases are always **PATCH** bumps.

* * *

## Footer

For questions or clarifications, refer to the [PouchPipes release guide](https://github.com/Fujio-Turner/PouchPipes/blob/main/guides/RELEASE.md).
