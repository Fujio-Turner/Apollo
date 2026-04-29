# PLAN — Per-Plugin `config.json` (Settings → Plugins, runtime-editable)

**Owner:** plugins / settings / web
**Status:** Phases 1, 2A, 2B & 2C done
**Tracking thread:** `T-019dd928-d858-73f8-b1d6-72da1f0c6a68`

---

## Goal

Every Apollo plugin owns a `config.json` that lives in its own folder.
The file declares at minimum `"enabled": true|false` (default `true`
for newly installed plugins) plus any plugin-specific knobs. End users
can flip those knobs from **Settings → Plugins** in the web UI and the
change takes effect in the running webserver without a restart.

---

## Phase 1 — Per-plugin `config.json` files + docs ✅ DONE

Tracked in this thread; see commit history / git status for the
exact diff. Summary:

- [x] `plugins/python3/config.json` — `enabled`, `extensions`,
      `extract_comments`, `comment_tags`, `extract_strings`,
      `extract_type_checking_imports`, `detect_patterns`,
      `known_patterns`, plus the **per-language ignore set**
      (`ignore_dirs`, `ignore_files`, `ignore_dir_markers`).
- [x] `plugins/markdown_gfm/config.json` — `enabled`, `extensions`,
      `max_file_size_bytes` (1 MB), and toggles for
      `extract_frontmatter` / `extract_sections` / `extract_code_blocks` /
      `extract_links` / `extract_wikilinks` / `extract_callouts` /
      `extract_tables` / `extract_task_items` / `extract_comments`,
      plus `comment_tags` and ignore keys.
- [x] `plugins/html5/config.json` — `enabled`, `extensions`,
      `max_file_size_bytes` (5 MB), `asset_tags` map, extraction
      toggles, `comment_tags`, ignore keys (`_site`, `public`,
      `.jekyll-cache`, `.docusaurus`, etc.).
- [x] `plugins/pdf_pypdf/config.json` — `enabled`, `extensions`,
      `max_file_size_bytes` (50 MB), `extract_pages`,
      `extract_outline`, `extract_metadata`,
      `decrypt_with_empty_password`, empty ignore keys.
- [x] `guides/making_plugins.md` — added new **§ 2.6 The plugin
      config (`config.json`)** including a dedicated
      **"Per-language directory ignores"** section, a row for the
      config in the file-tree diagram, a new step in the TL;DR list,
      and a checklist item.

**Universal baseline:** every config has `"enabled": true`. A newly
installed plugin must default to enabled.

**Per-language ignore baseline:** every config also carries
`ignore_dirs` / `ignore_files` / `ignore_dir_markers` (possibly
empty). The indexer will compose the union of *enabled* plugins'
ignore sets in Phase 2A — see § 2A below.

---

## Phase 2 — Wire configs into the runtime + UI

Three layers, each its own commit. Do them in order; each is testable
on its own.

### 2A. Loader — make merged configs reach the parsers ✅ DONE

Goal: parsers receive their config dict at construction time, and a
plugin with `enabled: false` is invisible to the graph builder.

**Touch points**

1. `apollo/projects/settings.py`
   - Extend `detect_installed_plugins()` to also read `config.json`
     (and `<name>.config.json` for single-file plugins) and include
     it on each plugin entry as `"config": {...}`.
   - Add a helper `load_plugin_config(name) -> dict` that returns
     the on-disk `config.json` merged with any user override stored
     in `data/settings.json` under `plugins[<name>].config`.
   - User overrides live in the global `data/settings.json` (not
     in the plugin's own folder) so plugin upgrades don't clobber
     them.

2. `plugins/__init__.py`
   - `discover_plugins()` should:
     1. resolve the merged config for each plugin name,
     2. skip the plugin entirely if `config.get("enabled") is False`,
     3. instantiate via `plugin_cls(config=merged)` when the
        constructor accepts a `config` kwarg, otherwise fall back
        to `plugin_cls()` for back-compat.

3. Each built-in parser (`plugins/{python3,markdown_gfm,html5,pdf_pypdf}/parser.py`)
   - Add a `DEFAULT_CONFIG` class attribute mirroring the on-disk
     `config.json` defaults.
   - `__init__(self, config: dict | None = None)` merges
     `DEFAULT_CONFIG` ⊕ `config or {}` and stores it on `self.config`.
   - Replace module-level constants used at runtime with reads from
     `self.config` where the user expects them tunable
     (`_MAX_FILE_SIZE`, extraction toggles, `comment_tags`, asset
     tag map, etc.). Module-level constants stay as the source of
     `DEFAULT_CONFIG` truth.
   - `can_parse()` returns `False` when `self.config["enabled"]` is
     falsy. (Belt-and-braces: `discover_plugins` already filters,
     but if a caller passes the parser explicitly the disable still
     applies.)

4. **Composed ignore set in `graph/builder.py`**
   - Replace the monolithic `_SKIP_DIRS` constant with:
     - a small **core skip list** that stays in the builder
       (`.git`, `_apollo`, `.apollo`, `_apollo_web`, plus generic
       cross-language entries like `build`, `dist`, `htmlcov`,
       `.idea`, `.vscode`), and
     - a **plugin-contributed skip list**, computed at indexing time
       as `union(plugin.config["ignore_dirs"] for plugin in enabled_plugins)`.
   - Same idea for `ignore_files` (glob list) and
     `ignore_dir_markers` (sentinel filenames — replaces the
     hardcoded `_VENV_MARKERS` tuple).
   - `GraphBuilder.__init__` should accept the merged ignore set
     from its caller (the same place `_build_parsers` is wired);
     the builder no longer hard-codes Python-specific names.
   - `_is_dir_included` checks (a) core skip, (b) plugin-contributed
     skip, then (c) user filter overrides as today.

5. Tests
   - Update `plugins/<name>/test_parser.py` to round-trip a custom
     `config={"enabled": False}` and assert `can_parse()` returns
     `False`.
   - Add `tests/test_plugin_ignore_dirs.py`:
     - When python3 is enabled, a synthetic project containing a
       `venv/` directory is **not** descended into.
     - When python3 is disabled, the same project's `venv/` **is**
       descended into (proves ignores follow the plugin).
   - Add a test in `tests/` covering the discover-with-disabled-plugin
     path end-to-end.

**Acceptance**
- `pytest -q` green.
- Disabling `python3` in `data/settings.json` causes a re-index of a
  Python project to fall back to the generic text indexer **and**
  removes Python's ignore folders from the skip set.
- Adding a brand-new plugin with `"ignore_dirs": ["my_thing"]` and
  reloading immediately stops the indexer from descending into
  `my_thing/` folders, with no edit to `graph/builder.py`.

---

### 2B. API — read & write plugin configs without a restart ✅ DONE

Goal: the front-end can list current configs and PATCH them, and the
running web server picks the new values up immediately.

**Touch points**

1. `web/server.py`
   - `GET /api/settings/plugins` — already implicit in
     `GET /api/settings`; verify the `config` field is included.
   - `PATCH /api/settings/plugins/<name>/config` — body is a partial
     dict of overrides. Validates:
     - keys exist in the on-disk `config.json`,
     - value types match (bool / int / str / list / dict),
     - `enabled` must be a bool when present.
   - Persists the override into `data/settings.json` under
     `plugins[<name>].config` via `SettingsManager`.
   - Calls a new `parsers_reload()` hook on the active
     `GraphBuilder` / project pipeline.

2. `main.py` (or wherever `_build_parsers` lives)
   - Expose a `reload_parsers()` function that re-runs
     `discover_plugins()` and swaps the parser list on the live
     `GraphBuilder` instance(s). Must be safe to call mid-flight
     (i.e. take a lock around mutation, finish in-flight parses
     before swap).

3. Tests
   - `tests/test_plugin_config_api.py` — happy path + invalid key
     + invalid type + flipping `enabled`.

**Acceptance**
- `curl -X PATCH .../api/settings/plugins/markdown_gfm/config -d '{"enabled":false}'`
  returns 200, and a subsequent index pass on a Markdown file falls
  through to the text indexer with no server restart.

---

### 2C. UI — render & edit configs under each plugin card ✅ DONE

Goal: every plugin card in **Settings → Plugins** shows an `enabled`
toggle and a collapsible "Settings" panel auto-rendered from the
plugin's config schema.

**Touch points**

1. `web/static/app.js` :: `renderPluginsList()`
   - Always-visible: an `enabled` toggle next to the status badge.
     Flipping it PATCHes `{"enabled": <bool>}` and re-renders.
   - Collapsible "Settings" panel rendering form controls by JSON
     value type:
     - `bool` → checkbox
     - `int` → number input
     - `string` → text input
     - `list[str]` → tag input (comma-separated for now is fine)
     - `dict` → read-only `<pre>` JSON view (no editor in v1)
     - `list[non-str]` → read-only JSON view (no editor in v1)
   - **Render every field's label and tooltip from the on-disk
     `_<key>` description sibling.** Each plugin's `config.json`
     ships a `"_foo": "<human-readable description>"` next to every
     runtime `"foo"` knob. The UI must:
     - skip `_<key>` keys when iterating fields to render,
     - look up the matching `_<key>` value as the field's label /
       help text (fall back to the bare key when missing),
     - never PATCH a `_<key>` (the API rejects them — they're
       read-only docs sourced from the plugin author's config.json).
   - "Save" button PATCHes the diff vs. the loaded values.
   - "Reset to defaults" button DELETEs the user override (which
     translates to `PATCH` with the on-disk default).

2. `web/static/index.html`
   - No new sections; the existing `#plugins-list` container is
     reused. We may add a tiny CSS rule for the new collapsible
     panel.

3. Manual smoke test
   - Open Settings → Plugins, flip `python3.enabled` off, confirm
     the badge updates, re-index a Python folder, see
     `functions == []` because the text indexer ran instead.

**Acceptance**
- Visually: every plugin card has a toggle and a Settings expander.
- Functionally: the toggle persists across reloads and immediately
  changes parser behaviour.

---

## Open questions / risks

- **Type evolution.** If a plugin author renames or removes a key in
  a later `config.json`, the user override in `data/settings.json`
  may carry stale keys. Loader strategy: silently drop stale keys
  during merge; surface a one-line warning in the server log.
- **Validation depth.** v1 only checks "key exists + type matches".
  Range checks (`max_file_size_bytes >= 0`) and enum constraints
  (`comment_tags ⊆ {TODO,FIXME,...}`) come later if needed.
- **Reload safety.** `reload_parsers()` must not race with an
  in-flight indexing pass. We may need a simple `threading.Lock`
  around the parser list inside the graph builder.

---

## Status log

- 2026-04-29 — Phase 1 complete: 4 `config.json` files added,
  `guides/making_plugins.md` updated with §2.6.
- 2026-04-29 — Scope expanded: each plugin's `config.json` now also
  declares `ignore_dirs` / `ignore_files` / `ignore_dir_markers`
  (per-language indexer ignores). Built-in lists for python3,
  markdown_gfm, html5, pdf_pypdf populated. Doc §2.6 expanded with
  a "Per-language directory ignores" subsection and a
  language-by-language reference table.
- 2026-04-29 — Phase 2A complete:
    * `apollo/projects/settings.py` — `detect_installed_plugins()`
      now includes each plugin's `config.json` under a `"config"`
      key; new `load_plugin_config(name)` helper merges on-disk
      defaults with user overrides from `data/settings.json`
      (stale override keys are dropped + logged).
    * `plugins/__init__.py` — `discover_plugins()` resolves the
      merged config per plugin, skips plugins with
      `enabled: False`, and forwards the merged config to
      constructors that accept a `config=` kwarg (back-compat
      preserved for plugins without one).
    * Each built-in parser (`python3`, `markdown_gfm`, `html5`,
      `pdf_pypdf`) gained a `DEFAULT_CONFIG` class attribute,
      a `__init__(self, config: dict | None = None)` that stores
      `self.config`, and runtime use of the configured knobs
      (`enabled`, `extensions`, `max_file_size_bytes`,
      `comment_tags`, `extract_*` toggles, `asset_tags`,
      `known_patterns`, `decrypt_with_empty_password`).
      `can_parse()` returns `False` when the plugin is disabled.
    * `graph/builder.py` — split the monolithic `_SKIP_DIRS` into a
      tiny core list (`_CORE_SKIP_DIRS`) plus a plugin-contributed
      union computed at index time by `_compose_ignore_set()`.
      `GraphBuilder` now uses `self._skip_dirs`,
      `self._ignore_file_globs`, and `self._venv_markers` (all
      derived from the enabled plugins' configs); the legacy
      `_SKIP_DIRS` constant is preserved as a backward-compat alias.
    * Tests added: `tests/test_plugin_ignore_dirs.py`,
      `tests/test_plugin_config_loader.py`, plus a
      `TestXxxPluginConfig` class in each plugin's
      `test_parser.py` covering `enabled: False` round-trip and
      a representative knob.
- 2026-04-29 — Phase 2B complete:
    * `web/server.py` — new `_build_active_parsers()` helper plus an
      internal `_active_parsers` list and `threading.Lock` owned by
      `create_app`. `_do_index` now snapshots that list under the lock
      so a concurrent reload can't race an in-flight build, and falls
      back to the legacy hard-coded set only when discovery returns
      nothing. New `_reload_parsers()` re-runs `discover_plugins()`
      and atomically swaps `_active_parsers[:]` in place.
    * `web/server.py` — new `PATCH /api/settings/plugins/{name}/config`
      endpoint validates that (a) the plugin exists on disk, (b) every
      key is present in the on-disk `config.json`, (c) every value's
      type matches the on-disk default, and (d) `enabled` is strictly
      a `bool` when present. On success the override is merged into
      `data/settings.json` under `plugins[<name>].config` and
      `_reload_parsers()` is called so subsequent indexing picks up
      the change with no server restart.
    * `web/server.py::_load_settings` and
      `apollo/projects/settings.py::SettingsManager._load` were
      reworked so the per-plugin `config` slot in
      `data/settings.json` is reserved exclusively for **user
      overrides**. The Phase 2A behavior of mirroring the on-disk
      `config.json` into that slot would have stomped any PATCH-set
      override on the next load; both call sites now strip `config`
      from `detect_installed_plugins()` results before persisting and
      preserve whatever override is already on disk.
    * Tests added: `tests/test_plugin_config_api.py` covering happy
      path, invalid-key 400, invalid-type 400, `enabled` strict-bool
      400, override merge across two PATCHes, and end-to-end
      `enabled: false` → `_build_active_parsers()` drops the plugin.
    * API docs synced per `guides/API_OPENAPI.md` checklist:
      `docs/openapi.yaml` gained a `/api/settings/plugins/{name}/config`
      PATCH path (`operationId: patchPluginConfig`, `tags: [Settings]`,
      request body schema, 200 response schema, `$ref` errors for
      400/404/500); `docs/API.md` Settings section gained a matching
      markdown entry with path-param table, request body description
      and response example.
- 2026-04-29 — Phase 2B addendum: introduced the `_<key>` description
  sibling convention so plugin configs are self-documenting for the
  Phase 2C UI:
    * Each runtime knob in `config.json` may now ship a sibling key
      prefixed with `_` whose value is a human-readable description.
    * `apollo/projects/settings.py::load_plugin_config()` strips every
      `_<key>` from the merged dict it returns, so parsers never see
      descriptions as data.
    * `PATCH /api/settings/plugins/<name>/config` rejects any body
      whose keys start with `_` (description siblings are read-only
      docs sourced from the plugin author's `config.json`).
    * `plugins/python3/config.json`, `plugins/markdown_gfm/config.json`,
      `plugins/html5/config.json`, `plugins/pdf_pypdf/config.json` all
      gained `_<key>` descriptions for every existing knob.
    * `guides/making_plugins.md` § 2.6 gained a "Describe each knob
      with a `_<key>` sibling" subsection.
    * Tests added: `tests/test_plugin_config_loader.py` checks that
      `_<key>` siblings never leak into the merged runtime dict;
      `tests/test_plugin_config_api.py` checks that PATCH rejects
      bodies with `_<key>` keys (400 + "read-only" message).
- 2026-04-29 — Phase 2C complete:
    * `web/server.py::get_settings()` — each `plugins[<name>]` entry
      now ships a `config_schema` field (raw on-disk `config.json`,
      including `_<key>` description siblings) plus a refreshed
      `config` field (merged effective values via
      `load_plugin_config()`, `_<key>` stripped). Both are
      response-only — `_load_settings()` still persists only the user
      override under `plugins[<name>].config` so plugin upgrades
      can't clobber user choices.
    * `web/static/app.js::renderPluginsList()` — rewritten to render
      a per-card `enabled` toggle next to the status badge plus a
      collapsible "Settings" panel auto-built from the schema.
      Inputs are typed off the on-disk JSON value (`bool`→checkbox,
      `int`/`number`→number input, `string`→text input,
      `list[str]`→comma-separated tag input; `dict` and
      `list[non-str]` render as read-only `<pre>` JSON in v1).
      Each field's label and tooltip are sourced from the matching
      `_<key>` description sibling, falling back to the bare key
      when the sibling is missing. `_<key>` keys are skipped during
      iteration and never PATCHed.
    * The toggle PATCHes `{"enabled": <bool>}` and reloads settings
      so badges and merged values stay in sync. The "Save" button
      collects a partial diff against the loaded merged values and
      PATCHes only the changed knobs. The "Reset to defaults" button
      PATCHes every editable knob back to its on-disk default
      (which is equivalent to clearing the user override at the
      key level).
    * `web/static/index.html` — helper text under "Installed
      Plugins" updated to mention the live toggle and the
      collapsible Settings panel; no new container needed (the
      existing `#plugins-list` host is reused).
    * Existing `tests/test_plugin_config_api.py` and
      `tests/test_plugin_config_loader.py` continue to pass
      unchanged — Phase 2C is a pure UI/serialization layer on top
      of the Phase 2B endpoint.
