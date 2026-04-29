/* ── Markdown config ───────────────────────────────────────────── */
marked.setOptions({
  breaks: true, gfm: true,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
    return hljs.highlightAuto(code).value;
  }
});

/* ── Constants ─────────────────────────────────────────────────── */
const NODE_COLORS = {
  function: '#00d9ff', class: '#ff6b6b', method: '#ffd93d',
  variable: '#6bcb77', file: '#8b8b8b', directory: '#000000', import: '#b088f9',
};
/* Per-type border overrides for the graph nodes. We make directories
   black-with-white-border so they're visually distinct from files
   (which stay grey, no border) — previously both were near-identical
   shades of grey and easy to misclick. */
const NODE_BORDERS = {
  directory: { borderColor: '#ffffff', borderWidth: 2 },
};
/* Pick black or white text for a badge whose background is `bg` (hex).
   Avoids invisible black-on-black for the directory badge. */
function badgeTextColor(bg) {
  if (typeof bg !== 'string') return '#000';
  const m = bg.match(/^#?([0-9a-f]{6})$/i);
  if (!m) return '#000';
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 0xff, g = (n >> 8) & 0xff, b = n & 0xff;
  // Standard relative-luminance threshold; below ~0.5 use white text.
  return (0.2126*r + 0.7152*g + 0.0722*b) / 255 < 0.5 ? '#fff' : '#000';
}
let graphChart = null, wordCloudChart = null, currentGraph = null, selectedNode = null, currentView = 'graph';
/* Dev-mode (?dev=true) graph variant. '1' = current force-directed render,
   '2' = stable circular-layout render with hideOverlap labels (mirrors the
   ECharts "graph-label-overlap" example). Hidden in normal mode. */
let graphChart2 = null, currentGraphVariant = '1';
let wordCloudAllData = [], wordCloudExpanded = false;
const WORDCLOUD_DEFAULT_LIMIT = 30;

/* ── Depth Slider ──────────────────────────────────────────────── */
const DEPTH_STOPS = [
  5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300, 400, 500,
  750, 1000, 1500, 2000, 3000, 5000,
];
function depthToNodes(val) { return DEPTH_STOPS[Math.min(val - 1, DEPTH_STOPS.length - 1)]; }
function depthToEdges(nodes) { return Math.min(nodes * 3, 5000); }
function updateDepthLabel() {
  const v = parseInt(document.getElementById('depth-slider').value);
  const n = depthToNodes(v);
  const e = depthToEdges(n);
  document.getElementById('depth-label').textContent = `${n} / ${e}`;
}
function getDepthNodes() { return depthToNodes(parseInt(document.getElementById('depth-slider').value)); }

/* ── Graph Cache (5-min TTL, keyed by query params) ────────────── */
const _graphCache = new Map();
const GRAPH_CACHE_TTL = 5 * 60 * 1000;
function graphCacheKey(url) { return url; }
function graphCacheGet(url) {
  const entry = _graphCache.get(url);
  if (!entry) return null;
  if (Date.now() - entry.ts > GRAPH_CACHE_TTL) { _graphCache.delete(url); return null; }
  return entry.data;
}
function graphCacheSet(url, data) { _graphCache.set(url, { data, ts: Date.now() }); }
function graphCacheClear() { _graphCache.clear(); }

/* ── Theme ─────────────────────────────────────────────────────── */
function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  const newTheme = isDark ? 'light' : 'dark';
  html.setAttribute('data-theme', newTheme);
  
  // Swap highlight.js CSS for light/dark code block themes
  let hlLink = document.querySelector('link[href*="atom-one"]');
  if (!hlLink) {
    hlLink = document.createElement('link');
    hlLink.rel = 'stylesheet';
    document.head.appendChild(hlLink);
  }
  hlLink.href = newTheme === 'dark'
    ? 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/atom-one-dark.min.css'
    : 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/atom-one-light.min.css';
  
  document.getElementById('theme-icon-moon').classList.toggle('hidden', !isDark);
  document.getElementById('theme-icon-sun').classList.toggle('hidden', isDark);
  const label = document.querySelector('#theme-btn .nav-label');
  if (label) label.textContent = isDark ? 'Light Mode' : 'Dark Mode';
  if (graphChart) { graphChart.dispose(); graphChart = null; }
  if (graphChart2) { graphChart2.dispose(); graphChart2 = null; }
  loadGraph();
  if (wordCloudChart) { wordCloudChart.dispose(); wordCloudChart = null; }
}

/* ── Nav ───────────────────────────────────────────────────────── */
function toggleNav() {
  const nav = document.getElementById('nav-sidebar');
  const icon = document.getElementById('nav-collapse-icon');
  nav.classList.toggle('collapsed');
  icon.innerHTML = nav.classList.contains('collapsed')
    ? '<path stroke-linecap="round" stroke-linejoin="round" d="M11.25 4.5l7.5 7.5-7.5 7.5m-6-15l7.5 7.5-7.5 7.5" />'
    : '<path stroke-linecap="round" stroke-linejoin="round" d="M18.75 19.5l-7.5-7.5 7.5-7.5m-6 15L5.25 12l7.5-7.5" />';
  setTimeout(() => {
    if (graphChart) graphChart.resize();
    if (graphChart2) graphChart2.resize();
    if (wordCloudChart) wordCloudChart.resize();
  }, 300);
}

/* ── Helpers ───────────────────────────────────────────────────── */
/* Single HTTP entry point for all JSON API calls. Handles method, body,
   status-code categorization (per https://restfulapi.net/http-status-codes/),
   server-supplied error detail extraction, and 204 No Content.

   Usage:
     await apiFetch('/api/foo');                              // GET
     await apiFetch('/api/foo', { method: 'POST', body: {} }); // POST JSON
     await apiFetch('/api/foo', { method: 'DELETE' });         // DELETE

   For streaming responses (e.g. SSE) pass `{ raw: true }` to receive the
   raw Response after status validation. */
class ApiError extends Error {
  constructor(status, category, detail) {
    super(`[${status || 'NET'}] ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.category = category;
    this.detail = detail;
  }
}
function _statusCategory(s) {
  if (!s) return 'network';
  if (s === 400) return 'badrequest';
  if (s === 401) return 'unauthorized';
  if (s === 403) return 'forbidden';
  if (s === 404) return 'notfound';
  if (s === 408) return 'timeout';
  if (s === 409) return 'conflict';
  if (s === 422) return 'validation';
  if (s === 429) return 'ratelimit';
  if (s === 503) return 'unavailable';
  if (s === 504) return 'gatewaytimeout';
  if (s >= 500) return 'server';   // 5xx — server fault
  if (s >= 400) return 'client';   // other 4xx — client fault
  if (s >= 300) return 'redirect'; // 3xx — should be auto-followed by fetch
  return 'ok';                     // 2xx
}
/* Format an ApiError (or generic Error) for user-facing display.
   Maps category → human-friendly prefix per restfulapi.net status semantics. */
function formatApiError(e, fallback = 'Request failed') {
  if (!e) return fallback;
  if (!(e instanceof ApiError)) return e.message || fallback;
  const map = {
    network:        'Network error',
    badrequest:     'Bad request',
    unauthorized:   'Not signed in',
    forbidden:      'Forbidden',
    notfound:       'Not found',
    timeout:        'Request timed out',
    conflict:       'Conflict',
    validation:     'Invalid input',
    ratelimit:      'Rate limited — slow down',
    unavailable:    'Service unavailable',
    gatewaytimeout: 'Upstream timed out',
    server:         'Server error',
    client:         'Request error',
  };
  const prefix = map[e.category] || `HTTP ${e.status}`;
  return `${prefix}: ${e.detail}`;
}

function showToast(msg, type = 'info') {
  let existing = document.querySelector('.toast-msg');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `toast-msg show alert alert-${type}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 300); }, 3000);
}

async function apiFetch(url, opts = {}) {
  const { method = 'GET', body, headers = {}, raw = false, signal } = opts;
  const init = { method, headers: { ...headers }, signal };
  if (body !== undefined && body !== null) {
    if (!init.headers['Content-Type']) init.headers['Content-Type'] = 'application/json';
    init.body = typeof body === 'string' ? body : JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(url, init);
  } catch (e) {
    // Network failure, CORS, DNS, offline, aborted, etc.
    throw new ApiError(0, 'network', e.message || 'Network error');
  }
  if (!r.ok) {
    let detail = r.statusText || `HTTP ${r.status}`;
    try {
      const ct = r.headers.get('content-type') || '';
      if (ct.includes('application/json')) {
        const j = await r.json();
        // Server's structured shape: { status_code, error: { code, message, details? } }
        // Legacy shapes: { detail }, { error: "msg" }, { message }
        const structuredMsg =
          j && j.error && typeof j.error === 'object' ? j.error.message : null;
        const legacyError =
          typeof j.error === 'string' ? j.error : null;
        const candidate =
          (typeof j.detail === 'string' ? j.detail : null) ||
          structuredMsg ||
          legacyError ||
          (typeof j.message === 'string' ? j.message : null);
        if (candidate) {
          detail = candidate;
        } else if (j && typeof j === 'object') {
          // Last resort: stringify so we never show "[object Object]"
          try { detail = JSON.stringify(j).slice(0, 500); } catch { /* keep default */ }
        }
      } else {
        const t = await r.text();
        if (t) detail = t.slice(0, 500);
      }
    } catch { /* ignore parse errors */ }
    throw new ApiError(r.status, _statusCategory(r.status), detail);
  }
  if (raw) return r;
  if (r.status === 204) return null; // No Content
  const ct = r.headers.get('content-type') || '';
  try {
    return ct.includes('application/json') ? await r.json() : await r.text();
  } catch (e) {
    // Server claimed success but body is unreadable / malformed.
    throw new ApiError(r.status, 'parse', `Malformed response body: ${e.message}`);
  }
}

/* ── Split Pane Drag ───────────────────────────────────────────── */
(function initSplit() {
  const handle = document.getElementById('split-handle');
  const left = document.getElementById('split-left');
  const pane = handle?.parentElement;
  if (!handle || !left || !pane) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => { dragging = true; e.preventDefault(); });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = pane.getBoundingClientRect();
    const pct = Math.min(70, Math.max(15, ((e.clientX - rect.left) / rect.width) * 100));
    left.style.width = pct + '%';
    if (graphChart) graphChart.resize();
    if (graphChart2) graphChart2.resize();
    if (wordCloudChart) wordCloudChart.resize();
  });
  document.addEventListener('mouseup', () => { dragging = false; });
})();

/* ── Vertical Split Pane Drag (graph / word cloud) ─────────────── */
(function initVSplit() {
  const handle = document.getElementById('vsplit-handle');
  const top = document.getElementById('vsplit-top');
  const pane = handle?.parentElement;
  if (!handle || !top || !pane) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => { dragging = true; e.preventDefault(); });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = pane.getBoundingClientRect();
    const pct = Math.min(85, Math.max(20, ((e.clientY - rect.top) / rect.height) * 100));
    top.style.height = pct + '%';
    if (graphChart) graphChart.resize();
    if (graphChart2) graphChart2.resize();
    if (wordCloudChart) wordCloudChart.resize();
  });
  document.addEventListener('mouseup', () => { dragging = false; });
})();

/* ── Left Vertical Split Pane Drag (detail / chat) ──────────────── */
(function initLeftVSplit() {
  const handle = document.getElementById('left-vsplit-handle');
  const top = document.getElementById('left-vsplit-top');
  const pane = handle?.parentElement;
  if (!handle || !top || !pane) return;
  let dragging = false;
  handle.addEventListener('mousedown', e => { dragging = true; e.preventDefault(); });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = pane.getBoundingClientRect();
    const pct = Math.min(85, Math.max(15, ((e.clientY - rect.top) / rect.height) * 100));
    top.style.flex = 'none';
    top.style.height = pct + '%';
  });
  document.addEventListener('mouseup', () => { dragging = false; });
})();

/* ── Bootstrap Wizard State ────────────────────────────────────── */
let _bootstrapState = { currentStep: 1, projectInfo: null, path: null };

function openBootstrapWizard(path) {
  _bootstrapState.path = path;
  _bootstrapState.currentStep = 1;
  document.getElementById('bootstrap-path').textContent = path;
  // Wire mode-card buttons (idempotent: dataset.bound prevents re-binding)
  const allBtn = document.getElementById('filter-mode-all-btn');
  const customBtn = document.getElementById('filter-mode-custom-btn');
  if (allBtn && !allBtn.dataset.bound) {
    allBtn.addEventListener('click', () => selectBootstrapMode('all'));
    allBtn.dataset.bound = '1';
  }
  if (customBtn && !customBtn.dataset.bound) {
    customBtn.addEventListener('click', () => selectBootstrapMode('custom'));
    customBtn.dataset.bound = '1';
  }
  selectBootstrapMode('all');
  showBootstrapStep(1);
  document.getElementById('bootstrap-modal').showModal();
}

function closeBootstrapModal() {
  document.getElementById('bootstrap-modal').close();
  _bootstrapState = { currentStep: 1, projectInfo: null, path: null };
}

function showBootstrapStep(step) {
  // Show only the active step (HTML uses .hidden, not .active)
  document.querySelectorAll('.bootstrap-step').forEach(el => el.classList.add('hidden'));
  document.getElementById(`bootstrap-step-${step}`)?.classList.remove('hidden');

  // Update steps indicator
  document.querySelectorAll('#bootstrap-steps .step').forEach((el, i) => {
    const stepNum = i + 1;
    el.classList.toggle('step-primary', stepNum <= step);
  });

  _bootstrapState.currentStep = step;

  // Load folder tree + Tagify-ify the inputs the first time step 2 opens
  if (step === 2) {
    loadBootstrapFolderTree();
    initBootstrapTagify();
  }
  
  // Update button visibility
  const nextBtn = document.getElementById('bootstrap-next-btn');
  const doneBtn = document.getElementById('bootstrap-done-btn');
  const cancelBtn = document.getElementById('bootstrap-cancel-btn');
  const backBtn = document.getElementById('bootstrap-back-btn');
  if (step === 4) {
    nextBtn.classList.add('hidden');
    doneBtn.classList.remove('hidden');
    cancelBtn.classList.add('hidden');
    if (backBtn) backBtn.classList.add('hidden');
  } else {
    nextBtn.classList.remove('hidden');
    doneBtn.classList.add('hidden');
    cancelBtn.classList.remove('hidden');
    // Back is available on step 2 (return to mode picker). Step 3 is mid-index.
    if (backBtn) backBtn.classList.toggle('hidden', step !== 2);
    // Hide Next while indexing — there's nothing to advance to manually.
    if (step === 3) nextBtn.classList.add('hidden');
  }
}

function bootstrapPrevStep() {
  const step = _bootstrapState.currentStep;
  if (step === 2) showBootstrapStep(1);
}

function selectBootstrapMode(mode) {
  const hidden = document.getElementById('filter-mode-hidden');
  if (hidden) hidden.value = mode;
  const allBtn = document.getElementById('filter-mode-all-btn');
  const customBtn = document.getElementById('filter-mode-custom-btn');
  const setActive = (el, active) => {
    if (!el) return;
    el.classList.toggle('border-primary', active);
    el.classList.toggle('bg-primary/10', active);
    el.classList.toggle('border-base-300', !active);
    el.classList.toggle('bg-base-100', !active);
  };
  setActive(allBtn, mode === 'all');
  setActive(customBtn, mode === 'custom');
}

async function bootstrapNextStep() {
  const step = _bootstrapState.currentStep;

  if (step === 1) {
    const mode = document.getElementById('filter-mode-hidden')?.value || 'all';
    if (mode === 'all') {
      showBootstrapStep(3);
      await submitBootstrapInit({ mode: 'all' });
    } else {
      showBootstrapStep(2);
    }
  } else if (step === 2) {
    const filters = serializeBootstrapFilters();
    showBootstrapStep(3);
    await submitBootstrapInit(filters);
  }
}

async function loadBootstrapFolderTree() {
  const container = document.getElementById('bootstrap-folder-tree');
  container.innerHTML = '<div class="text-xs opacity-50 p-2">Loading folders…</div>';
  try {
    // /api/projects/tree returns the tree object directly (not wrapped in .tree)
    const tree = await apiFetch(`/api/projects/tree?depth=3`);
    if (!tree) {
      container.innerHTML = '<div class="text-xs opacity-50 p-2">No folder tree available.</div>';
      return;
    }
    // Render the children of the root directly so the user picks top-level
    // dirs/files inside the project instead of toggling the project itself.
    // Hide dotfile/dotfolder entries (e.g. .git, .venv) and Apollo's own
    // state dirs (_apollo, _apollo_web) — they're noise for most users and
    // shouldn't be indexed.
    const children = (tree.children || []).filter(
      c => c && typeof c.name === 'string'
        && !c.name.startsWith('.')
        && !_isApolloStateName(c.name)
    );
    if (!children.length) {
      container.innerHTML = '<div class="text-xs opacity-50 p-2">Folder is empty.</div>';
      return;
    }
    container.innerHTML = children.map(c => renderBootstrapFolderTree(c, 0)).join('');
  } catch (e) {
    container.innerHTML = `<div class="alert alert-error text-xs p-2">${formatApiError(e)}</div>`;
  }
}

function renderBootstrapFolderTree(node, depth) {
  if (!node || !node.name) return '';
  // Skip dotfile/dotfolder entries at any depth (e.g. .git, .venv, .DS_Store)
  // and Apollo's own state dirs (_apollo, _apollo_web) — they're never
  // indexable and must not appear in the custom-filters tree.
  if (typeof node.name === 'string'
      && (node.name.startsWith('.') || _isApolloStateName(node.name))) {
    return '';
  }
  const isDir = node.type === 'dir';
  const icon = isDir ? '📁' : '📄';
  const hasChildren = isDir && Array.isArray(node.children) && node.children.length > 0;
  const path = (node.path && node.path !== '.') ? node.path : node.name;
  const safePath = String(path).replace(/"/g, '&quot;');
  const counts = isDir
    ? `<span class="text-xs opacity-50 ml-2">(${node.child_dir_count || 0} dirs, ${node.child_file_count || 0} files)</span>`
    : '';

  let html = `<div class="flex items-center gap-2 py-0.5" style="padding-left:${depth * 16}px">`;
  // Every dir AND file gets a checkbox so the user can include/exclude individually.
  html += `<input type="checkbox" class="checkbox checkbox-xs bootstrap-folder-check" data-path="${safePath}" data-type="${isDir ? 'dir' : 'file'}" checked />`;
  html += `<span class="text-sm">${icon} ${node.name}</span>${counts}`;
  html += '</div>';

  if (hasChildren) {
    html += node.children.map(child => renderBootstrapFolderTree(child, depth + 1)).join('');
  }

  return html;
}

// Default "do not index" patterns pre-filled into the bootstrap wizard's
// exclude-globs input. ``.*`` covers every dotfile (e.g. .DS_Store, .env,
// .gitignore) — by convention dotfiles are hidden / config / local-only and
// rarely belong in a code index. The other entries are common minified,
// generated, or lockfile artifacts. Users can remove any of these chips if
// they really do want them indexed.
const _DEFAULT_EXCLUDE_GLOBS = [
  '.*',                  // all dotfiles (auto-hidden by convention)
  '*.min.js', '*.min.css',
  '*.lock', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
  '*.pyc', '*.pyo',
  '*.map',
  '*.log', '*.tmp', '*.bak', '*.swp',
];

const _DEFAULT_DOC_TYPES_WHITELIST = [
  'py', 'js', 'jsx', 'ts', 'tsx',
  'md', 'json', 'yaml', 'yml', 'toml',
  'html', 'css', 'scss',
  'go', 'rs', 'sh', 'txt', 'pdf',
];

let _bootstrapTagify = { globs: null, types: null };
function initBootstrapTagify() {
  if (typeof Tagify === 'undefined') return;
  // Defer until after the modal layout pass so Tagify can measure its host.
  requestAnimationFrame(() => {
    const mkOpts = (whitelist) => ({
      delimiters: ',',
      dropdown: { enabled: 0, classname: 'bootstrap-tagify-dd' },
      whitelist,
      // Trim and drop empty values to avoid the "tag element doesn't exist" warning
      transformTag: (tagData) => {
        if (tagData && typeof tagData.value === 'string') {
          tagData.value = tagData.value.trim();
        }
      },
      validate: (tagData) => Boolean(tagData && tagData.value && tagData.value.trim())
    });
    const globsInput = document.getElementById('bootstrap-exclude-globs');
    const typesInput = document.getElementById('bootstrap-doc-types');
    try {
      if (globsInput && !_bootstrapTagify.globs && !globsInput.dataset.tagified) {
        _bootstrapTagify.globs = new Tagify(globsInput, mkOpts(_DEFAULT_EXCLUDE_GLOBS));
        // Pre-fill so users SEE the defaults (dotfiles, lockfiles, …) and
        // can remove any they actually want indexed.
        try { _bootstrapTagify.globs.addTags(_DEFAULT_EXCLUDE_GLOBS); } catch (_) {}
        globsInput.dataset.tagified = '1';
      }
      if (typesInput && !_bootstrapTagify.types && !typesInput.dataset.tagified) {
        _bootstrapTagify.types = new Tagify(typesInput, mkOpts(_DEFAULT_DOC_TYPES_WHITELIST));
        typesInput.dataset.tagified = '1';
      }
    } catch (e) {
      console.warn('Tagify init failed:', e);
    }
  });
}

function _readTagifyValues(inputId, fallbackInst) {
  // Tagify writes JSON like '[{"value":"py"}]' into the original input.
  const el = document.getElementById(inputId);
  if (!el) return [];
  // Prefer the live Tagify instance if we have one.
  if (fallbackInst && Array.isArray(fallbackInst.value)) {
    return fallbackInst.value.map(t => t.value).filter(Boolean);
  }
  const raw = el.value || '';
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.map(t => (t && t.value) || '').filter(Boolean);
  } catch (_) { /* not JSON — fall through */ }
  return raw.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
}

// Apollo's own per-project state and VCS metadata — never indexable, never
// shown in the custom-filters UI, and stripped from any filter payload sent
// to the server. The backend enforces the same rule, but we belt-and-brace
// here so users can never accidentally include them.
//
// ``_apollo`` (per-project store) and ``_apollo_web`` (web-UI state) do NOT
// start with a dot, so the dotfile filter does not catch them and they must
// be named explicitly here.
const _ALWAYS_SKIP_DIRS = new Set([
  '_apollo', '_apollo_web', '.apollo', '.git',
]);
function _isApolloStateName(name) {
  return name === '_apollo' || name === '_apollo_web' || name === '.apollo';
}
function _isAlwaysSkipped(path) {
  if (!path) return false;
  const first = String(path).replace(/\\/g, '/').split('/')[0];
  return _ALWAYS_SKIP_DIRS.has(first);
}

function serializeBootstrapFilters() {
  const mode = document.getElementById('filter-mode-hidden')?.value || 'all';
  if (mode === 'all') return { mode: 'all' };

  // Split checked tree rows into directories and individual files.
  const checkedRows = Array.from(document.querySelectorAll('.bootstrap-folder-check:checked'));
  const includeDirs = checkedRows
    .filter(el => el.dataset.type === 'dir')
    .map(el => el.dataset.path)
    .filter(p => p && !_isAlwaysSkipped(p));

  // Files that the user UNchecked → add to exclude_file_globs (exact path match).
  const allRows = Array.from(document.querySelectorAll('.bootstrap-folder-check'));
  const uncheckedFiles = allRows
    .filter(el => el.dataset.type === 'file' && !el.checked)
    .map(el => el.dataset.path)
    .filter(Boolean);
  const uncheckedDirs = allRows
    .filter(el => el.dataset.type === 'dir' && !el.checked)
    .map(el => el.dataset.path)
    .filter(Boolean);

  const excludeGlobs = _readTagifyValues('bootstrap-exclude-globs', _bootstrapTagify.globs)
    .concat(uncheckedFiles);
  const docTypes = _readTagifyValues('bootstrap-doc-types', _bootstrapTagify.types);

  return {
    mode: 'custom',
    include_dirs: includeDirs,
    exclude_dirs: uncheckedDirs,
    exclude_file_globs: excludeGlobs,
    include_doc_types: docTypes
  };
}

async function submitBootstrapInit(filters) {
  try {
    // 1. Persist filters to _apollo/apollo.json
    _bootstrapState.projectInfo = await apiFetch('/api/projects/init', {
      method: 'POST',
      body: { path: _bootstrapState.path, filters }
    });
    // 2. Actually start indexing. The /api/index worker reads
    //    project_manager.manifest.filters and applies them to GraphBuilder,
    //    so the filters chosen in the wizard take effect here.
    fetch('/api/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ directory: _bootstrapState.path })
    }).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.detail || ('Error ' + r.status)); });
      return r.json();
    }).then(() => {
      graphCacheClear();
      fetchIndexCount();
    }).catch(e => {
      showToast('Indexing failed: ' + (e.message || e), 'error');
    });
    // 3. Watch progress (polls /api/projects/current; status modal also updates)
    listenBootstrapIndexing();
  } catch (e) {
    showToast(formatApiError(e), 'error');
  }
}

function listenBootstrapIndexing() {
  // Poll /api/indexing-status (the same endpoint the regular indexing modal
  // uses). The previous version polled /api/projects/current, whose payload
  // has no `indexing` field — so the modal was stuck on "Starting…".
  let polling = false;
  const pollInterval = setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      const r = await fetch('/api/indexing-status');
      const s = await r.json();

      const step = s.step || 1;
      const label = s.step_label || 'Starting…';
      const detailEl = document.getElementById('bootstrap-indexing-detail');
      const labelEl = document.getElementById('bootstrap-step-label');
      if (labelEl) labelEl.textContent = label;
      if (detailEl) detailEl.textContent = s.detail || '';

      // Mark sub-steps as complete (1..step-1 done; current is in-progress)
      document.querySelectorAll('#bootstrap-indexing-steps .step').forEach((el, i) => {
        el.classList.toggle('step-primary', i + 1 <= step);
      });

      // Indexing finished when active=false AND step has reached 4.
      if (!s.active && step >= 4) {
        clearInterval(pollInterval);
        // Pull final counts from /api/projects/current (after indexing).
        try {
          const data = await apiFetch('/api/projects/current');
          document.getElementById('bootstrap-stat-files').textContent = data?.stats?.files_indexed ?? '—';
          document.getElementById('bootstrap-stat-nodes').textContent = data?.stats?.nodes ?? '—';
          document.getElementById('bootstrap-stat-edges').textContent = data?.stats?.edges ?? '—';
        } catch (_) { /* best-effort */ }
        showBootstrapStep(4);
        graphCacheClear();
        fetchIndexCount();
        loadGraph();
        // Refresh the sidebar folder tree so newly indexed files/folders
        // appear without requiring a manual page refresh.
        loadFolderTree();
      }
    } catch (e) {
      clearInterval(pollInterval);
      showToast('Indexing failed: ' + (e?.message || e), 'error');
    } finally {
      polling = false;
    }
  }, 1000);
}

/* ── My Files — Folder Browser ─────────────────────────────────── */
let _browseCurrentPath = '/';
function openFolderPicker() {
  fetch('/api/env').then(r => r.json()).then(env => {
    if (env.native_picker) { _openNativePicker(); }
    else { _openBrowserPicker(); }
  }).catch(() => _openBrowserPicker());
}
function _openNativePicker() {
  const dot = document.getElementById('status-dot');
  const txt = document.getElementById('status-text');
  dot.className = 'w-1.5 h-1.5 rounded-full bg-warning animate-pulse';
  txt.textContent = 'Select a folder…';
  fetch('/api/browse-folder', { method: 'POST' })
    .then(r => { if (!r.ok) throw new Error('Cancelled'); return r.json(); })
    .then(async data => {
      if (!data.path) { dot.className = 'w-1.5 h-1.5 rounded-full bg-base-content/30'; txt.textContent = 'Ready'; return; }
      // Route through /api/projects/open so first-time folders trigger the
      // bootstrap wizard (file/folder/file-type filters) instead of
      // unconditionally indexing the entire folder.
      try {
        const proj = await apiFetch('/api/projects/open', {
          method: 'POST',
          body: { path: data.path }
        });
        if (proj && proj.needs_bootstrap) {
          dot.className = 'w-1.5 h-1.5 rounded-full bg-base-content/30';
          txt.textContent = 'Configure project…';
          openBootstrapWizard(data.path);
          return;
        }
      } catch (e) {
        showToast(formatApiError(e), 'error');
        dot.className = 'w-1.5 h-1.5 rounded-full bg-base-content/30';
        txt.textContent = 'Ready';
        return;
      }
      // Already-bootstrapped project → reindex normally.
      txt.textContent = 'Indexing ' + data.path + '…';
      showIndexingModal(data.path);
      return fetch('/api/index', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ directory: data.path }) })
        .then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || `Error ${r.status}`); }); return r.json(); })
        .then(() => { graphCacheClear(); fetchIndexCount(); dot.className = 'w-1.5 h-1.5 rounded-full bg-success'; txt.textContent = 'Indexed ' + data.path; });
    })
    .catch(() => { dot.className = 'w-1.5 h-1.5 rounded-full bg-base-content/30'; txt.textContent = 'Ready'; });
}
function _openBrowserPicker() {
  const modal = document.getElementById('folder-picker-modal');
  document.getElementById('folder-picker-error').classList.add('hidden');
  document.getElementById('folder-picker-loading').classList.add('hidden');
  document.getElementById('folder-picker-go').disabled = false;
  modal.showModal();
  _browseLoadDir('/data');
}
function closeFolderPicker() { document.getElementById('folder-picker-modal').close(); }
function _browseLoadDir(path) {
  _browseCurrentPath = path;
  const pathEl = document.getElementById('folder-current-path');
  const listEl = document.getElementById('folder-list');
  pathEl.textContent = path;
  listEl.innerHTML = '<li class="text-xs opacity-50 p-2">Loading…</li>';
  fetch('/api/browse-dir?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(data => {
      _browseCurrentPath = data.path;
      pathEl.textContent = data.path;
      let html = '';
      if (data.path !== '/') {
        const parent = data.path.replace(/\/[^/]+\/?$/, '') || '/';
        html += `<li><button type="button" class="btn btn-ghost btn-xs w-full justify-start gap-2 font-normal" onclick="_browseLoadDir('${parent.replace(/'/g, "\\'")}')">📁 ..</button></li>`;
      }
      data.dirs.forEach(d => {
        const full = (data.path === '/' ? '/' : data.path + '/') + d;
        html += `<li><button type="button" class="btn btn-ghost btn-xs w-full justify-start gap-2 font-normal" onclick="_browseLoadDir('${full.replace(/'/g, "\\'")}')">📁 ${d}</button></li>`;
      });
      if (!data.dirs.length && data.path === '/') html = '<li class="text-xs opacity-50 p-2">No folders found</li>';
      listEl.innerHTML = html;
    })
    .catch(() => { listEl.innerHTML = '<li class="text-xs text-error p-2">Failed to load</li>'; });
}
async function submitFolderPicker() {
  const errEl = document.getElementById('folder-picker-error');
  const loadEl = document.getElementById('folder-picker-loading');
  const goBtn = document.getElementById('folder-picker-go');
  errEl.classList.add('hidden');
  loadEl.classList.remove('hidden');
  goBtn.disabled = true;
  closeFolderPicker();
  
  try {
    const data = await apiFetch('/api/projects/open', {
      method: 'POST',
      body: { path: _browseCurrentPath }
    });
    
    if (data.needs_bootstrap) {
      // Open bootstrap wizard
      openBootstrapWizard(_browseCurrentPath);
    } else {
      // Already indexed, load normally
      graphCacheClear();
      fetchIndexCount();
      switchView('graph');
    }
  } catch (e) {
    errEl.textContent = formatApiError(e);
    errEl.classList.remove('hidden');
    goBtn.disabled = false;
  } finally {
    loadEl.classList.add('hidden');
  }
}

/* ── Indexing Progress Modal ────────────────────────────────────── */
let _indexingPollTimer = null;
function showIndexingModal(directory) {
  const modal = document.getElementById('indexing-modal');
  document.getElementById('indexing-dir').textContent = directory;
  document.getElementById('indexing-done').classList.add('hidden');
  document.getElementById('indexing-spinner').classList.remove('hidden');
  document.getElementById('indexing-step-label').textContent = 'Starting…';
  document.getElementById('indexing-detail').textContent = '';
  document.querySelectorAll('#indexing-steps .step').forEach(li => {
    li.classList.remove('step-primary');
  });
  modal.showModal();
  _startIndexingPoll();
}
function _startIndexingPoll() {
  if (_indexingPollTimer) clearInterval(_indexingPollTimer);
  let polling = false;
  _indexingPollTimer = setInterval(() => {
    if (polling) return;          // skip if previous request still in-flight
    polling = true;
    fetch('/api/indexing-status').then(r => r.json()).then(s => {
      const steps = document.querySelectorAll('#indexing-steps .step');
      steps.forEach(li => {
        const n = parseInt(li.dataset.step);
        li.classList.toggle('step-primary', n <= (s.step || 0));
      });
      document.getElementById('indexing-step-label').textContent = s.step_label || '';
      document.getElementById('indexing-detail').textContent = s.detail || '';
      if (!s.active && s.step >= 4) {
        clearInterval(_indexingPollTimer);
        _indexingPollTimer = null;
        document.getElementById('indexing-spinner').classList.add('hidden');
        document.getElementById('indexing-done').classList.remove('hidden');
        loadFolderTree();
      }
    }).catch(() => {}).finally(() => { polling = false; });
  }, 2000);
}

/* ── Folder Tree (Sidebar) ─────────────────────────────────────── */
// Indexed root path, captured from /api/tree so we can show relative paths
// (e.g. "graph/query.py" instead of the full absolute path) elsewhere in the UI.
let indexedRootPath = '';

function relativePath(p) {
  if (!p) return '';
  // The graph stores file/directory paths as relative-to-the-indexed-root in
  // some configurations and absolute in others. If it's already relative,
  // use it as-is.
  if (!p.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(p)) return p;
  const root = (indexedRootPath || '').replace(/\/+$/, '');
  if (root && (p === root || p.startsWith(root + '/'))) {
    const rel = p.slice(root.length).replace(/^\/+/, '');
    return rel || p.split('/').pop() || p;
  }
  return p;
}

async function loadFolderTree() {
  const treeEl = document.getElementById('folder-tree');
  const titleEl = document.getElementById('folder-tree-title');
  if (!treeEl) return;
  try {
    const root = await apiFetch('/api/tree');
    if (!root || (!root.children || !root.children.length)) {
      treeEl.innerHTML = '<div class="folder-tree-empty">No folder indexed.</div>';
      titleEl.textContent = 'FOLDER';
      indexedRootPath = '';
      return;
    }
    indexedRootPath = root.path || '';
    titleEl.textContent = (root.name || 'FOLDER').toUpperCase();
    treeEl.innerHTML = '';
    const children = root.type === 'directory' && root.children ? root.children : [root];
    treeEl.appendChild(renderTreeChildren(children, 0));
    // Decorate file rows with note-indicator dots (daisyUI indicator).
    if (typeof Annotations !== 'undefined' && Annotations.refreshIndicators) {
      Annotations.refreshIndicators();
    }
  } catch (e) {
    treeEl.innerHTML = '<div class="folder-tree-empty">No folder indexed.</div>';
    titleEl.textContent = 'FOLDER';
  }
}

const _FOLDER_SVG = '<svg class="tree-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" /></svg>';
const _CHEVRON_SVG = '<svg class="tree-chevron" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" /></svg>';

/* Map file extension → { label, color } for the colored badge in the tree. */
const FILE_TYPE_BADGES = {
  js:    { label: 'JS',  color: '#f7df1e', fg: '#000' },
  mjs:   { label: 'JS',  color: '#f7df1e', fg: '#000' },
  cjs:   { label: 'JS',  color: '#f7df1e', fg: '#000' },
  ts:    { label: 'TS',  color: '#3178c6' },
  tsx:   { label: 'TSX', color: '#3178c6' },
  jsx:   { label: 'JSX', color: '#61dafb', fg: '#000' },
  py:    { label: 'PY',  color: '#3776ab' },
  rb:    { label: 'RB',  color: '#cc342d' },
  go:    { label: 'GO',  color: '#00add8' },
  rs:    { label: 'RS',  color: '#dea584', fg: '#000' },
  java:  { label: 'JV',  color: '#b07219' },
  kt:    { label: 'KT',  color: '#a97bff' },
  swift: { label: 'SW',  color: '#fa7343' },
  c:     { label: 'C',   color: '#555' },
  h:     { label: 'H',   color: '#888' },
  cpp:   { label: 'C++', color: '#00599c' },
  cs:    { label: 'C#',  color: '#178600' },
  php:   { label: 'PHP', color: '#777bb4' },
  sh:    { label: 'SH',  color: '#4eaa25' },
  bash:  { label: 'SH',  color: '#4eaa25' },
  zsh:   { label: 'SH',  color: '#4eaa25' },
  sql:   { label: 'SQL', color: '#e38c00' },
  html:  { label: 'HTM', color: '#e34c26' },
  htm:   { label: 'HTM', color: '#e34c26' },
  css:   { label: 'CSS', color: '#1572b6' },
  scss:  { label: 'SCS', color: '#cc6699' },
  sass:  { label: 'SAS', color: '#cc6699' },
  less:  { label: 'LES', color: '#1d365d' },
  json:  { label: '{}',  color: '#cbcb41', fg: '#000' },
  yml:   { label: 'YML', color: '#cb171e' },
  yaml:  { label: 'YML', color: '#cb171e' },
  toml:  { label: 'TML', color: '#9c4221' },
  xml:   { label: 'XML', color: '#0060ac' },
  md:    { label: 'MD',  color: '#0a7bbb' },
  mdx:   { label: 'MDX', color: '#1a5fa8' },
  txt:   { label: 'TXT', color: '#666' },
  csv:   { label: 'CSV', color: '#1f7d3a' },
  pdf:   { label: 'PDF', color: '#d93025' },
  doc:   { label: 'W',   color: '#2b579a' },
  docx:  { label: 'W',   color: '#2b579a' },
  xls:   { label: 'X',   color: '#217346' },
  xlsx:  { label: 'X',   color: '#217346' },
  ppt:   { label: 'P',   color: '#d24726' },
  pptx:  { label: 'P',   color: '#d24726' },
  png:   { label: 'IMG', color: '#a259ff' },
  jpg:   { label: 'IMG', color: '#a259ff' },
  jpeg:  { label: 'IMG', color: '#a259ff' },
  gif:   { label: 'IMG', color: '#a259ff' },
  webp:  { label: 'IMG', color: '#a259ff' },
  svg:   { label: 'SVG', color: '#ffb13b', fg: '#000' },
  ico:   { label: 'ICO', color: '#a259ff' },
  zip:   { label: 'ZIP', color: '#8a8a8a' },
  tar:   { label: 'TAR', color: '#8a8a8a' },
  gz:    { label: 'GZ',  color: '#8a8a8a' },
  env:   { label: 'ENV', color: '#ecd53f', fg: '#000' },
  lock:  { label: 'LCK', color: '#666' },
  log:   { label: 'LOG', color: '#666' },
  dockerfile: { label: 'DOC', color: '#0db7ed' },
};

function fileBadgeHtml(name) {
  const lower = (name || '').toLowerCase();
  let key;
  if (lower === 'dockerfile' || lower.endsWith('.dockerfile')) key = 'dockerfile';
  else {
    const dot = lower.lastIndexOf('.');
    key = dot >= 0 ? lower.slice(dot + 1) : '';
  }
  const meta = FILE_TYPE_BADGES[key] || { label: (key || '?').slice(0, 3).toUpperCase(), color: '#555' };
  const fg = meta.fg || '#fff';
  return `<span class="file-badge" style="background:${meta.color};color:${fg}">${escapeHtml(meta.label)}</span>`;
}

function renderTreeChildren(children, depth) {
  const frag = document.createDocumentFragment();
  const sorted = [...children].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
    return (a.name || '').localeCompare(b.name || '');
  });
  for (const node of sorted) frag.appendChild(renderTreeNode(node, depth));
  return frag;
}

function renderTreeNode(node, depth) {
  const isDir = node.type === 'directory';
  const wrap = document.createElement('div');
  const row = document.createElement('div');
  row.className = 'tree-row' + (isDir ? '' : ' is-file');
  row.style.paddingLeft = (4 + depth * 12) + 'px';
  row.title = node.path || node.name || '';
  // Stash the node id so Annotations.applyTreeIndicators() can map a row
  // back to its graph node without re-walking the tree.
  if (node.id) row.dataset.nodeId = node.id;
  row.innerHTML = (isDir ? _CHEVRON_SVG : '<span class="tree-chevron-spacer"></span>')
    + (isDir ? _FOLDER_SVG : fileBadgeHtml(node.name))
    + `<span class="tree-label">${escapeHtml(node.name || '')}</span>`;
  wrap.appendChild(row);
  if (isDir && node.children && node.children.length) {
    const kids = document.createElement('div');
    kids.className = 'tree-children collapsed';
    kids.appendChild(renderTreeChildren(node.children, depth + 1));
    wrap.appendChild(kids);
    row.classList.add('collapsed');
    row.addEventListener('click', () => {
      row.classList.toggle('collapsed');
      kids.classList.toggle('collapsed');
      if (node.id) treeRowSelect(row, node.id);
    });
  } else if (node.id) {
    row.addEventListener('click', () => treeRowSelect(row, node.id));
  }
  return wrap;
}

function treeRowSelect(row, nodeId) {
  document.querySelectorAll('.tree-row.tree-row-active').forEach(el => el.classList.remove('tree-row-active'));
  row.classList.add('tree-row-active');
  if (currentView !== 'graph') switchView('graph');
  softSelectNode(nodeId);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

(function initFolderTreeHeader() {
  document.addEventListener('DOMContentLoaded', () => {
    const header = document.querySelector('.folder-tree-header');
    const section = document.querySelector('.folder-tree-section');
    if (header && section) header.addEventListener('click', () => section.classList.toggle('collapsed'));
  });
})();

/* ── View Switch ───────────────────────────────────────────────── */
function switchView(view) {
  currentView = view;
  document.querySelectorAll('.nav-item[data-view]').forEach(item => {
    if (item.dataset.view === view) { item.classList.add('active','bg-primary','text-primary-content'); item.classList.remove('hover:bg-base-300'); }
    else { item.classList.remove('active','bg-primary','text-primary-content'); item.classList.add('hover:bg-base-300'); }
  });
  ['graph-view','image-view','settings-view'].forEach(v => {
    const el = document.getElementById(v);
    if (!el) return;
    const show = v === view || (view === 'graph' && v === 'graph-view') || (view === 'settings' && v === 'settings-view');
    el.style.display = show ? 'flex' : 'none';
    el.classList.toggle('hidden', !show);
  });
  if (view === 'settings') loadSettings();
  if (view === 'graph') setTimeout(() => {
    if (graphChart) graphChart.resize();
    if (graphChart2) graphChart2.resize();
    if (wordCloudChart) wordCloudChart.resize();
  }, 50);
}

/* ── Graph ─────────────────────────────────────────────────────── */
async function loadGraph() {
  const overlay = document.getElementById('loading-overlay');
  overlay.classList.remove('hidden');
  const allTypes = Object.keys(NODE_COLORS).join(',');
  const allEdges = 'calls,imports,defines,inherits,contains';
  const nodeLimit = getDepthNodes();
  const edgeLimit = depthToEdges(nodeLimit);
  const url = `/api/graph?types=${allTypes}&edges=${allEdges}&limit=${nodeLimit}&max_edges=${edgeLimit}`;
  try {
    let data = graphCacheGet(url);
    if (!data) {
      data = await apiFetch(url);
      graphCacheSet(url, data);
    }
    currentGraph = data;
    renderGraph(data);
    const shown = (data.nodes||[]).length, total = data.total_nodes||shown;
    document.getElementById('status-nodes').textContent = data.truncated ? `Nodes: ${shown}/${total}` : `Nodes: ${shown}`;
    const edgeShown = (data.edges||[]).length, edgeTotal = data.total_edges||edgeShown;
    document.getElementById('status-edges').textContent = data.edges_truncated ? `Edges: ${edgeShown}/${edgeTotal}` : `Edges: ${edgeShown}`;
    document.getElementById('status-dot').classList.add('bg-success');
    document.getElementById('status-text').textContent = 'Connected';
    fetchIndexCount();
    setTimeout(loadWordCloud, 200);
    // No longer auto-select the largest node on load — leave the graph
    // unselected so the Welcome tab remains visible by default.
  } catch (e) { console.error(e); document.getElementById('status-text').textContent = 'Error'; }
  finally { overlay.classList.add('hidden'); }
}

function renderGraph(data) {
  // In dev mode the user can toggle between Graph 1 (force-directed) and
  // Graph 2 (stable circular layout w/ hideOverlap labels — see ECharts
  // "graph-label-overlap" example). In normal mode only Graph 1 renders.
  if (currentGraphVariant === '2') {
    renderGraph2(data);
    return;
  }
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const tc = isDark ? '#dcddde' : '#333', ec = isDark ? '#3a3a3a' : '#ccc';
  if (!graphChart) {
    graphChart = echarts.init(document.getElementById('graph-chart'));
    graphChart.on('click', onGraphClick);
    graphChart.getZr().on('click', function(e) {
      if (!e.target) clearFocus();
    });
  }
  const catNames = (data.categories||[]).map(c => typeof c === 'string' ? c : c.name);
  if (!catNames.length) catNames.push(...Object.keys(NODE_COLORS));
  const categories = catNames.map(n => ({ name: n, itemStyle: { color: NODE_COLORS[n]||'#888' } }));
  const catIdx = {}; catNames.forEach((n,i) => catIdx[n] = i);
  const large = (data.nodes||[]).length > 500;
  // Dev mode: seed every node with a deterministic (x,y) derived from a
  // hash of its id, so the same node always starts in the same spot
  // across renders/releases. Combined with force.layoutAnimation:false
  // the force layout converges to a stable picture instead of reshuffling
  // every time the graph reloads. Drag still works (draggable:true).
  const stable = IS_DEV_MODE;
  const seedR = stable ? Math.max(300, 40 * Math.sqrt(Math.max(1, (data.nodes||[]).length))) : 0;
  const nodes = (data.nodes||[]).map(n => {
    const t = n.value||n.type||'unknown';
    const sz = Math.max(8,Math.min(40,n.symbolSize||12));
    const node = { id:n.id, name:n.name||n.id, symbolSize:sz, category:catIdx[t]??0,
      itemStyle:{color:NODE_COLORS[t]||'#888', ...(NODE_BORDERS[t]||{})}, label:{show:!large&&sz>18,fontSize:10,color:tc},
      _type:t, _path:n.attributes?.path||n.path, _line:n.attributes?.line_start||n.line };
    if (stable) {
      const h = _g2HashStr(n.id);
      const ang = ((h % 3600) / 3600) * Math.PI * 2;
      const rad = (((h >>> 8) % 1000) / 1000) * seedR;
      node.x = Math.cos(ang) * rad;
      node.y = Math.sin(ang) * rad;
    }
    return node;
  });
  const edges = (data.edges||[]).map(e => ({ source:e.source, target:e.target, lineStyle:{color:ec,opacity:0.5}, _rel:e.type||e.rel }));
  graphChart.setOption({
    tooltip: { trigger:'item', formatter: p => { if(p.dataType==='node'){const d=p.data;let t=`<b>${d.name}</b><br/>Type: ${d._type}`;if(d._path)t+=`<br/>${d._path}${d._line!=null?':'+d._line:''}`;return t;}return'';},
      backgroundColor:isDark?'#2b2b2b':'#fff', borderColor:isDark?'#3a3a3a':'#ddd', textStyle:{color:tc,fontSize:11} },
    legend: { data:categories.map(c=>c.name), bottom:8, textStyle:{color:tc,fontSize:11}, selectedMode:true,
      icon:'circle', itemWidth:10, itemHeight:10, itemGap:12 },
    animationDurationUpdate: large ? 0 : 500,
    animation: !large && !stable,
    series: [{ type:'graph', layout:'force', data:nodes, links:edges, categories, roam:true, draggable:true,
      large: large,
      // In dev mode disable continuous force animation so the deterministic
      // initial positions aren't relaxed into different ones each render.
      force:{repulsion:large?80:120,edgeLength:large?[40,120]:[60,200],gravity:large?0.15:0.08,layoutAnimation:!large && !stable,friction:large?0.4:0.6},
      emphasis:{focus:'adjacency',lineStyle:{width:3,color:'#7c3aed'}},
      select:{itemStyle:{borderColor:'#7c3aed',borderWidth:3,shadowBlur:10,shadowColor:'#7c3aed'},label:{show:true,fontSize:12,fontWeight:'bold',color:tc}},
      selectedMode:'single',
      edgeLabel:{show:false}, lineStyle:{curveness:0.1} }],
  }, true);
}

/* Graph 2 — stable, clustered layout for dev mode (?dev=true).
   Visual target: ECharts "graph" (Les Miserables) example —
     https://echarts.apache.org/examples/en/editor.html?c=graph
   That example loads pre-computed (x,y) positions and uses layout:'none'
   so the picture is reproducible. We do the same here, but generate the
   positions deterministically from each node's id and category so the
   same node always lands in the same place across reloads/releases —
   solving the "Graph 1 redistributes on every render, I have to hunt
   for the node again" problem.

   - Per-category cluster centers placed evenly around a large outer
     ring (clustered, not a single ring of nodes).
   - Within a cluster, node offset = deterministic hash of id → angle/
     radius. So clusters look organic but are stable.
   - lineStyle.color:'source' + curveness:0.3 mirrors Les Miserables. */
function _g2HashStr(s) {
  // Simple 32-bit string hash (djb2-ish). Deterministic, no deps.
  let h = 5381;
  s = String(s);
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return h >>> 0;
}
function renderGraph2(data) {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const tc = isDark ? '#dcddde' : '#333';
  if (!graphChart2) {
    graphChart2 = echarts.init(document.getElementById('graph-chart-2'));
    graphChart2.on('click', onGraphClick);
    graphChart2.getZr().on('click', function(e) {
      if (!e.target) clearFocus();
    });
  }
  const catNames = (data.categories||[]).map(c => typeof c === 'string' ? c : c.name);
  if (!catNames.length) catNames.push(...Object.keys(NODE_COLORS));
  const categories = catNames.map(n => ({ name: n, itemStyle: { color: NODE_COLORS[n]||'#888' } }));
  const catIdx = {}; catNames.forEach((n,i) => catIdx[n] = i);

  // Cluster geometry. Outer ring radius scales with node count so
  // dense graphs don't collapse on top of each other. Each category
  // gets one fixed cluster center on the outer ring.
  const totalNodes = (data.nodes||[]).length;
  const ringR = Math.max(380, 60 * Math.sqrt(Math.max(1, totalNodes)));
  const catCount = Math.max(1, catNames.length);
  const catCenters = catNames.map((_, i) => {
    const a = (i / catCount) * Math.PI * 2 - Math.PI / 2;
    return { x: Math.cos(a) * ringR, y: Math.sin(a) * ringR };
  });
  // Within-cluster radius scales with how many nodes share the cluster.
  const catSize = {};
  (data.nodes||[]).forEach(n => {
    const t = n.value||n.type||'unknown';
    catSize[t] = (catSize[t]||0) + 1;
  });

  const nodes = (data.nodes||[]).map(n => {
    const t = n.value||n.type||'unknown';
    const sz = Math.max(8, Math.min(40, n.symbolSize||12));
    const ci = catIdx[t] ?? 0;
    const center = catCenters[ci] || { x: 0, y: 0 };
    const inner = Math.max(80, 18 * Math.sqrt(catSize[t]||1));
    // Deterministic offset within the cluster from the node id hash.
    const h = _g2HashStr(n.id);
    const ang = ((h % 3600) / 3600) * Math.PI * 2;
    const rad = ((h >>> 8) % 1000) / 1000 * inner;
    const x = center.x + Math.cos(ang) * rad;
    const y = center.y + Math.sin(ang) * rad;
    return {
      id: n.id, name: n.name||n.id, symbolSize: sz, category: ci, x, y,
      itemStyle: { color: NODE_COLORS[t] || '#888', ...(NODE_BORDERS[t]||{}) },
      // Match Les Mis: only label larger nodes to keep the picture readable.
      label: { show: sz > 22, position: 'right', fontSize: 10, color: tc, formatter: '{b}' },
      _type: t, _path: n.attributes?.path || n.path, _line: n.attributes?.line_start || n.line,
    };
  });
  const edges = (data.edges||[]).map(e => ({
    source: e.source, target: e.target,
    lineStyle: { opacity: 0.7, width: 0.8, curveness: 0.3 },
    _rel: e.type || e.rel,
  }));
  graphChart2.setOption({
    tooltip: { trigger:'item', formatter: p => { if(p.dataType==='node'){const d=p.data;let t=`<b>${d.name}</b><br/>Type: ${d._type}`;if(d._path)t+=`<br/>${d._path}${d._line!=null?':'+d._line:''}`;return t;}return'';},
      backgroundColor:isDark?'#2b2b2b':'#fff', borderColor:isDark?'#3a3a3a':'#ddd', textStyle:{color:tc,fontSize:11} },
    legend: { data:categories.map(c=>c.name), bottom:8, textStyle:{color:tc,fontSize:11}, selectedMode:true,
      icon:'circle', itemWidth:10, itemHeight:10, itemGap:12 },
    animation: false,            // positions are fixed, no need to animate
    series: [{
      type: 'graph',
      layout: 'none',            // use the (x,y) we computed per node
      data: nodes, links: edges, categories,
      roam: true, draggable: true,
      labelLayout: { hideOverlap: true },
      // Edge color inherits source node color, like the Les Mis example.
      lineStyle: { color: 'source', curveness: 0.3 },
      emphasis: { focus: 'adjacency', label: { show: true, fontSize: 11, fontWeight: 'bold' }, lineStyle: { width: 3, color: '#7c3aed' } },
      select: { itemStyle: { borderColor: '#7c3aed', borderWidth: 3, shadowBlur: 10, shadowColor: '#7c3aed' }, label: { show: true, fontSize: 12, fontWeight: 'bold', color: tc } },
      selectedMode: 'single',
      edgeLabel: { show: false },
    }],
  }, true);
}

/* Dev-mode tab switcher: toggles which graph variant is visible and
   re-renders the active one against the currently-loaded data. */
function switchGraphVariant(variant) {
  if (variant !== '1' && variant !== '2') return;
  currentGraphVariant = variant;
  const c1 = document.getElementById('graph-chart');
  const c2 = document.getElementById('graph-chart-2');
  if (c1) c1.classList.toggle('hidden', variant !== '1');
  if (c2) c2.classList.toggle('hidden', variant !== '2');
  document.querySelectorAll('.graph-variant-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.graphVariant === variant);
  });
  if (currentGraph) renderGraph(currentGraph);
  // ECharts needs a resize after its container becomes visible.
  setTimeout(() => {
    if (variant === '1' && graphChart) graphChart.resize();
    if (variant === '2' && graphChart2) graphChart2.resize();
  }, 50);
}

async function onGraphClick(p) {
  if (p.dataType === 'node') await selectNode(p.data.id);
  else if (p.dataType === 'edge') openEdgeTab(p.data);
}

/* Ring-only select: detail panel + ring, all nodes/edges stay visible */
async function softSelectNode(nodeId) {
  selectedNode = nodeId;
  try {
    const data = await apiFetch(`/api/node/${encodeURIComponent(nodeId)}`);
    if (data.type === 'file' && data.path) {
      await showFileContent(data);
    } else {
      showDetail(data);
    }
    applyRingOnly(nodeId);
  } catch (e) { console.error(e); }
}

/* Render the full content of a file node in the detail pane.
   - .md → marked
   - .html/.htm → sandboxed iframe preview + raw toggle
   - everything else → highlight.js */
async function showFileContent(data) {
  const tabId = 'file:' + (data.path || data.id);
  const nodeId = data.id;
  Tabs.open({
    id: tabId,
    title: data.name || data.path || 'File',
    tooltip: relativePath(data.path) || data.path || data.name || '',
    closable: true,
    onActivate: () => {
      if (nodeId) applyPersistentFocus(nodeId);
      // Re-apply highlights + margin notes whenever this file's tab is
      // re-activated (e.g. after switching tabs).
      if (nodeId) requestAnimationFrame(() => Annotations.applyToDetail(nodeId));
    },
  });

  // Brief loading state.
  Tabs.setBody(tabId, '<div class="text-xs opacity-60 py-4">Loading file…</div>');

  let file;
  try {
    file = await apiFetch(`/api/file/content?path=${encodeURIComponent(data.path)}`);
  } catch (e) {
    Tabs.setBody(tabId, `<div class="text-xs text-error py-4">Failed to load file: ${escapeHtml(e.message || String(e))}</div>`);
    return;
  }

  const ext = (file.extension || '').toLowerCase();
  const lang = file.language || '';
  const typeColor = NODE_COLORS.file || '#888';
  const sizeKb = (file.size_bytes / 1024).toFixed(1);

  const header = `
    <div class="flex items-center gap-2 flex-wrap mb-3">
      <span class="badge badge-sm font-semibold" style="background:${typeColor};color:${badgeTextColor(typeColor)}">file</span>
      ${lang ? `<span class="badge badge-sm badge-outline font-mono">${escapeHtml(lang)}</span>` : ''}
      <span class="badge badge-sm badge-ghost font-mono" title="${escapeHtml(file.path)}">${escapeHtml(relativePath(data.path) || file.relative_path || relativePath(file.path))}</span>
      <span class="badge badge-sm badge-ghost font-mono">${sizeKb} KB</span>
      ${file.truncated ? '<span class="badge badge-sm badge-warning">truncated</span>' : ''}
      ${file.is_binary ? '<span class="badge badge-sm badge-warning">binary</span>' : ''}
    </div>`;

  const IMAGE_EXTS = ['.png','.jpg','.jpeg','.gif','.webp','.svg','.bmp','.ico','.avif'];
  const filePath = data.path || file.path;
  const fileDir = filePath ? filePath.replace(/[^/\\]+$/, '').replace(/[/\\]+$/, '') : '';
  const rawUrl = (p) => `/api/file/raw?path=${encodeURIComponent(p)}`;
  // Resolve a (possibly-relative) src against the file's directory.
  // Leaves absolute URLs (http/https/data:) untouched.
  const resolveSrc = (src) => {
    if (!src) return src;
    if (/^(?:[a-z]+:)?\/\//i.test(src) || src.startsWith('data:')) return src;
    if (src.startsWith('/')) return rawUrl(src.replace(/^\/+/, ''));
    const joined = fileDir ? `${fileDir}/${src}` : src;
    // Normalize ../ and ./
    const parts = [];
    for (const seg of joined.split(/[/\\]+/)) {
      if (!seg || seg === '.') continue;
      if (seg === '..') { parts.pop(); continue; }
      parts.push(seg);
    }
    return rawUrl(parts.join('/'));
  };
  // Rewrite all <img src> attributes inside an HTML string so they resolve via /api/file/raw.
  const rewriteImgSrc = (html) => {
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    tmp.querySelectorAll('img[src]').forEach(img => {
      img.setAttribute('src', resolveSrc(img.getAttribute('src')));
    });
    return tmp.innerHTML;
  };
  // Rewrite asset references inside a full HTML document string so that <link>,
  // <script>, <img>, <source>, <video>, <audio>, etc. resolve via /api/file/raw.
  // Uses DOMParser so <head>/<body> structure is preserved (unlike a div wrapper).
  const rewriteHtmlAssets = (html) => {
    let doc;
    try { doc = new DOMParser().parseFromString(html, 'text/html'); }
    catch (e) { return html; }
    const ATTRS = [
      ['img', 'src'], ['img', 'srcset'],
      ['source', 'src'], ['source', 'srcset'],
      ['video', 'src'], ['video', 'poster'],
      ['audio', 'src'],
      ['link', 'href'],
      ['script', 'src'],
      ['iframe', 'src'],
      ['use', 'href'], ['use', 'xlink:href'],
    ];
    for (const [tag, attr] of ATTRS) {
      doc.querySelectorAll(`${tag}[${attr.includes(':') ? attr.replace(':', '\\:') : attr}]`).forEach(el => {
        const val = el.getAttribute(attr);
        if (!val) return;
        if (attr === 'srcset') {
          // srcset is a comma-separated list of "url descriptor" pairs.
          const rewritten = val.split(',').map(part => {
            const trimmed = part.trim();
            if (!trimmed) return '';
            const [url, ...desc] = trimmed.split(/\s+/);
            return [resolveSrc(url), ...desc].join(' ');
          }).filter(Boolean).join(', ');
          el.setAttribute(attr, rewritten);
        } else {
          el.setAttribute(attr, resolveSrc(val));
        }
      });
    }
    return '<!doctype html>\n' + doc.documentElement.outerHTML;
  };

  let body;
  if (ext === '.pdf' && filePath) {
    const pdfSrc = escapeHtml(rawUrl(filePath));
    body = `<div class="md-content">
      <iframe src="${pdfSrc}" type="application/pdf"
        style="width:100%;height:80vh;border:0;border-radius:6px;background:#fff"
        title="${escapeHtml(file.name || filePath)}"></iframe>
      <div class="text-xs opacity-60 mt-2">
        If the PDF doesn't render above,
        <a class="link" href="${pdfSrc}" target="_blank" rel="noopener">open it in a new tab</a>.
      </div>
    </div>`;
  } else if (file.is_binary) {
    if (IMAGE_EXTS.includes(ext) && filePath) {
      body = `<div class="md-content"><img src="${escapeHtml(rawUrl(filePath))}" alt="${escapeHtml(file.name || filePath)}" style="max-width:100%;height:auto;border-radius:6px"></div>`;
    } else {
      body = '<div class="text-xs opacity-60 py-4 italic">Binary file — preview not available.</div>';
    }
  } else if (ext === '.md' || ext === '.markdown') {
    const html = (typeof marked !== 'undefined') ? marked.parse(file.content) : `<pre>${escapeHtml(file.content)}</pre>`;
    body = `<div class="md-content">${rewriteImgSrc(html)}</div>`;
  } else if (ext === '.html' || ext === '.htm') {
    const rewritten = rewriteHtmlAssets(file.content);
    const safeSrc = rewritten.replace(/<\/script>/gi, '<\\/script>');
    const displayPath = relativePath(data.path) || file.relative_path || relativePath(file.path) || file.name || '';
    const iframe = `<iframe sandbox="allow-scripts" style="width:100%;height:480px;border:0;background:#fff;display:block" srcdoc="${escapeHtml(safeSrc)}"></iframe>`;
    const mockup = `
      <div class="mockup-browser border border-base-300 bg-base-200">
        <div class="mockup-browser-toolbar">
          <div class="input border border-base-300">${escapeHtml(displayPath)}</div>
        </div>
        <div class="bg-base-100">${iframe}</div>
      </div>`;
    let highlighted;
    try { highlighted = hljs.highlight(file.content, { language: 'html', ignoreIllegals: true }).value; }
    catch(e) { highlighted = escapeHtml(file.content); }
    body = `
      <div role="tablist" class="tabs tabs-bordered tabs-sm mb-2">
        <button role="tab" class="tab tab-active" data-tab="detail-tab-preview" onclick="switchDetailTab(this)">Preview</button>
        <button role="tab" class="tab" data-tab="detail-tab-source" onclick="switchDetailTab(this)">Source</button>
      </div>
      <div id="detail-tab-preview">${mockup}</div>
      <div id="detail-tab-source" class="hidden"><pre class="detail-code-block"><code class="hljs language-html">${highlighted}</code></pre></div>`;
  } else {
    let highlighted;
    try {
      highlighted = lang
        ? hljs.highlight(file.content, { language: lang, ignoreIllegals: true }).value
        : hljs.highlightAuto(file.content).value;
    } catch(e) {
      highlighted = escapeHtml(file.content);
    }
    body = `<pre class="detail-code-block"><code class="hljs">${highlighted}</code></pre>`;
  }

  Tabs.setBody(tabId, header + body);

  // Apply user annotations (highlights, notes, margin cards). Has to run
  // *after* the file body is in the DOM so wrapTextInElement can find the
  // anchor text inside the rendered markdown / HTML / code view.
  if (nodeId && Tabs.isActive(tabId)) {
    requestAnimationFrame(() => Annotations.applyToDetail(nodeId));
  }
}

/* Full select: detail panel + ring + adjacency focus */
/* Cache of full connection payloads (with snippets) keyed by node id, so
   re-opening the Connections tab for the same node doesn't refetch. */
const connectionsCache = new Map();

async function selectNode(nodeId) {
  selectedNode = nodeId;
  try {
    const data = await apiFetch(`/api/node/${encodeURIComponent(nodeId)}`);
    if (data.type === 'file' && data.path) {
      await showFileContent(data);
    } else {
      showDetail(data);
    }
    applyPersistentFocus(nodeId);
  } catch (e) { console.error(e); }
}

/* Fetch the heavy connections payload (with snippets) and re-render the
   Connections tab in place. Uses a per-node cache. */
async function loadConnectionSnippets(nodeId) {
  const target = document.getElementById('detail-tab-connections');
  if (!target) return;
  if (target.dataset.loaded === '1') return;
  if (target.dataset.loading === '1') return;
  // Guard against the panel being for a different node by the time the
  // async fetch resolves (user clicked away).
  const requestedNodeId = nodeId;
  let payload = connectionsCache.get(nodeId);
  if (!payload) {
    target.dataset.loading = '1';
    // Show a per-card loading spinner overlay on every existing connection
    // card so the user sees that snippet content is being fetched.
    target.querySelectorAll('.connection-card').forEach(card => {
      if (card.querySelector('.connection-loading')) return;
      const spinner = document.createElement('div');
      spinner.className = 'connection-loading flex items-center justify-center gap-2 py-2 mt-1.5 text-[11px] opacity-70';
      spinner.innerHTML = '<span class="loading loading-spinner loading-xs"></span><span>Loading snippet…</span>';
      card.appendChild(spinner);
    });
    // If there are no cards yet (still rendering / empty), show a top-level
    // spinner so the user knows something is in flight.
    if (!target.querySelector('.connection-card')) {
      target.innerHTML = `<div class="flex items-center justify-center gap-2 py-6 text-xs opacity-70">
        <span class="loading loading-spinner loading-md"></span>
        <span>Loading connections…</span>
      </div>`;
    }
    try {
      payload = await apiFetch(`/api/node/${encodeURIComponent(nodeId)}/connections`);
      if (!payload || typeof payload !== 'object') throw new Error('empty response');
      connectionsCache.set(nodeId, payload);
    } catch (e) {
      console.error('Failed to load connections for', nodeId, e);
      // Only update the DOM if the user is still looking at this node.
      const stillCurrent = document.getElementById('detail-tab-connections');
      if (stillCurrent && stillCurrent.dataset.nodeId === requestedNodeId) {
        stillCurrent.dataset.loading = '';
        const msg = (e && e.message) ? escapeHtml(e.message) : 'unknown error';
        stillCurrent.innerHTML = `<div class="alert alert-error text-xs py-2 my-2">
          <span>Failed to load connections: ${msg}</span>
          <button class="btn btn-xs btn-ghost" onclick="document.getElementById('detail-tab-connections').dataset.loaded='';loadConnectionSnippets('${requestedNodeId.replace(/'/g, "\\'")}')">Retry</button>
        </div>`;
      }
      return;
    }
  }
  const stillCurrent = document.getElementById('detail-tab-connections');
  if (!stillCurrent || stillCurrent.dataset.nodeId !== requestedNodeId) return;

  let html;
  try {
    const graphNodeIds = new Set((currentGraph?.nodes || []).map(n => n.id));
    const visibleIn  = (payload.edges_in  || []).filter(e => graphNodeIds.has(e.source_id || e.source));
    const visibleOut = (payload.edges_out || []).filter(e => graphNodeIds.has(e.target_id || e.target));
    html = buildConnectionRows(visibleIn, visibleOut);
  } catch (e) {
    console.error('Failed to render connections for', nodeId, e);
    html = `<div class="alert alert-error text-xs py-2 my-2"><span>Could not render connections: ${escapeHtml(e.message || 'unknown error')}</span></div>`;
  }
  stillCurrent.innerHTML = html;
  stillCurrent.dataset.loaded = '1';
  stillCurrent.dataset.loading = '';
}

/* Just the ring highlight, no dimming */
function applyRingOnly(id) {
  if (!graphChart || !currentGraph) return;
  const opt = graphChart.getOption();
  const seriesNodes = opt.series[0].data;
  const idx = seriesNodes.findIndex(n => n.id === id);
  if (idx >= 0) {
    graphChart.dispatchAction({type:'unselect', seriesIndex:0});
    graphChart.dispatchAction({type:'select', seriesIndex:0, dataIndex:idx});
  }
}

/* Ring + adjacency dimming */
function applyPersistentFocus(id) {
  if (!graphChart || !currentGraph) return;
  const edges = currentGraph.edges || [];
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const tc = isDark ? '#dcddde' : '#333';

  const neighborIds = new Set([id]);
  const connectedEdges = new Set();
  edges.forEach((e, idx) => {
    if (e.source === id || e.target === id) {
      neighborIds.add(e.source);
      neighborIds.add(e.target);
      connectedEdges.add(idx);
    }
  });

  const opt = graphChart.getOption();
  const seriesNodes = opt.series[0].data;
  const seriesEdges = opt.series[0].links || opt.series[0].edges;

  seriesNodes.forEach(n => {
    const isNeighbor = neighborIds.has(n.id);
    n.itemStyle = n.itemStyle || {};
    n.itemStyle.opacity = isNeighbor ? 1 : 0.1;
    n.label = n.label || {};
    if (isNeighbor) {
      n.label.show = true;
      n.label.fontSize = n.id === id ? 12 : 10;
      n.label.fontWeight = n.id === id ? 'bold' : 'normal';
      n.label.color = tc;
    } else {
      n.label.show = false;
    }
  });

  seriesEdges.forEach((e, i) => {
    e.lineStyle = e.lineStyle || {};
    if (connectedEdges.has(i)) {
      e.lineStyle.opacity = 0.8;
      e.lineStyle.width = 2.5;
      e.lineStyle.color = '#7c3aed';
    } else {
      e.lineStyle.opacity = 0.03;
      e.lineStyle.width = undefined;
      e.lineStyle.color = isDark ? '#3a3a3a' : '#ccc';
    }
  });

  graphChart.setOption({ series: [{ data: seriesNodes, links: seriesEdges }] });

  const idx = seriesNodes.findIndex(n => n.id === id);
  if (idx >= 0) {
    graphChart.dispatchAction({type:'unselect', seriesIndex:0});
    graphChart.dispatchAction({type:'select', seriesIndex:0, dataIndex:idx});
  }
}

function clearFocus() {
  if (!graphChart || !currentGraph) return;
  selectedNode = null;
  renderGraph(currentGraph);
  showWelcomePanel();
}

function highlightNode(id) { applyRingOnly(id); }

function focusNodeInGraph(id) {
  if (!graphChart||!currentGraph) return;
  const i = (currentGraph.nodes||[]).findIndex(n=>n.id===id);
  if (i<0) return;
  const n = currentGraph.nodes[i];
  if (n.x!=null&&n.y!=null) graphChart.dispatchAction({type:'graphRoam',seriesIndex:0,center:[n.x,n.y],zoom:2});
  applyPersistentFocus(id);
}

/* ── Content Tabs (left pane) ──────────────────────────────────
   Tab manager for the left detail pane. The "welcome" tab is the
   permanent base tab (welcome content + index analytics). Clicking
   a node, edge, or file in the side nav opens an additional tab
   with an "x" close button. */
const Tabs = (function () {
  const list = []; // {id, title, icon, closable, html, onActivate}
  let activeId = null;
  const listEl = () => document.getElementById('content-tabs');
  const bodyEl = () => document.getElementById('left-detail-content');
  const find = id => list.find(t => t.id === id);
  const indexOf = id => list.findIndex(t => t.id === id);

  function render() {
    const el = listEl();
    if (!el) return;
    el.innerHTML = '';
    for (const t of list) {
      const btn = document.createElement('div');
      btn.className = 'content-tab' + (t.id === activeId ? ' active' : '');
      btn.title = t.tooltip || t.title;
      btn.dataset.tabId = t.id;
      btn.setAttribute('role', 'tab');
      btn.innerHTML =
        (t.icon ? `<span class="content-tab-icon">${t.icon}</span>` : '') +
        `<span class="content-tab-label">${escapeHtml(t.title)}</span>` +
        (t.closable ? `<span class="content-tab-close" title="Close tab">×</span>` : '');
      btn.addEventListener('click', e => {
        if (e.target && e.target.classList.contains('content-tab-close')) {
          e.stopPropagation();
          close(t.id);
          return;
        }
        activate(t.id);
      });
      el.appendChild(btn);
    }
  }

  function activate(id) {
    const t = find(id);
    if (!t) return;
    // Save the current DOM into the previously-active tab cache so
    // its state (e.g. expanded sub-tabs) is preserved on switch back.
    if (activeId && activeId !== id) {
      const old = find(activeId);
      if (old) old.html = bodyEl().innerHTML;
    }
    activeId = id;
    bodyEl().innerHTML = t.html || '';
    render();
    if (typeof t.onActivate === 'function') {
      try { t.onActivate(); } catch (e) { console.error(e); }
    }
  }

  function open(spec) {
    let t = find(spec.id);
    if (!t) {
      t = {
        id: spec.id,
        title: spec.title || spec.id,
        tooltip: spec.tooltip || '',
        icon: spec.icon || '',
        closable: spec.closable !== false,
        html: '',
        onActivate: spec.onActivate || null,
      };
      list.push(t);
    } else {
      if (spec.title) t.title = spec.title;
      if (spec.tooltip != null) t.tooltip = spec.tooltip;
      if (spec.icon != null) t.icon = spec.icon;
      if (spec.onActivate) t.onActivate = spec.onActivate;
    }
    activate(spec.id);
    return t;
  }

  function close(id) {
    const i = indexOf(id);
    if (i < 0) return;
    const t = list[i];
    if (!t.closable) return;
    list.splice(i, 1);
    if (activeId === id) {
      activeId = null;
      const next = list[Math.max(0, i - 1)] || list[0];
      if (next) activate(next.id);
      else { bodyEl().innerHTML = ''; render(); }
    } else {
      render();
    }
  }

  function setBody(id, html) {
    const t = find(id);
    if (!t) return;
    t.html = html;
    if (activeId === id) bodyEl().innerHTML = html;
  }

  function active() { return activeId; }
  function isActive(id) { return activeId === id; }

  return { open, close, activate, setBody, active, isActive, render };
})();

function openEdgeTab(edgeData) {
  if (!edgeData) return;
  const src = edgeData.source, tgt = edgeData.target;
  const rel = edgeData._rel || edgeData.rel || 'edge';
  const tabId = `edge:${src}->${tgt}|${rel}`;
  const nodes = (currentGraph && currentGraph.nodes) || [];
  const srcNode = nodes.find(n => n.id === src);
  const tgtNode = nodes.find(n => n.id === tgt);
  const srcName = (srcNode && srcNode.name) || src;
  const tgtName = (tgtNode && tgtNode.name) || tgt;
  Tabs.open({ id: tabId, title: `${srcName} → ${tgtName}`, closable: true });
  const html = `
    <div class="flex items-center gap-2 flex-wrap mb-3">
      <span class="badge badge-sm badge-primary font-semibold">edge</span>
      <span class="badge badge-sm badge-outline font-mono">${escapeHtml(rel)}</span>
    </div>
    <div class="text-xs space-y-2">
      <div><span class="opacity-60 uppercase tracking-wider mr-1 text-[10px]">From</span>
        <a href="#" class="link link-primary" onclick="event.preventDefault();selectNode('${String(src).replace(/'/g, "\\'")}')">${escapeHtml(srcName)}</a>
        <div class="opacity-50 font-mono break-all">${escapeHtml(src)}</div>
      </div>
      <div><span class="opacity-60 uppercase tracking-wider mr-1 text-[10px]">To</span>
        <a href="#" class="link link-primary" onclick="event.preventDefault();selectNode('${String(tgt).replace(/'/g, "\\'")}')">${escapeHtml(tgtName)}</a>
        <div class="opacity-50 font-mono break-all">${escapeHtml(tgt)}</div>
      </div>
      <div><span class="opacity-60 uppercase tracking-wider mr-1 text-[10px]">Relationship</span>
        <span class="font-mono">${escapeHtml(rel)}</span>
      </div>
    </div>
  `;
  Tabs.setBody(tabId, html);
}

/* ── Detail Panel (left pane, rich formatting) ─────────────────── */
function guessLang(type, path) {
  if (path) {
    const ext = path.split('.').pop().toLowerCase();
    const map = { py:'python', js:'javascript', ts:'typescript', jsx:'javascript', tsx:'typescript', rb:'ruby', go:'go', rs:'rust', java:'java', c:'c', cpp:'cpp', h:'c', hpp:'cpp', cs:'csharp', swift:'swift', kt:'kotlin', sh:'bash', yml:'yaml', yaml:'yaml', json:'json', html:'html', css:'css', sql:'sql', md:'markdown' };
    if (map[ext]) return map[ext];
  }
  if (['function','method','class'].includes(type)) return 'python';
  return '';
}

async function showDetail(data) {
  const tabId = 'node:' + data.id;
  const rel = relativePath(data.path);
  const tabTooltip = rel
    ? (data.line_start != null ? `${rel}:${data.line_start}` : rel)
    : (data.name || '');
  Tabs.open({
    id: tabId,
    title: data.name || 'Node',
    tooltip: tabTooltip,
    closable: true,
    onActivate: () => {
      if (data.id) applyPersistentFocus(data.id);
      if (data.id) requestAnimationFrame(() => Annotations.applyToDetail(data.id));
    },
  });
  const typeColor = NODE_COLORS[data.type] || '#888';
  const lang = guessLang(data.type, data.path);

  // Try to load the full file when we have a path + line range, so we can
  // show the whole file with the relevant region highlighted instead of
  // just the snippet.
  let fullSource = '';
  let useFullFile = false;
  const startLine = data.line_start || null;
  const endLine = data.line_end || startLine;
  if (data.path && startLine && data.type !== 'directory' && data.type !== 'file') {
    try {
      const file = await apiFetch(`/api/file/content?path=${encodeURIComponent(data.path)}`);
      if (file && !file.is_binary && typeof file.content === 'string') {
        fullSource = file.content;
        useFullFile = true;
      }
    } catch (e) { /* fall back to snippet */ }
  }

  const source = useFullFile ? fullSource : (data.source || '');
  let codeHtml = '';
  if (source) {
    try {
      const highlighted = lang
        ? hljs.highlight(source, { language: lang, ignoreIllegals: true }).value
        : hljs.highlightAuto(source).value;
      if (useFullFile) {
        codeHtml = renderLinedCode(highlighted, startLine, endLine);
      } else {
        codeHtml = `<pre class="detail-code-block"><code class="hljs">${highlighted}</code></pre>`;
      }
    } catch(e) {
      const esc = source.replace(/</g,'&lt;').replace(/>/g,'&gt;');
      codeHtml = useFullFile
        ? renderLinedCode(esc, startLine, endLine)
        : `<pre class="detail-code-block"><code>${esc}</code></pre>`;
    }
  } else {
    codeHtml = '<div class="text-xs opacity-40 italic py-2">No source available</div>';
  }

  const fileIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3 inline-block"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" /></svg>';
  const lineIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3 inline-block"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 8.25h15m-16.5 7.5h15m-1.8-13.5l-3.9 19.5m-2.1-19.5l-3.9 19.5" /></svg>';

  const graphNodeIds = new Set((currentGraph?.nodes||[]).map(n => n.id));
  const visibleIn = (data.edges_in||[]).filter(e => graphNodeIds.has(e.source_id || e.source));
  const visibleOut = (data.edges_out||[]).filter(e => graphNodeIds.has(e.target_id || e.target));

  const connectionRows = buildConnectionRows(visibleIn, visibleOut);

  const isDir = data.type === 'directory';

  const bookmarkBtn = data.id ? bookmarkButtonHtml(data.id, data.name) : '';
  const noteIndicator = data.id ? noteIndicatorHtml(data.id) : '';
  const headerHtml = isDir
    ? `<div class="flex items-center gap-2 flex-wrap mb-3">
        <span class="badge badge-sm font-semibold" style="background:${typeColor};color:${badgeTextColor(typeColor)}">${data.type}</span>
        ${bookmarkBtn}
        ${noteIndicator}
      </div>`
    : `<div class="flex items-center gap-2 flex-wrap mb-3">
        <span class="badge badge-sm font-semibold" style="background:${typeColor};color:${badgeTextColor(typeColor)}">${data.type}</span>
        ${lang ? `<span class="badge badge-sm badge-outline font-mono">${lang}</span>` : ''}
        ${data.path ? `<span class="badge badge-sm badge-ghost font-mono gap-1" title="${escapeHtml(data.path)}">${fileIcon} ${escapeHtml(relativePath(data.path))}</span>` : ''}
        ${data.line_start!=null ? `<span class="badge badge-sm badge-ghost font-mono gap-1">${lineIcon} L${data.line_start}${data.line_end ? '-'+data.line_end : ''}</span>` : ''}
        ${bookmarkBtn}
        ${noteIndicator}
      </div>`;

  const sourceSectionHtml = isDir
    ? ''
    : `<div class="mb-3">
        <div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1">Source</div>
        ${codeHtml}
      </div>`;

  Tabs.setBody(tabId, `
    ${headerHtml}
    <div role="tablist" class="tabs tabs-bordered tabs-sm mb-2">
      <button role="tab" class="tab tab-active" data-tab="detail-tab-content" onclick="switchDetailTab(this)">Details</button>
      <button role="tab" class="tab" data-tab="detail-tab-connections" onclick="switchDetailTab(this)">Connections <span class="badge badge-xs badge-primary ml-1">${visibleIn.length + visibleOut.length}</span></button>
    </div>
    <div id="detail-tab-content">
      ${sourceSectionHtml}
    </div>
    <div id="detail-tab-connections" class="hidden" data-node-id="${escapeHtml(data.id || '')}">
      ${connectionRows}
    </div>
  `);

  // Auto-scroll the highlighted region into view (if any).
  if (useFullFile && startLine && Tabs.isActive(tabId)) {
    requestAnimationFrame(() => {
      const body = document.getElementById('left-detail-content');
      const target = body && body.querySelector('.code-line.hl-start');
      if (target) target.scrollIntoView({ block: 'center' });
    });
  }

  // Apply user annotations (highlights, notes, bookmark state).
  if (data.id && Tabs.isActive(tabId)) {
    requestAnimationFrame(() => { Annotations.applyToDetail(data.id); });
  }
}

/* Wrap highlight.js HTML output line-by-line, adding line numbers and
   marking the [start, end] range as highlighted. Balances open <span>
   tags across newlines so multi-line tokens render correctly. */
function renderLinedCode(html, start, end) {
  const lines = [''];
  const stack = [];
  let i = 0;
  while (i < html.length) {
    const c = html[i];
    if (c === '<') {
      const close = html.indexOf('>', i);
      if (close === -1) { lines[lines.length - 1] += html.slice(i); break; }
      const tag = html.slice(i, close + 1);
      if (tag.startsWith('</')) stack.pop();
      else if (!tag.endsWith('/>') && !/^<(br|img|hr|input|meta|link)\b/i.test(tag)) stack.push(tag);
      lines[lines.length - 1] += tag;
      i = close + 1;
    } else if (c === '\n') {
      for (let j = 0; j < stack.length; j++) lines[lines.length - 1] += '</span>';
      lines.push(stack.join(''));
      i++;
    } else {
      lines[lines.length - 1] += c;
      i++;
    }
  }
  const COLLAPSE_THRESHOLD = 10;
  const collapseEnabled = !!start && start > COLLAPSE_THRESHOLD;
  const collapseId = collapseEnabled ? `src-collapse-${(renderLinedCode._uid = (renderLinedCode._uid || 0) + 1)}` : null;
  const out = [];
  if (collapseEnabled) {
    out.push(`<div class="code-collapse-wrap"><div id="${collapseId}" class="code-collapsed-lines hidden">`);
  }
  let toggleInserted = false;
  for (let k = 0; k < lines.length; k++) {
    const ln = k + 1;
    if (collapseEnabled && !toggleInserted && ln === start) {
      out.push(`</div><button type="button" class="code-show-all-btn" onclick="toggleSourceCollapse(this, '${collapseId}')" data-from="1" data-to="${start - 1}">────── Show lines 1–${start - 1} ──────</button>`);
      toggleInserted = true;
    }
    const cls = ['code-line'];
    if (start && ln >= start && ln <= (end || start)) cls.push('hl');
    if (start && ln === start) cls.push('hl-start');
    if (start && ln === (end || start)) cls.push('hl-end');
    out.push(`<div class="${cls.join(' ')}" data-line="${ln}"><span class="ln">${ln}</span><span class="lc">${lines[k] || ' '}</span></div>`);
  }
  if (collapseEnabled) out.push(`</div>`);
  return `<pre class="detail-code-block lined"><code class="hljs">${out.join('')}</code></pre>`;
}

function toggleSourceCollapse(btn, id) {
  const wrap = document.getElementById(id);
  if (!wrap) return;
  const hidden = wrap.classList.toggle('hidden');
  if (hidden) {
    btn.textContent = `────── Show lines ${btn.dataset.from}–${btn.dataset.to} ──────`;
  } else {
    btn.textContent = `────── Hide lines ${btn.dataset.from}–${btn.dataset.to} ──────`;
  }
}

function switchDetailTab(tabEl) {
  const parent = tabEl.closest('[role="tablist"]');
  parent.querySelectorAll('.tab').forEach(t => t.classList.remove('tab-active'));
  tabEl.classList.add('tab-active');
  const targetId = tabEl.dataset.tab;
  const container = parent.parentElement;
  container.querySelectorAll('[id^="detail-tab-"]').forEach(p => p.classList.toggle('hidden', p.id !== targetId));
  // Lazy-load the snippet-rich connections payload the first time the
  // Connections tab is opened for this node.
  if (targetId === 'detail-tab-connections') {
    const panel = document.getElementById('detail-tab-connections');
    const nodeId = panel?.dataset.nodeId;
    if (nodeId) loadConnectionSnippets(nodeId);
  }
}

function buildConnectionRows(edgesIn, edgesOut) {
  const inArr = edgesIn || [];
  const outArr = edgesOut || [];

  if (!inArr.length && !outArr.length) {
    return '<div class="text-xs opacity-60 py-4 text-center">No connections</div>';
  }

  const fileIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3 inline-block flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" /></svg>';
  const lineIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3 inline-block flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 8.25h15m-16.5 7.5h15m-1.8-13.5l-3.9 19.5m-2.1-19.5l-3.9 19.5" /></svg>';
  const arrowInIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5 opacity-60 flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3" /></svg>';
  const arrowOutIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5 opacity-60 flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M15 15l6-6m0 0l-6-6m6 6H9a6 6 0 000 12h3" /></svg>';
  const replaceIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" /></svg>';

  function renderCard(e, dir) {
    const isIn = dir === 'in';
    const nodeId  = isIn ? (e.source_id || e.source) : (e.target_id || e.target);
    const name    = (isIn ? e.source_name : e.target_name) || e.name || nodeId || '?';
    const nType   = (isIn ? e.source_type : e.target_type) || e.type || '';
    const path    = (isIn ? e.source_path : e.target_path) || '';
    const lstart  = isIn ? e.source_line_start : e.target_line_start;
    const lend    = isIn ? e.source_line_end   : e.target_line_end;
    const lang    = (isIn ? e.source_lang : e.target_lang) || '';
    const snippet = isIn ? e.source_snippet : e.target_snippet;
    const rel     = e.rel || e.type || 'related';
    const sameFile = !!e.same_file;
    const color   = NODE_COLORS[nType] || '#888';
    const escId   = (nodeId || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    const arrow   = isIn ? arrowInIcon : arrowOutIcon;
    const lineStr = lstart != null ? `L${lstart}${lend && lend !== lstart ? '-'+lend : ''}` : '';
    const relPath = path ? (typeof relativePath === 'function' ? relativePath(path) : path) : '';

    let snippetHtml = '';
    if (snippet && snippet.error) {
      // Backend reported a problem reading the file (deleted, moved,
      // permissions, lines out of range after edit, etc.).
      snippetHtml = `<div class="alert alert-warning py-1 px-2 mt-1.5 text-[10px] gap-1 rounded">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5 flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.732 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" /></svg>
        <span>preview unavailable — ${escapeHtml(snippet.error)}</span>
      </div>`;
    } else if (snippet && Array.isArray(snippet.lines) && snippet.lines.length) {
      const joined = snippet.lines.join('\n');
      let highlighted;
      try {
        highlighted = (lang && window.hljs && hljs.getLanguage(lang))
          ? hljs.highlight(joined, { language: lang, ignoreIllegals: true }).value
          : (window.hljs ? hljs.highlightAuto(joined).value : escapeHtml(joined));
      } catch (_) {
        highlighted = escapeHtml(joined);
      }
      const hlLines = highlighted.split('\n');
      const startLn = snippet.start || 1;
      const hlNum   = snippet.highlight;
      const rows = hlLines.map((ln, i) => {
        const num = startLn + i;
        const cls = ['code-line'];
        if (hlNum != null && num === hlNum) cls.push('hl', 'hl-start', 'hl-end');
        return `<div class="${cls.join(' ')}" data-line="${num}"><span class="ln">${num}</span><span class="lc">${ln || ' '}</span></div>`;
      }).join('');
      const truncatedNote = snippet.truncated
        ? '<div class="text-[10px] opacity-50 italic px-1 pt-0.5">… truncated</div>'
        : '';
      snippetHtml = `<pre class="detail-code-block lined connection-snippet mt-1.5 mb-0"><code class="hljs">${rows}</code></pre>${truncatedNote}`;
    }

    return `<div class="connection-card border border-base-300 rounded p-2 mb-2 hover:bg-base-200 transition-colors">
      <div class="flex items-center gap-2 mb-1">
        ${arrow}
        <span class="badge badge-xs font-semibold flex-shrink-0" style="background:${color};color:#000">${escapeHtml(nType || '?')}</span>
        <span class="text-xs font-semibold flex-1 truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
        ${sameFile ? '<span class="badge badge-xs badge-warning flex-shrink-0" title="Defined in the same file">same file</span>' : ''}
        <span class="badge badge-xs badge-outline flex-shrink-0">${escapeHtml(rel)}</span>
        <button class="btn btn-xs btn-ghost btn-square" title="Open this connection in the detail view" onclick="event.stopPropagation();connectionClick('${escId}')">${replaceIcon}</button>
      </div>
      ${path ? `<div class="flex items-center gap-1 text-[11px] opacity-70 font-mono truncate" title="${escapeHtml(path)}">
        ${fileIcon}<span class="truncate">${escapeHtml(relPath)}</span>
        ${lineStr ? `<span class="opacity-60">·</span>${lineIcon}<span>${lineStr}</span>` : ''}
      </div>` : ''}
      ${snippetHtml}
    </div>`;
  }

  let html = '';

  if (inArr.length) {
    html += `<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Incoming <span class="opacity-50">(${inArr.length})</span></div>`;
    html += '<div class="mb-3">';
    inArr.forEach(e => { html += renderCard(e, 'in'); });
    html += '</div>';
  }

  if (outArr.length) {
    html += `<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Outgoing <span class="opacity-50">(${outArr.length})</span></div>`;
    html += '<div class="mb-3">';
    outArr.forEach(e => { html += renderCard(e, 'out'); });
    html += '</div>';
  }

  return html;
}

function connectionClick(nodeId) {
  if (!nodeId) return;
  selectNode(nodeId);
  focusNodeInGraph(nodeId);
}

/* ── Search ────────────────────────────────────────────────────── */
async function searchNodes(query) {
  if (!query.trim()) return;
  const c = document.getElementById('search-results');
  c.innerHTML = '<div class="p-2 text-xs opacity-50">Searching...</div>';
  c.classList.add('visible');
  try {
    const data = await apiFetch(`/api/search?q=${encodeURIComponent(query)}&top=10`);
    if (!data.results||!data.results.length) { c.innerHTML = '<div class="p-2 text-xs opacity-50">No results</div>'; return; }
    c.innerHTML = data.results.map(r => `<div class="search-result-item p-2.5 cursor-pointer hover:bg-base-300 border-b border-base-300 last:border-0" data-id="${r.id}">
      <div class="flex items-center gap-1.5 mb-1">
        <span class="font-semibold text-sm">${r.name}</span>
        <span class="badge badge-xs font-semibold" style="background:${NODE_COLORS[r.type]||'#444'};color:${badgeTextColor(NODE_COLORS[r.type]||'#444')}">${r.type}</span>
        ${r.score!=null?`<span class="badge badge-xs badge-ghost font-mono ml-auto">${r.score.toFixed(2)}</span>`:''}
      </div>
      ${r.path?`<div class="text-[11px] opacity-70 font-mono truncate">${r.path}</div>`:''}</div>`).join('');
    c.querySelectorAll('[data-id]').forEach(item => item.addEventListener('click', () => {
      c.classList.remove('visible'); switchView('graph');
      setTimeout(() => { focusNodeInGraph(item.dataset.id); selectNode(item.dataset.id); }, 100);
    }));
  } catch (e) { c.innerHTML = '<div class="p-2 text-xs text-error">Search failed</div>'; }
}

/* ── Idea Cloud ────────────────────────────────────────────────── */
function renderWordCloud(data) {
  const container = document.getElementById('wordcloud-container');
  if (!container || !container.offsetHeight) return;
  if (!wordCloudChart) wordCloudChart = echarts.init(container);
  wordCloudChart.setOption({ series: [{ type:'wordCloud', shape:'circle', sizeRange:[10,48], rotationRange:[0,0], gridSize:2, left:0, top:0, right:0, bottom:0, width:'100%', height:'100%',
    textStyle: { fontFamily:'Inter,sans-serif', color:()=>{const c=Object.values(NODE_COLORS);return c[Math.floor(Math.random()*c.length)];} },
    emphasis:{textStyle:{color:'#7c3aed'}}, data }] });
  wordCloudChart.off('click');
  wordCloudChart.on('click', p => { askChatFromCloud(p.name); });
  updateWordCloudToggle();
}

function updateWordCloudToggle() {
  const btn = document.getElementById('wordcloud-toggle');
  if (!btn) return;
  if (wordCloudAllData.length <= WORDCLOUD_DEFAULT_LIMIT) {
    btn.classList.add('hidden');
  } else {
    btn.classList.remove('hidden');
    btn.textContent = wordCloudExpanded ? 'Show Less' : 'Show More';
  }
}

function toggleWordCloud() {
  wordCloudExpanded = !wordCloudExpanded;
  const data = wordCloudExpanded ? wordCloudAllData : wordCloudAllData.slice(0, WORDCLOUD_DEFAULT_LIMIT);
  renderWordCloud(data);
}

async function loadWordCloud() {
  try {
    const data = await apiFetch('/api/wordcloud');
    wordCloudAllData = data.sort((a, b) => b.value - a.value);
    wordCloudExpanded = false;
    renderWordCloud(wordCloudAllData.slice(0, WORDCLOUD_DEFAULT_LIMIT));
  } catch (e) { console.error(e); }
}

/* ── Graph Zoom Controls ───────────────────────────────────────── */
function graphZoom(factor) {
  if (!graphChart) return;
  const opt = graphChart.getOption();
  const cur = (opt.series && opt.series[0] && opt.series[0].zoom) || 1;
  const next = Math.max(0.1, Math.min(20, cur * factor));
  graphChart.setOption({ series: [{ zoom: next }] });
}

function graphResetZoom() {
  if (!graphChart) return;
  graphChart.setOption({ series: [{ zoom: 1, center: null }] });
  if (currentGraph) renderGraph(currentGraph);
}

/* ── Pane Fullscreen Overlay ───────────────────────────────────── */
let _activeOverlay = null;

function togglePaneFullscreen(paneId) {
  if (_activeOverlay) { closePaneFullscreen(); return; }

  const pane = document.getElementById(paneId);
  if (!pane) return;

  const overlay = document.createElement('div');
  overlay.className = 'pane-overlay';
  overlay.innerHTML = `<div class="pane-overlay-card">
      <div class="pane-overlay-content"></div>
      <button type="button" class="pane-overlay-close" title="Close">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
      </button>
    </div>`;

  const content = overlay.querySelector('.pane-overlay-content');
  const closeBtn = overlay.querySelector('.pane-overlay-close');

  const chart = paneId === 'vsplit-top' ? graphChart : wordCloudChart;
  const chartEl = paneId === 'vsplit-top' ? document.getElementById('graph-chart') : document.getElementById('wordcloud-container');

  _activeOverlay = { overlay, paneId, pane, chartEl, parentEl: chartEl.parentElement };

  content.appendChild(chartEl);
  document.body.appendChild(overlay);

  closeBtn.addEventListener('click', closePaneFullscreen);
  overlay.addEventListener('click', e => { if (e.target === overlay) closePaneFullscreen(); });

  setTimeout(() => { if (chart) chart.resize(); }, 50);
}

function closePaneFullscreen() {
  if (!_activeOverlay) return;
  const { overlay, paneId, pane, chartEl, parentEl } = _activeOverlay;

  parentEl.prepend(chartEl);
  overlay.remove();
  _activeOverlay = null;

  const chart = paneId === 'vsplit-top' ? graphChart : wordCloudChart;
  setTimeout(() => { if (chart) chart.resize(); }, 50);
}

/* ── Stats ─────────────────────────────────────────────────────── */
async function loadStats() {
  try {
    const data = await apiFetch('/api/stats');
    return data;
  } catch (e) { return null; }
}

async function showWelcomePanel() {
  Tabs.open({
    id: 'welcome',
    title: 'My Hub',
    closable: false,
    onActivate: () => {
      // Drop any node selection in the graph; do NOT call clearFocus()
      // since that re-opens the welcome tab and would recurse.
      if (selectedNode && graphChart && currentGraph) {
        selectedNode = null;
        renderGraph(currentGraph);
      }
      // Re-render whichever sub-tab is active so dynamic event listeners
      // (lost when Tabs.activate caches the body as innerHTML) get rebound.
      const subTab = document.querySelector('#hub-pane-annotations:not(.hidden)') ? 'annotations' : 'main';
      if (subTab === 'annotations') Annotations.openTab(Annotations.currentSubFilter() || 'notes');
      else loadHubRecent();
    },
  });
  const stats = await loadStats();

  let statsHtml = '';
  if (stats) {
    statsHtml += `<div class="stats stats-horizontal shadow bg-base-200 w-full mb-3">
      <div class="stat py-2 px-3"><div class="stat-title text-[10px]">Nodes</div><div class="stat-value text-base text-primary">${(stats.total_nodes||0).toLocaleString()}</div></div>
      <div class="stat py-2 px-3"><div class="stat-title text-[10px]">Edges</div><div class="stat-value text-base text-secondary">${(stats.total_edges||0).toLocaleString()}</div></div>
    </div>`;

    const hasNodeTypes = stats.node_types && Object.keys(stats.node_types).length;
    const hasEdgeTypes = stats.edge_types && Object.keys(stats.edge_types).length;

    if (hasNodeTypes) {
      statsHtml += '<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Node Types</div>';
      statsHtml += '<div id="welcome-node-chart" style="width:100%;height:200px" class="mb-3"></div>';
    }
    if (hasEdgeTypes) {
      statsHtml += '<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Edge Types</div>';
      statsHtml += '<div id="welcome-edge-chart" style="width:100%;height:180px" class="mb-3"></div>';
    }
  }

  Tabs.setBody('welcome', `
    <div role="tablist" class="tabs tabs-bordered tabs-sm mb-3" id="hub-tabs">
      <button role="tab" class="tab tab-active" data-hub-tab="main" onclick="switchHubTab('main')">Main</button>
      <button role="tab" class="tab" data-hub-tab="annotations" onclick="switchHubTab('annotations')">Notes &amp; Bookmarks</button>
    </div>

    <div id="hub-pane-main" data-hub-pane="main">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="md-content">
          <h2>My Hub</h2>
          <p>Explore your codebase as an interactive graph. Click any node to inspect its source code, connections, and relationships.</p>
          <h3>Quick Start</h3>
          <ul>
            <li><strong>My Files</strong> &mdash; index a folder to build the graph</li>
            <li><strong>AI Chat</strong> &mdash; type a question; add <code>find:</code> for graph-only search or <code>chat:</code> for plain chat</li>
            <li><strong>Click a node</strong> &mdash; view source and connections</li>
            <li><strong>Click empty space</strong> &mdash; reset the view</li>
            <li><strong>Depth slider</strong> &mdash; control how many nodes are shown</li>
            <li><strong>Highlight text</strong> in a node detail panel to save a highlight or note; click ★ to bookmark a node</li>
          </ul>
        </div>
        <div>
          <div class="md-content"><h3>Recent</h3></div>
          <div id="hub-recent" class="rounded-lg text-xs">
            <div class="opacity-60 italic p-3">Loading recent activity…</div>
          </div>
        </div>
      </div>
      ${stats ? '<div class="md-content mt-4"><h3><b><i>My Files</i></b> Index Analytics</h3></div>' : ''}
      ${statsHtml}
    </div>

    <div id="hub-pane-annotations" data-hub-pane="annotations" class="hidden">
      <div class="ann-toolbar" style="border-radius:6px;border:1px solid oklch(var(--b3));margin-bottom:10px;padding:8px 10px;display:flex;align-items:center;gap:8px;background:oklch(var(--b2))">
        <button type="button" class="ann-tab active" data-ann-tab="notes" onclick="Annotations.openTab('notes')">📝 Notes</button>
        <button type="button" class="ann-tab" data-ann-tab="bookmarks" onclick="Annotations.openTab('bookmarks')">⭐ Bookmarks</button>
        <button type="button" class="ann-tab" data-ann-tab="highlights" onclick="Annotations.openTab('highlights')">🖍️ Highlights</button>
        <span class="flex-1"></span>
        <button type="button" class="btn btn-ghost btn-xs btn-square" onclick="Annotations.refreshList()" title="Refresh">↻</button>
      </div>
      <div id="annotations-list" class="ann-list" style="padding:0">
        <div class="ann-empty">Click a tab above to load…</div>
      </div>
    </div>
  `);

  loadHubRecent();

  if (stats) {
    if (stats.node_types && Object.keys(stats.node_types).length) {
      const el = document.getElementById('welcome-node-chart');
      if (el && window.echarts) {
        const data = Object.entries(stats.node_types).map(([t,c]) => ({
          name: t, value: c, itemStyle: { color: NODE_COLORS[t] || '#888' }
        }));
        echarts.init(el).setOption({
          tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
          legend: { type: 'scroll', orient: 'vertical', right: 0, top: 'middle', textStyle: { fontSize: 10 } },
          series: [{
            type: 'pie', radius: ['45%', '70%'], center: ['35%', '50%'],
            avoidLabelOverlap: true, label: { show: false }, labelLine: { show: false },
            data
          }]
        });
      }
    }
    if (stats.edge_types && Object.keys(stats.edge_types).length) {
      const el = document.getElementById('welcome-edge-chart');
      if (el && window.echarts) {
        const entries = Object.entries(stats.edge_types);
        echarts.init(el).setOption({
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          grid: { left: 80, right: 20, top: 10, bottom: 20 },
          xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
          yAxis: { type: 'category', data: entries.map(e => e[0]), axisLabel: { fontSize: 10 } },
          series: [{
            type: 'bar', data: entries.map(e => e[1]),
            itemStyle: { color: '#6366f1' },
            label: { show: true, position: 'right', fontSize: 10 }
          }]
        });
      }
    }
  }
}

/* Switch between sub-tabs inside the My Hub panel. */
function switchHubTab(tab) {
  document.querySelectorAll('#hub-tabs [data-hub-tab]').forEach(b => {
    b.classList.toggle('tab-active', b.dataset.hubTab === tab);
  });
  const main = document.getElementById('hub-pane-main');
  const ann  = document.getElementById('hub-pane-annotations');
  if (main) main.classList.toggle('hidden', tab !== 'main');
  if (ann)  ann.classList.toggle('hidden',  tab !== 'annotations');
  if (tab === 'annotations') {
    Annotations.openTab(Annotations.currentSubFilter() || 'notes');
  } else {
    loadHubRecent();
  }
}

/* Format an ISO timestamp into a human-friendly relative time string,
   e.g. "5 minutes ago", "3 hours ago", "2 days ago", "4 months ago". */
function formatTimeAgo(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return diffSec <= 1 ? 'just now' : `${diffSec} seconds ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} ${diffMin === 1 ? 'minute' : 'minutes'} ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} ${diffHr === 1 ? 'hour' : 'hours'} ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay} ${diffDay === 1 ? 'day' : 'days'} ago`;
  const diffMo = Math.floor(diffDay / 30);
  if (diffMo < 12) return `${diffMo} ${diffMo === 1 ? 'month' : 'months'} ago`;
  const diffYr = Math.floor(diffMo / 12);
  return `${diffYr} ${diffYr === 1 ? 'year' : 'years'} ago`;
}

/* Fetch recent chats + notes, merge sorted by timestamp, render last 10. */
async function loadHubRecent() {
  const host = document.getElementById('hub-recent');
  if (!host) return;
  host.innerHTML = '<div class="opacity-60 italic p-3">Loading recent activity…</div>';

  const [threads, notes] = await Promise.all([
    apiFetch('/api/chat/threads').catch(() => []),
    apiFetch('/api/annotations?type=note').then(d => d.annotations || []).catch(() => []),
  ]);

  const items = [];
  (threads || []).forEach(t => {
    const ts = t.updated_at || t.created_at || '';
    items.push({
      kind: 'chat',
      ts,
      id: t.id,
      title: t.title || 'Untitled chat',
      subtitle: `${t.message_count || 0} ${t.message_count === 1 ? 'message' : 'messages'}`,
    });
  });
  (notes || []).forEach(a => {
    const ts = a.last_modified_at || a.created_at || '';
    const tgt = a.target || {};
    const tgtLabel = tgt.type === 'node' ? (tgt.node_id || '') : (tgt.file_path || '');
    const m = (a.content || '').match(/^>\s?[\s\S]+?\n\n([\s\S]*)$/);
    const body = (m ? m[1] : (a.content || '')).trim();
    const title = body ? body.split('\n')[0].slice(0, 80) : 'Note';
    items.push({
      kind: 'note',
      ts,
      id: a.id,
      nodeId: tgt.type === 'node' ? tgt.node_id : '',
      filePath: tgt.type === 'file' ? tgt.file_path : '',
      title,
      subtitle: tgtLabel,
    });
  });

  items.sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
  const top = items.slice(0, 10);

  if (!top.length) {
    host.innerHTML = '<div class="opacity-60 italic p-3">No recent chats or notes yet.</div>';
    return;
  }

  host.innerHTML = top.map(it => {
    const when = escapeHtml(formatTimeAgo(it.ts));
    if (it.kind === 'chat') {
      return `<div class="recent-item recent-chat" data-recent-kind="chat" data-recent-id="${escapeHtml(it.id)}" title="Resume chat">
          <div class="recent-row">
            <span class="recent-tag">CHAT</span>
            <span class="recent-title">${escapeHtml(it.title)}</span>
            <span class="recent-time">${when}</span>
          </div>
          <div class="recent-sub">${escapeHtml(it.subtitle)}</div>
        </div>`;
    }
    return `<div class="recent-item recent-note" data-recent-kind="note" data-recent-id="${escapeHtml(it.id)}" data-recent-node="${escapeHtml(it.nodeId || '')}" data-recent-file="${escapeHtml(it.filePath || '')}" title="Open note">
        <div class="recent-row">
          <span class="recent-tag">NOTE</span>
          <span class="recent-title">${escapeHtml(it.title)}</span>
          <span class="recent-time">${when}</span>
        </div>
        ${it.subtitle ? `<div class="recent-sub">${escapeHtml(it.subtitle)}</div>` : ''}
      </div>`;
  }).join('');

  host.querySelectorAll('.recent-item').forEach(el => {
    el.addEventListener('click', () => {
      if (el.dataset.recentKind === 'chat') openRecentChat(el.dataset.recentId);
      else openRecentNote(el.dataset.recentId, el.dataset.recentNode, el.dataset.recentFile);
    });
  });
}

/* Resume a chat thread in the bottom chat panel. */
function openRecentChat(threadId) {
  if (!threadId) return;
  switchView('graph');
  setTimeout(() => loadChatThread(threadId), 60);
}

/* Open the file/node a note was taken on, without deleting the note. */
async function openRecentNote(annId, nodeId, filePath) {
  if (nodeId) {
    switchView('graph');
    setTimeout(() => { focusNodeInGraph(nodeId); selectNode(nodeId); }, 80);
    return;
  }
  if (filePath) {
    showToast('Note attached to file — open it from My Files', 'info');
    return;
  }
  showToast('Note has no target', 'info');
}

/* ── Chat ──────────────────────────────────────────────────────── */
let chatHistory = [], chatAvailable = false, currentThreadId = null;
function _setChatReady() {
  chatAvailable = true;
  const el = document.getElementById('chat-status');
  el.textContent = 'connected'; el.classList.add('text-success');
  const input = document.getElementById('chat-input');
  input.disabled = false;
  if (chatTagify) chatTagify.setDisabled(false);
  document.getElementById('chat-send').disabled = false;
}
async function checkChatStatus() {
  const badge = document.getElementById('chat-active-model');
  try {
    const d = await apiFetch('/api/chat/status');
    if (badge) badge.textContent = d.model ? `${d.provider_label || d.provider}: ${d.model}` : 'no model';
    if (d.available) { _setChatReady(); return; }
    document.getElementById('chat-status').textContent = `no ${d.provider || ''} API key`.trim();
  } catch (e) {
    document.getElementById('chat-status').textContent = 'offline';
    if (badge) badge.textContent = 'offline';
  }
}
function appendChatMessage(role, content) {
  const m = document.getElementById('chat-messages');
  // Only the most-recent assistant message is regenerable. Strip footers from
  // any earlier assistant bubbles before adding the new message.
  m.querySelectorAll('.chat-footer').forEach(el => el.remove());
  const wrapper = document.createElement('div');
  wrapper.className = `chat ${role === 'user' ? 'chat-end' : 'chat-start'}`;
  const header = document.createElement('div');
  header.className = 'chat-header text-[10px]';
  header.textContent = role === 'user' ? 'You' : 'Grok';
  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role === 'user' ? 'chat-bubble-primary' : 'chat-bubble-accent'}`;
  if (role === 'assistant') {
    bubble.innerHTML = '<div class="md-content">' + marked.parse(formatChatContent(content)) + '</div>';
    bubble.querySelectorAll('pre code:not(.hljs)').forEach(b => hljs.highlightElement(b));
    _wireChipHandlers(bubble);
  } else {
    bubble.textContent = content;
  }
  wrapper.appendChild(header);
  wrapper.appendChild(bubble);
  if (role === 'assistant') {
    const footer = document.createElement('div');
    footer.className = 'chat-footer text-[10px] opacity-60 mt-0.5';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-ghost btn-xs h-5 min-h-0 px-1 gap-1 regen-btn';
    btn.title = 'Regenerate response (e.g. on timeout or bad answer)';
    btn.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3">' +
      '<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />' +
      '</svg><span>Regenerate</span>';
    btn.onclick = () => regenerateChatMessage(wrapper);
    footer.appendChild(btn);
    wrapper.appendChild(footer);
  }
  m.appendChild(wrapper);
  m.scrollTop = m.scrollHeight;
  return bubble;
}
function formatChatContent(text) {
  return text.replace(/\[\[([^\]]+)\]\]/g, (_, id) => {
    const esc = id.replace(/'/g,"\\'"), short = id.split('::').pop();
    return `<span class="node-ref text-primary" onclick="chatNodeClick('${esc}')">${short}</span>`;
  });
}
function chatNodeClick(id) { switchView('graph'); setTimeout(()=>{focusNodeInGraph(id);selectNode(id);},100); }
function chatFileClick(path) {
  // Find the file node by path and jump to it
  fetch('/api/search?q=' + encodeURIComponent(path) + '&top=5').then(r=>r.json()).then(rows => {
    const hit = (rows.results||rows||[]).find(r => r.type === 'file' && r.path === path) || (rows.results||rows||[])[0];
    if (hit && hit.id) chatNodeClick(hit.id);
  }).catch(()=>{});
}
function _wireChipHandlers(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll('.rr-chip[data-rr-node]').forEach(el => {
    el.onclick = () => chatNodeClick(el.getAttribute('data-rr-node'));
  });
  root.querySelectorAll('.rr-chip[data-rr-file]').forEach(el => {
    el.onclick = () => chatFileClick(el.getAttribute('data-rr-file'));
  });
}

// Active AbortController for the in-flight chat request, so the Send/Cancel
// button can abort it. null when no request is running.
let chatAbortController = null;

function _setSendBtnSending() {
  const b = document.getElementById('chat-send');
  b.classList.remove('btn-primary');
  b.classList.add('btn-error');
  b.textContent = 'Cancel';
  b.disabled = false;
  b.dataset.mode = 'cancel';
}

function _setSendBtnIdle() {
  const b = document.getElementById('chat-send');
  b.classList.remove('btn-error');
  b.classList.add('btn-primary');
  b.textContent = 'Send';
  b.disabled = false;
  b.dataset.mode = 'send';
}

function cancelChatRequest() {
  if (chatAbortController) {
    chatAbortController.abort();
    showToast('Request cancelled', 'warning');
  }
}

async function sendChatMessage() {
  if (!chatAvailable) return;
  // If a request is currently in flight, the button acts as Cancel.
  if (chatAbortController) { cancelChatRequest(); return; }

  const { mode, query } = parseChatMode();
  const msg = query.trim();
  if (!msg) return;

  // find: → graph search only, no AI call
  if (mode === 'find') {
    searchNodes(msg);
    return;
  }

  chatAbortController = new AbortController();
  _setSendBtnSending();
  if (chatTagify) chatTagify.setDisabled(true);
  showToast('Sending…', 'info');

  // Send the raw user message. The backend chat service (Phase 11 tool-calling)
  // exposes search_graph / get_node / get_stats tools and lets Grok decide when
  // to call them — that's strictly better than us pre-stuffing a literal search
  // of the user's natural-language question.
  clearChatInput();
  document.getElementById('search-results').classList.remove('visible');

  if (!currentThreadId) {
    try {
      const t = await apiFetch('/api/chat/threads', {
        method: 'POST',
        body: { title: msg.slice(0, 60) },
      });
      currentThreadId = t.id;
    } catch (e) {
      console.warn('Thread create failed:', e.status, e.detail);
      showToast(`Chat history disabled this session (${formatApiError(e)})`, 'error');
      // proceed without persistence — chat still works in-memory
    }
  }
  appendChatMessage('user', msg); chatHistory.push({role:'user',content:msg});
  _persistMessage('user', msg);
  const ad = appendChatMessage('assistant','...');
  try {
    const full = await _streamAssistantResponse(msg, ad, chatHistory.slice(-10), chatAbortController.signal);
    if (full !== null) {
      chatHistory.push({role:'assistant',content:full});
      _persistMessage('assistant', full);
    }
  } finally {
    chatAbortController = null;
    _setSendBtnIdle();
    if (chatTagify) { chatTagify.setDisabled(false); chatTagify.DOM.input.focus(); }
    else { const i = document.getElementById('chat-input'); i.disabled = false; i.focus(); }
  }
}

/* Stream a chat response into the given bubble. Returns the full text on
   success, or null on error (the bubble is updated with the error message).
   `historyForCall` is the prior message history sent to /api/chat. */
async function _streamAssistantResponse(msg, ad, historyForCall, signal) {
  // Animated typing indicator (daisyUI loading-dots — wave-style dots)
  // so the user can see we're actively waiting on the model.
  ad.innerHTML = '<span class="chat-typing inline-flex items-center gap-1" aria-label="Assistant is thinking">'
    + '<span class="loading loading-dots loading-sm"></span>'
    + '</span>';
  let firstChunk = true;
  const wrapper = ad.closest('.chat') || ad.parentElement;
  const scroller = document.getElementById('chat-messages');
  const scrollAssistantToTop = () => {
    if (!wrapper || !scroller) return;
    // Position the assistant bubble's top near the top of the chat viewport
    // so the user starts reading from the beginning of the reply.
    const top = wrapper.offsetTop - scroller.offsetTop;
    scroller.scrollTop = Math.max(0, top - 8);
  };
  const isAbort = (e) => signal && signal.aborted ||
    (e && (e.name === 'AbortError' || /abort/i.test(e.message || '')));
  let res;
  try {
    res = await apiFetch('/api/chat', {
      method: 'POST',
      body: { message: msg, history: historyForCall, context_node: selectedNode },
      raw: true, // need the streaming Response body for SSE
      signal,
    });
  } catch (e) {
    if (isAbort(e)) { ad.innerHTML = '<span class="opacity-60 italic">Cancelled</span>'; return null; }
    ad.innerHTML = `<span class="text-error">${formatApiError(e, 'Chat failed')}</span>`;
    // 503 typically means provider key not configured — refresh status badge.
    if (e instanceof ApiError && e.status === 503) checkChatStatus();
    return null;
  }
  try {
    const reader=res.body.getReader(), dec=new TextDecoder(); let full='';
    while(true) { const {done,value}=await reader.read(); if(done)break; for(const line of dec.decode(value,{stream:true}).split('\n')) {
      if(!line.startsWith('data: '))continue; const raw=line.slice(6);
      if(raw==='[DONE]')continue; if(raw.startsWith('[ERROR]')){ad.innerHTML=`<span class="text-error">${raw}</span>`;return null;}
      const d = raw.replace(/\\r/g,'\r').replace(/\\n/g,'\n').replace(/\\\\/g,'\\');
      full+=d; ad.innerHTML='<div class="md-content">'+marked.parse(formatChatContent(full))+'</div>';
      if (firstChunk) {
        // First content arrived — anchor the viewport at the TOP of the
        // assistant bubble so the user can read the response from line 1
        // instead of being yanked to the bottom as it grows.
        firstChunk = false;
        scrollAssistantToTop();
      }
    }}
    ad.querySelectorAll('pre code:not(.hljs)').forEach(b => hljs.highlightElement(b));
    _wireChipHandlers(ad);
    // Re-anchor after the final render (heights may have shifted due to
    // syntax highlighting / markdown layout).
    requestAnimationFrame(scrollAssistantToTop);
    return full;
  } catch(e) {
    if (isAbort(e)) {
      ad.innerHTML = ad.innerHTML + '<div class="text-xs opacity-60 italic mt-1">— cancelled —</div>';
      return null;
    }
    ad.innerHTML=`<span class="text-error">Failed: ${e.message}</span>`;
    return null;
  }
}

/* Regenerate the assistant response in the given wrapper. Finds the user
   message that prompted it (the most recent prior user message) and replays
   the request, replacing the bubble in place and updating persistence. */
async function regenerateChatMessage(wrapper) {
  if (!chatAvailable) return;
  // Find the prior user message in the DOM (the immediately preceding
  // wrapper with class chat-end).
  const m = document.getElementById('chat-messages');
  const wrappers = Array.from(m.children);
  const idx = wrappers.indexOf(wrapper);
  let userMsg = null;
  for (let i = idx - 1; i >= 0; i--) {
    if (wrappers[i].classList.contains('chat-end')) {
      userMsg = wrappers[i].querySelector('.chat-bubble').textContent;
      break;
    }
  }
  if (!userMsg) return;

  // Drop the last assistant entry from in-memory history before replaying.
  if (chatHistory.length && chatHistory[chatHistory.length - 1].role === 'assistant') {
    chatHistory.pop();
  }

  const bubble = wrapper.querySelector('.chat-bubble');
  const regenBtn = wrapper.querySelector('.regen-btn');
  if (regenBtn) regenBtn.disabled = true;
  try {
    const full = await _streamAssistantResponse(userMsg, bubble, chatHistory.slice(-10));
    if (full !== null) {
      chatHistory.push({role:'assistant',content:full});
      _replaceLastPersistedMessage('assistant', full);
    }
  } finally {
    if (regenBtn) regenBtn.disabled = false;
  }
}

function _replaceLastPersistedMessage(role, content) {
  if (!currentThreadId) return;
  apiFetch(`/api/chat/threads/${currentThreadId}/messages/last`, {
    method: 'PUT',
    body: { role, content },
  }).catch((e) => _handlePersistError(e, 'Could not save regenerated reply'));
}

/* Shared handler for chat-history persistence failures.
   - 404: thread was deleted out from under us → drop client state silently
   - network/5xx: warn the user via toast (data only lives in memory)
   - other 4xx: log + toast (likely a code bug we want to see) */
function _handlePersistError(e, prefix) {
  if (e instanceof ApiError && e.status === 404) {
    console.warn('Chat thread no longer exists; resetting.');
    currentThreadId = null;
    return;
  }
  console.warn(prefix + ':', e.status, e.detail || e.message);
  showToast(`${prefix} (${formatApiError(e)})`, 'error');
}

/* Send a chat message seeded from the Idea Cloud word click. Drops the
   text into the chat input and auto-executes (no extra Send click needed). */
function askChatFromCloud(term) {
  if (!term) return;
  const text = `Tell me about "${term}" in this codebase.`;
  switchView('graph');                       // ensure chat panel is visible
  if (chatTagify) {
    chatTagify.removeAllTags();
    chatTagify.DOM.input.textContent = text;
  } else {
    const input = document.getElementById('chat-input');
    input.value = text;
  }
  // Defer one tick so Tagify's input event finishes wiring before we send.
  if (chatAvailable) setTimeout(() => sendChatMessage(), 0);
  else showToast('Chat not available — set an API key in Settings', 'error');
}

function askAboutNode(nodeId) {
  if (!chatAvailable) return; switchView('graph');
  const text = `What does ${nodeId.split('::').pop()} do and where is it used?`;
  if (chatTagify) {
    chatTagify.removeAllTags();
    chatTagify.DOM.input.textContent = text;
    chatTagify.DOM.input.focus();
  } else {
    const input = document.getElementById('chat-input');
    input.value = text; input.focus();
  }
}

/* ── Chat History ──────────────────────────────────────────────── */
async function newChatThread() {
  try {
    const thread = await fetch('/api/chat/threads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }).then(r => r.json());
    currentThreadId = thread.id;
    chatHistory = [];
    document.getElementById('chat-messages').innerHTML = '';
    document.getElementById('chat-thread-list').classList.add('hidden');
  } catch (e) { console.error('Failed to create thread:', e); }
}

async function toggleChatHistory() {
  const panel = document.getElementById('chat-thread-list');
  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    return;
  }
  panel.innerHTML = '<div class="p-2 text-xs opacity-50">Loading...</div>';
  panel.classList.remove('hidden');
  try {
    const threads = await apiFetch('/api/chat/threads');
    if (!threads.length) {
      panel.innerHTML = '<div class="p-2 text-xs opacity-50">No saved chats</div>';
      return;
    }
    let html = '';
    threads.forEach(t => {
      const date = t.updated_at ? new Date(t.updated_at).toLocaleDateString() : '';
      const count = t.message_count || 0;
      html += `<div class="flex items-center justify-between px-2 py-1.5 hover:bg-base-200 cursor-pointer group" onclick="loadChatThread('${t.id}')">
        <div class="flex-1 min-w-0">
          <div class="text-xs truncate">${t.title || 'Untitled'}</div>
          <div class="text-[10px] opacity-40">${date} · ${count} msgs</div>
        </div>
        <button type="button" class="btn btn-ghost btn-xs btn-square opacity-0 group-hover:opacity-100" onclick="event.stopPropagation();deleteChatThread('${t.id}')" title="Delete">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>`;
    });
    panel.innerHTML = html;
  } catch (e) { panel.innerHTML = '<div class="p-2 text-xs text-error">Failed to load</div>'; }
}

async function loadChatThread(threadId) {
  try {
    const thread = await apiFetch(`/api/chat/threads/${threadId}`);
    currentThreadId = threadId;
    chatHistory = [];
    const msgEl = document.getElementById('chat-messages');
    msgEl.innerHTML = '';
    (thread.messages || []).forEach(m => {
      appendChatMessage(m.role, m.content);
      chatHistory.push({ role: m.role, content: m.content });
    });
    document.getElementById('chat-thread-list').classList.add('hidden');
  } catch (e) { showToast('Failed to load thread', 'error'); }
}

async function deleteChatThread(threadId) {
  try {
    await fetch(`/api/chat/threads/${threadId}`, { method: 'DELETE' });
    if (currentThreadId === threadId) {
      currentThreadId = null;
      chatHistory = [];
      document.getElementById('chat-messages').innerHTML = '';
    }
    toggleChatHistory();
  } catch (e) { showToast('Failed to delete thread', 'error'); }
}

function _persistMessage(role, content) {
  if (!currentThreadId) return;
  apiFetch(`/api/chat/threads/${currentThreadId}/messages`, {
    method: 'POST',
    body: { role, content },
  }).catch((e) => _handlePersistError(e, 'Could not save message'));
}

/* ── Image Generation ──────────────────────────────────────────── */
async function generateImage() {
  const prompt = document.getElementById('image-prompt').value.trim();
  if (!prompt) return;
  const model = document.getElementById('image-model').value;
  const btn = document.getElementById('image-generate-btn');
  const results = document.getElementById('image-results');
  btn.disabled = true; btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Generating...';

  // Add prompt display
  const promptDiv = document.createElement('div');
  promptDiv.className = 'w-full max-w-lg text-xs opacity-60 mb-1';
  promptDiv.textContent = 'Prompt: ' + prompt;
  results.appendChild(promptDiv);

  try {
    const res = await fetch('/api/image/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, model }),
    });
    if (!res.ok) { const e = await res.json().catch(()=>({})); showToast(e.detail||'Image generation failed','error'); return; }
    const data = await res.json();
    (data.images||[]).forEach(b64 => {
      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + b64;
      img.className = 'gen-image max-w-lg';
      results.appendChild(img);
    });
    document.getElementById('image-prompt').value = '';
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = 'Generate'; }
}

/* ── Settings ──────────────────────────────────────────────────── */
let _providerRegistry = [];
let _settingsCache = null;

function _esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function renderProvidersUI(d) {
  _settingsCache = d;
  _providerRegistry = d.providers || [];
  const active = d.chat?.active_provider || (_providerRegistry[0] && _providerRegistry[0].id);
  const perProvider = d.chat?.providers || {};

  // Active-provider selector
  const sel = document.getElementById('active-provider');
  sel.innerHTML = _providerRegistry.map(p =>
    `<option value="${_esc(p.id)}"${p.id===active?' selected':''}>${_esc(p.label)}</option>`
  ).join('');

  // Preserve any unsaved typing across re-renders (e.g. when toggling
  // active provider) by snapshotting current input values first.
  const _typed = {};
  const list = document.getElementById('providers-list');
  if (list) {
    _providerRegistry.forEach(p => {
      const k = document.getElementById(`pk-${p.id}`);
      if (k && k.value && !k.value.includes('•')) _typed[p.id] = k.value;
    });
  }

  // Per-provider cards (DaisyUI card component — card-body / card-title / card-actions)
  list.innerHTML = _providerRegistry.map(p => {
    const masked = d.api_keys?.[p.id] || '';
    const inputId = `pk-${p.id}`;
    const modelId = `pm-${p.id}`;
    const selectedModel = perProvider[p.id]?.model || p.default_model;
    const opts = p.models.map(m => `<option value="${_esc(m)}"${m===selectedModel?' selected':''}>${_esc(m)}</option>`).join('');
    const isActive = p.id === active;
    const status = masked
      ? '<span class="badge badge-success badge-sm">key set</span>'
      : '<span class="badge badge-ghost badge-sm">no key</span>';
    const activeBadge = isActive ? '<span class="badge badge-primary badge-sm">active</span>' : '';
    const cardCls = isActive
      ? 'card bg-base-200 border-2 border-primary shadow-md'
      : 'card bg-base-200 border border-base-300 shadow-sm';
    return `
      <div class="${cardCls}" data-provider="${_esc(p.id)}">
        <div class="card-body p-5 gap-3">
          <div class="flex items-start justify-between gap-2">
            <h2 class="card-title text-base">${_esc(p.label)}</h2>
            <div class="flex items-center gap-2">
              ${activeBadge}
              ${status}
            </div>
          </div>
          <p class="text-xs opacity-50 -mt-1">
            <a href="${_esc(p.key_url)}" target="_blank" class="link link-primary">Get key ↗</a>
            <span class="opacity-60">· stored in env <code class="font-mono">${_esc(p.env)}</code></span>
          </p>

          <div class="form-control">
            <label class="label py-1"><span class="label-text text-xs font-medium">API Key</span></label>
            <div class="flex gap-2">
              <input id="${inputId}" type="password" placeholder="${_esc(masked || p.key_placeholder)}" class="input input-sm input-bordered flex-1 font-mono" autocomplete="off" />
              <button type="button" class="btn btn-sm btn-ghost btn-square" onclick="togglePasswordVisibility('${inputId}')" aria-label="Toggle key visibility">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4"><path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
              </button>
            </div>
          </div>

          <div class="form-control">
            <label class="label py-1"><span class="label-text text-xs font-medium">Model</span></label>
            <select id="${modelId}" class="select select-sm select-bordered w-full">${opts}</select>
          </div>

          <div class="card-actions justify-end mt-1">
            <button type="button" class="btn btn-xs ${isActive ? 'btn-primary' : 'btn-ghost'}" onclick="setActiveProvider('${_esc(p.id)}')" ${isActive ? 'disabled' : ''}>
              ${isActive ? 'Active' : 'Set active'}
            </button>
          </div>
        </div>
      </div>`;
  }).join('');

  // Restore any in-flight key typing that the user hadn't saved yet.
  Object.entries(_typed).forEach(([pid, v]) => {
    const el = document.getElementById(`pk-${pid}`);
    if (el) el.value = v;
  });
}

function setActiveProvider(pid) {
  const sel = document.getElementById('active-provider');
  if (!sel) return;
  sel.value = pid;
  // Re-render cards from the cached payload so the active border/button
  // updates immediately. Persisted on Save Settings.
  if (_settingsCache) {
    _settingsCache = { ..._settingsCache, chat: { ...(_settingsCache.chat || {}), active_provider: pid } };
    renderProvidersUI(_settingsCache);
  }
}

function _setVal(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!val;
  else el.value = val == null ? '' : val;
}

function _getVal(id) {
  const el = document.getElementById(id);
  if (!el) return undefined;
  return el.type === 'checkbox' ? el.checked : el.value;
}

function _csv(s) {
  return String(s || '').split(',').map(x => x.trim()).filter(Boolean);
}

function renderExtraSettings(d) {
  const chat = d.chat || {};
  _setVal('chat-max-tool-rounds', chat.max_tool_rounds ?? 5);
  _setVal('chat-streaming', chat.streaming !== false);

  const appearance = d.appearance || {};
  _setVal('appearance-theme', appearance.theme || 'dark');

  const g = d.graph || {};
  _setVal('graph-default-depth', g.default_depth ?? 20);
  _setVal('graph-edge-cap', g.edge_cap_multiplier ?? 3);
  _setVal('graph-anim-threshold', g.animation_threshold ?? 500);

  const idx = d.indexing || {};
  _setVal('indexing-exclude-globs', (idx.exclude_globs || []).join(', '));
  _setVal('indexing-extra-skip-dirs', (idx.extra_skip_dirs || []).join(', '));
  _setVal('indexing-embed-batch', idx.embedding_batch_size ?? 256);
  _setVal('indexing-embed-min', idx.embedding_min_text_length ?? 40);

  const r = d.reindex || {};
  _setVal('reindex-strategy', r.strategy || 'auto');
  _setVal('reindex-interval', r.sweep_interval_minutes ?? 30);
  _setVal('reindex-max-hops', r.local_max_hops ?? 1);
  _setVal('reindex-force-full', r.force_full_after_runs ?? 50);
  _setVal('reindex-on-start', r.sweep_on_session_start !== false);

  const c = d.captures || {};
  _setVal('captures-folder', c.folder || '_apollo_web');

  const lg = d.logging || {};
  _setVal('logging-path', lg.path || '');
  _setVal('logging-level', lg.level || '');
  _setVal('logging-json', !!lg.json_mode);
  _setVal('logging-max-size-mb', lg.max_size_mb ?? 100);
  _setVal('logging-max-age-days', lg.max_age_days ?? 7);
  _setVal('logging-rotated-total-mb', lg.rotated_total_mb ?? 1024);

  renderPluginsList(d.plugins || {});
}

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Cache the most recent /api/settings plugins payload so the inline
// PATCH handlers below can read schemas / current values without
// having to re-fetch the whole settings document for every keystroke.
let _pluginsCache = {};

function renderPluginsList(plugins) {
  const host = document.getElementById('plugins-list');
  if (!host) return;
  _pluginsCache = plugins || {};
  const names = Object.keys(_pluginsCache).sort();
  if (!names.length) {
    host.innerHTML = '<span class="opacity-50">No plugins detected under <code class="font-mono">plugins/</code>.</span>';
    return;
  }
  const cards = names.map(name => _renderPluginCard(name, _pluginsCache[name] || {})).join('');
  host.innerHTML = cards;
  // Wire interactive controls after the DOM is in place.
  names.forEach(name => _bindPluginCard(name));
}

/* Build the markup for a single plugin card. */
function _renderPluginCard(name, p) {
  const installed = !!p.installed;
  const schema = (p && p.config_schema) || {};
  const merged = (p && p.config) || {};
  const hasConfig = Object.keys(schema).length > 0;
  const enabled = hasConfig ? (merged.enabled !== false) : true;

  const statusBadge = installed
    ? '<span class="badge badge-success badge-sm">installed</span>'
    : '<span class="badge badge-ghost badge-sm">unavailable</span>';
  const enabledBadge = hasConfig
    ? (enabled
        ? '<span class="badge badge-info badge-sm" data-role="enabled-badge">enabled</span>'
        : '<span class="badge badge-warning badge-sm" data-role="enabled-badge">disabled</span>')
    : '';
  const desc = p.description
    ? `<p class="text-xs opacity-70 mt-1">${_esc(p.description)}</p>`
    : '<p class="text-xs opacity-40 italic mt-1">No description (missing or invalid plugin.md)</p>';
  const version = p.version
    ? `<span class="badge badge-outline badge-sm font-mono cursor-help" title="SHA-256(parser.py): ${_esc(p.sha256) || '(unavailable)'}">v${_esc(p.version)}</span>`
    : '<span class="badge badge-ghost badge-sm">no version</span>';
  const url = p.url
    ? `<a href="${_esc(p.url)}" target="_blank" rel="noopener" class="link link-primary text-xs break-all">${_esc(p.url)}</a>`
    : '<span class="opacity-40 text-xs italic">no url</span>';
  const author = p.author
    ? `<span class="text-xs">${_esc(p.author)}</span>`
    : '<span class="opacity-40 text-xs italic">unknown</span>';

  const enableToggle = hasConfig ? `
        <label class="cursor-pointer flex items-center gap-2 ml-auto" title="${_esc(_pluginFieldDoc(schema, 'enabled') || 'Enable / disable this plugin')}">
          <span class="text-xs opacity-60">enabled</span>
          <input type="checkbox" class="toggle toggle-sm toggle-success" data-role="plugin-enabled" ${enabled ? 'checked' : ''} />
        </label>` : '';

  const settingsPanel = hasConfig
    ? _renderPluginSettingsPanel(name, schema, merged)
    : '';

  return `
    <div class="border border-base-300 rounded-lg p-3 mb-3" data-plugin-name="${_esc(name)}">
      <div class="flex items-center gap-2 flex-wrap">
        <code class="font-mono text-sm font-semibold">${_esc(name)}</code>
        ${version}
        ${statusBadge}
        ${enabledBadge}
        ${enableToggle}
      </div>
      ${desc}
      <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 mt-2 text-xs">
        <span class="opacity-50">Author:</span>${author}
        <span class="opacity-50">URL:</span>${url}
      </div>
      ${settingsPanel}
    </div>`;
}

/* Look up a `_<key>` description sibling on the on-disk schema. */
function _pluginFieldDoc(schema, key) {
  const v = schema['_' + key];
  return typeof v === 'string' ? v : '';
}

/* Build the collapsible "Settings" panel for one plugin. Inputs are
   typed off the on-disk schema's value (so a JSON `bool` always renders
   as a checkbox even if the user-saved override happens to be the
   wrong type). Read-only types render a <pre> JSON view. */
function _renderPluginSettingsPanel(name, schema, merged) {
  // Collect runtime keys (skip `_<key>` description siblings, and skip
  // `enabled` because it has its own dedicated toggle in the header).
  const keys = Object.keys(schema)
    .filter(k => !k.startsWith('_') && k !== 'enabled')
    .sort();
  if (!keys.length) {
    return `
      <details class="mt-3">
        <summary class="cursor-pointer text-xs opacity-70 select-none">Settings</summary>
        <div class="mt-2 text-xs opacity-50 italic">This plugin has no editable settings.</div>
      </details>`;
  }
  const rows = keys.map(k => _renderPluginField(name, k, schema, merged)).join('');
  return `
    <details class="mt-3 plugin-settings-panel">
      <summary class="cursor-pointer text-xs opacity-70 select-none">Settings</summary>
      <div class="mt-2 grid grid-cols-1 gap-3 text-xs">
        ${rows}
      </div>
      <div class="flex justify-end gap-2 mt-3">
        <button type="button" class="btn btn-xs btn-ghost" data-role="plugin-reset">Reset to defaults</button>
        <button type="button" class="btn btn-xs btn-primary" data-role="plugin-save">Save</button>
      </div>
    </details>`;
}

/* Render a single field row. `key` is the runtime knob; the schema's
   value at `key` decides which control we draw. */
function _renderPluginField(name, key, schema, merged) {
  const onDisk = schema[key];
  const current = (merged && key in merged) ? merged[key] : onDisk;
  const doc = _pluginFieldDoc(schema, key);
  const labelHtml = `
    <div class="flex items-center gap-2">
      <code class="font-mono text-xs opacity-80">${_esc(key)}</code>
      ${doc ? `<span class="opacity-50 text-[11px]">— ${_esc(doc)}</span>` : ''}
    </div>`;

  const dataAttr = `data-plugin-field="${_esc(key)}"`;

  if (typeof onDisk === 'boolean') {
    return `
      <label class="flex items-start gap-3 cursor-pointer">
        <input type="checkbox" class="checkbox checkbox-sm mt-0.5" ${dataAttr} data-plugin-type="bool" ${current ? 'checked' : ''} />
        <div class="flex-1">${labelHtml}</div>
      </label>`;
  }
  if (typeof onDisk === 'number' && Number.isInteger(onDisk)) {
    return `
      <div class="flex flex-col gap-1">
        ${labelHtml}
        <input type="number" step="1" class="input input-bordered input-xs w-48 font-mono" ${dataAttr} data-plugin-type="int" value="${_esc(current)}" />
      </div>`;
  }
  if (typeof onDisk === 'number') {
    return `
      <div class="flex flex-col gap-1">
        ${labelHtml}
        <input type="number" step="any" class="input input-bordered input-xs w-48 font-mono" ${dataAttr} data-plugin-type="float" value="${_esc(current)}" />
      </div>`;
  }
  if (typeof onDisk === 'string') {
    return `
      <div class="flex flex-col gap-1">
        ${labelHtml}
        <input type="text" class="input input-bordered input-xs w-full font-mono" ${dataAttr} data-plugin-type="string" value="${_esc(current)}" />
      </div>`;
  }
  if (Array.isArray(onDisk)) {
    const allStrings = onDisk.every(x => typeof x === 'string');
    if (allStrings) {
      const tags = Array.isArray(current) ? current : [];
      return `
        <div class="flex flex-col gap-1">
          ${labelHtml}
          <input type="text" class="input input-bordered input-xs w-full font-mono" ${dataAttr} data-plugin-type="list[str]" value="${_esc(tags.join(', '))}" placeholder="comma-separated values" />
        </div>`;
    }
    // Non-string list → read-only JSON.
    return `
      <div class="flex flex-col gap-1">
        ${labelHtml}
        <pre class="bg-base-200 rounded p-2 text-[11px] overflow-auto max-h-40 m-0" ${dataAttr} data-plugin-type="json-readonly">${_esc(JSON.stringify(current, null, 2))}</pre>
        <span class="opacity-50 text-[11px] italic">Read-only in v1 (edit <code>config.json</code> directly).</span>
      </div>`;
  }
  if (onDisk && typeof onDisk === 'object') {
    return `
      <div class="flex flex-col gap-1">
        ${labelHtml}
        <pre class="bg-base-200 rounded p-2 text-[11px] overflow-auto max-h-40 m-0" ${dataAttr} data-plugin-type="json-readonly">${_esc(JSON.stringify(current, null, 2))}</pre>
        <span class="opacity-50 text-[11px] italic">Read-only in v1 (edit <code>config.json</code> directly).</span>
      </div>`;
  }
  // Fallback: null / unsupported → show JSON preview.
  return `
    <div class="flex flex-col gap-1">
      ${labelHtml}
      <pre class="bg-base-200 rounded p-2 text-[11px] overflow-auto max-h-40 m-0" ${dataAttr} data-plugin-type="json-readonly">${_esc(JSON.stringify(current, null, 2))}</pre>
    </div>`;
}

/* Wire toggle + Save + Reset for a single plugin card. */
function _bindPluginCard(name) {
  const card = document.querySelector(`[data-plugin-name="${CSS.escape(name)}"]`);
  if (!card) return;

  const toggle = card.querySelector('[data-role="plugin-enabled"]');
  if (toggle && !toggle.dataset.bound) {
    toggle.dataset.bound = '1';
    toggle.addEventListener('change', async () => {
      const newVal = !!toggle.checked;
      const ok = await _patchPluginConfig(name, { enabled: newVal });
      if (!ok) {
        // revert UI on failure
        toggle.checked = !newVal;
      } else {
        showToast(`Plugin ${name}: ${newVal ? 'enabled' : 'disabled'}`, 'success');
        // Refresh so badges + computed merged values stay in sync.
        await loadSettings();
      }
    });
  }

  const saveBtn = card.querySelector('[data-role="plugin-save"]');
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = '1';
    saveBtn.addEventListener('click', async () => {
      const diff = _collectPluginDiff(card, name);
      if (diff === null) return; // collection error (toast already shown)
      if (Object.keys(diff).length === 0) {
        showToast('No changes to save', 'info');
        return;
      }
      const ok = await _patchPluginConfig(name, diff);
      if (ok) {
        showToast(`Plugin ${name}: saved`, 'success');
        await loadSettings();
      }
    });
  }

  const resetBtn = card.querySelector('[data-role="plugin-reset"]');
  if (resetBtn && !resetBtn.dataset.bound) {
    resetBtn.dataset.bound = '1';
    resetBtn.addEventListener('click', async () => {
      const schema = (_pluginsCache[name] || {}).config_schema || {};
      // Build a payload of every editable runtime knob (skip `_<key>`
      // description siblings) set back to its on-disk default. We
      // include `enabled` so disabled→enabled also resets via the
      // same call.
      const defaults = {};
      Object.keys(schema).forEach(k => {
        if (k.startsWith('_')) return;
        const v = schema[k];
        // Skip dict / non-string list — they're read-only in v1, but
        // sending the on-disk value back is still safe and keeps the
        // override store clean.
        defaults[k] = v;
      });
      const ok = await _patchPluginConfig(name, defaults);
      if (ok) {
        showToast(`Plugin ${name}: reset to defaults`, 'success');
        await loadSettings();
      }
    });
  }
}

/* Walk the form controls inside a card and collect a partial diff
   against the currently-loaded merged values. Returns `null` on a
   parse error (and toasts the user). */
function _collectPluginDiff(card, name) {
  const schema = (_pluginsCache[name] || {}).config_schema || {};
  const merged = (_pluginsCache[name] || {}).config || {};
  const diff = {};
  const fields = card.querySelectorAll('[data-plugin-field]');
  for (const el of fields) {
    const key = el.getAttribute('data-plugin-field');
    const type = el.getAttribute('data-plugin-type');
    if (type === 'json-readonly') continue;
    let value;
    try {
      if (type === 'bool') {
        value = !!el.checked;
      } else if (type === 'int') {
        const raw = el.value.trim();
        if (raw === '') continue; // skip empty -> treat as no change
        value = parseInt(raw, 10);
        if (Number.isNaN(value)) throw new Error(`"${key}" must be an integer`);
      } else if (type === 'float') {
        const raw = el.value.trim();
        if (raw === '') continue;
        value = parseFloat(raw);
        if (Number.isNaN(value)) throw new Error(`"${key}" must be a number`);
      } else if (type === 'string') {
        value = el.value;
      } else if (type === 'list[str]') {
        value = el.value
          .split(',')
          .map(s => s.trim())
          .filter(s => s.length > 0);
      } else {
        continue;
      }
    } catch (e) {
      showToast(e.message || String(e), 'error');
      return null;
    }
    const current = (key in merged) ? merged[key] : schema[key];
    if (!_pluginValuesEqual(current, value)) {
      diff[key] = value;
    }
  }
  return diff;
}

function _pluginValuesEqual(a, b) {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) return false;
    }
    return true;
  }
  return false;
}

/* PATCH /api/settings/plugins/<name>/config with a body, surfacing
   server validation errors via a toast. Returns true on success. */
async function _patchPluginConfig(name, body) {
  try {
    await apiFetch(`/api/settings/plugins/${encodeURIComponent(name)}/config`, {
      method: 'PATCH',
      body,
    });
    return true;
  } catch (e) {
    showToast(`Plugin ${name}: ${e.message || e}`, 'error');
    return false;
  }
}

function collectExtraSettings() {
  return {
    chat: {
      max_tool_rounds: parseInt(_getVal('chat-max-tool-rounds'), 10) || 5,
      streaming: !!_getVal('chat-streaming'),
    },
    appearance: { theme: _getVal('appearance-theme') || 'dark' },
    graph: {
      default_depth: parseInt(_getVal('graph-default-depth'), 10) || 20,
      edge_cap_multiplier: parseInt(_getVal('graph-edge-cap'), 10) || 3,
      animation_threshold: parseInt(_getVal('graph-anim-threshold'), 10) || 500,
    },
    indexing: {
      exclude_globs: _csv(_getVal('indexing-exclude-globs')),
      extra_skip_dirs: _csv(_getVal('indexing-extra-skip-dirs')),
      embedding_batch_size: parseInt(_getVal('indexing-embed-batch'), 10) || 256,
      embedding_min_text_length: parseInt(_getVal('indexing-embed-min'), 10) || 40,
    },
    reindex: {
      strategy: _getVal('reindex-strategy') || 'auto',
      sweep_interval_minutes: parseInt(_getVal('reindex-interval'), 10) || 30,
      sweep_on_session_start: !!_getVal('reindex-on-start'),
      local_max_hops: parseInt(_getVal('reindex-max-hops'), 10) || 1,
      force_full_after_runs: parseInt(_getVal('reindex-force-full'), 10) || 50,
    },
    captures: { folder: _getVal('captures-folder') || '_apollo_web' },
    logging: {
      path: _getVal('logging-path') || '',
      level: (_getVal('logging-level') || '').toUpperCase(),
      json_mode: !!_getVal('logging-json'),
      max_size_mb: parseInt(_getVal('logging-max-size-mb'), 10) || 100,
      max_age_days: parseInt(_getVal('logging-max-age-days'), 10) || 7,
      rotated_total_mb: parseInt(_getVal('logging-rotated-total-mb'), 10) || 1024,
    },
  };
}

function switchSettingsTab(tabId) {
  document.querySelectorAll('#settings-tablist .tab').forEach(t => {
    t.classList.toggle('tab-active', t.dataset.tab === tabId);
  });
  document.querySelectorAll('.settings-tab-panel').forEach(p => {
    p.classList.toggle('hidden', p.dataset.panel !== tabId);
  });
}

function initSettingsTabs() {
  const list = document.getElementById('settings-tablist');
  if (!list || list.dataset.bound === '1') return;
  list.dataset.bound = '1';
  list.addEventListener('click', e => {
    const t = e.target.closest('.tab');
    if (t && t.dataset.tab) switchSettingsTab(t.dataset.tab);
  });
}

async function loadSettings() {
  try {
    initSettingsTabs();
    const d = await apiFetch('/api/settings');
    renderProvidersUI(d);
    renderExtraSettings(d);
    // NOTE: Don't force the saved theme onto the live DOM here. The user
    // may have toggled light/dark via the sidebar button; opening the
    // Settings view shouldn't override that choice. Theme is applied on
    // explicit save (see saveSettings) and on initial page load.
  } catch (e) {
    console.error('Settings API not available:', e);
  }
  loadLoggingInfo();
}

function _formatBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function _formatTime(epoch) {
  if (epoch == null) return '—';
  try {
    return new Date(epoch * 1000).toLocaleString();
  } catch (_) { return '—'; }
}

async function loadLoggingInfo() {
  const infoEl = document.getElementById('logging-info-panel');
  const settingsEl = document.getElementById('logging-settings-panel');
  if (!infoEl || !settingsEl) return;
  try {
    const d = await apiFetch('/api/logging/info');

    // ── Log files panel ──
    if (!d.enabled) {
      infoEl.innerHTML = `
        <div class="alert alert-warning text-xs">
          File logging is <strong>disabled</strong>. Set
          <code class="font-mono">APOLLO_LOG_FILE</code> to a path (or unset it
          to use the default <code class="font-mono">${d.default_path}</code>)
          and restart the server to enable.
        </div>`;
    } else {
      const a = d.active;
      const activeRow = a
        ? (a.error
            ? `<tr><td class="opacity-60">Active</td><td colspan="2" class="text-error font-mono">${a.path} — ${a.error}</td></tr>`
            : `<tr><td class="opacity-60">Active</td>
                  <td class="font-mono break-all">${a.path}</td>
                  <td class="text-right whitespace-nowrap">${_formatBytes(a.size_bytes)}</td>
                </tr>
                <tr><td class="opacity-60">Modified</td>
                  <td colspan="2" class="font-mono opacity-70">${_formatTime(a.modified)}</td>
                </tr>`)
        : `<tr><td class="opacity-60">Active</td>
              <td colspan="2" class="font-mono opacity-50">${d.path} (no events written yet)</td>
            </tr>`;

      const rotatedRows = (d.rotated || []).length === 0
        ? `<tr><td colspan="3" class="opacity-50 text-center py-2">No rotated log files yet.</td></tr>`
        : (d.rotated || []).map(r => `
            <tr>
              <td class="font-mono break-all opacity-80">${r.name}</td>
              <td class="font-mono opacity-60 whitespace-nowrap">${_formatTime(r.modified)}</td>
              <td class="text-right whitespace-nowrap">${_formatBytes(r.size_bytes)}</td>
            </tr>`).join('');

      const totalRotated = `${(d.rotated || []).length} file(s), ${_formatBytes(d.rotated_total_bytes)} total`;

      infoEl.innerHTML = `
        <table class="table table-xs w-full mb-3">
          <tbody>
            <tr><td class="opacity-60 w-32">Directory</td>
              <td colspan="2" class="font-mono break-all">${d.directory || '—'}</td>
            </tr>
            ${activeRow}
          </tbody>
        </table>

        <h4 class="text-xs font-semibold opacity-70 mt-4 mb-2">Rotated files <span class="opacity-50 font-normal">(${totalRotated})</span></h4>
        <div class="overflow-x-auto rounded border border-base-300">
          <table class="table table-xs w-full">
            <thead class="opacity-60">
              <tr><th>File</th><th>Modified</th><th class="text-right">Size</th></tr>
            </thead>
            <tbody>${rotatedRows}</tbody>
          </table>
        </div>`;
    }

    // ── Settings panel ──
    const s = d.settings || {};
    settingsEl.innerHTML = `
      <table class="table table-xs w-full">
        <tbody>
          <tr><td class="opacity-60 w-56">Log level</td>
            <td><span class="badge badge-sm">${d.level}</span>
              <span class="opacity-50 ml-2 font-mono">APOLLO_LOG_LEVEL</span></td>
          </tr>
          <tr><td class="opacity-60">JSON formatter</td>
            <td>${d.json_mode ? '<span class="badge badge-sm badge-primary">on</span>' : '<span class="badge badge-sm badge-ghost">off</span>'}
              <span class="opacity-50 ml-2 font-mono">APOLLO_LOG_JSON</span></td>
          </tr>
          <tr><td class="opacity-60">Max file size before rollover</td>
            <td>${s.max_size_mb} MB
              <span class="opacity-50 ml-2 font-mono">APOLLO_LOG_MAX_SIZE_MB</span></td>
          </tr>
          <tr><td class="opacity-60">Retention age (rotated files)</td>
            <td>${s.max_age_days} days
              <span class="opacity-50 ml-2 font-mono">APOLLO_LOG_MAX_AGE_DAYS</span></td>
          </tr>
          <tr><td class="opacity-60">Total rotated-file budget</td>
            <td>${s.rotated_total_mb} MB
              <span class="opacity-50 ml-2 font-mono">APOLLO_LOG_ROTATED_TOTAL_MB</span></td>
          </tr>
        </tbody>
      </table>`;
  } catch (e) {
    infoEl.innerHTML = `<div class="alert alert-error text-xs">Failed to load logging info: ${e}</div>`;
    settingsEl.innerHTML = '';
    console.error('Logging info API not available:', e);
  }
}

// Wire up the refresh button once the DOM is ready.
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('logging-refresh-btn');
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = '1';
    btn.addEventListener('click', loadLoggingInfo);
  }
});

async function saveSettings() {
  const btn = document.getElementById('save-settings-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Saving...';
  try {
    const apiKeys = {};
    const providers = {};
    for (const p of _providerRegistry) {
      const k = (document.getElementById(`pk-${p.id}`) || {}).value || '';
      if (k && !k.includes('•')) apiKeys[p.id] = k;
      const m = (document.getElementById(`pm-${p.id}`) || {}).value || '';
      if (m) providers[p.id] = { model: m };
    }
    const active = document.getElementById('active-provider').value;
    const extra = collectExtraSettings();
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_keys: apiKeys,
        chat: { active_provider: active, providers, ...extra.chat },
        appearance: extra.appearance,
        graph: extra.graph,
        indexing: extra.indexing,
        reindex: extra.reindex,
        captures: extra.captures,
        logging: extra.logging,
      }),
    });
    if (res.ok) {
      showToast('Settings saved', 'success');
      // Apply theme immediately so user sees the change.
      if (extra.appearance?.theme) {
        document.documentElement.setAttribute('data-theme', extra.appearance.theme);
      }
      await loadSettings();   // refresh masked keys / status badges
      checkChatStatus();
    } else {
      showToast('Failed to save', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Settings';
  }
}

function showToast(msg,type) {
  const cls = {
    success: 'bg-success text-success-content',
    info:    'bg-info text-info-content',
    warning: 'bg-warning text-warning-content',
    error:   'bg-error text-error-content',
  }[type] || 'bg-error text-error-content';
  const t=document.createElement('div'); t.className=`toast-msg ${cls}`;
  t.textContent=msg; document.body.appendChild(t); requestAnimationFrame(()=>t.classList.add('show'));
  setTimeout(()=>{t.classList.remove('show');setTimeout(()=>t.remove(),300);},3000);
}
function togglePasswordVisibility(id) { const i=document.getElementById(id); i.type=i.type==='password'?'text':'password'; }

/* ── Events ────────────────────────────────────────────────────── */
document.addEventListener('click', e => {
  const chatPane = document.getElementById('left-vsplit-bottom');
  if (!chatPane?.contains(e.target)) document.getElementById('search-results').classList.remove('visible');
});
(function initDepthSlider() {
  const slider = document.getElementById('depth-slider');
  slider.max = DEPTH_STOPS.length;
  let depthTimer = null;
  slider.addEventListener('input', () => {
    updateDepthLabel();
    clearTimeout(depthTimer);
    depthTimer = setTimeout(loadGraph, 400);
  });
  updateDepthLabel();
})();
window.addEventListener('resize', () => { if(graphChart)graphChart.resize(); if(graphChart2)graphChart2.resize(); if(wordCloudChart)wordCloudChart.resize(); });
/* ── Tagify Mode Badge on Chat Input ───────────────────────────── */
let chatTagify = null;
let _chatSearchDebounce = null;
const CHAT_MODES = [
  { value: 'find:', mode: 'find' },
  { value: 'chat:', mode: 'chat' },
];
(function initChatTagify() {
  const input = document.getElementById('chat-input');
  if (!input || typeof Tagify === 'undefined') return;
  chatTagify = new Tagify(input, {
    mode: 'mix',
    pattern: /^/,
    enforceWhitelist: true,
    whitelist: CHAT_MODES,
    dropdown: {
      enabled: 0,           // show on focus
      maxItems: 5,
      position: 'input',
      closeOnSelect: true,
      highlightFirst: true,
    },
    tagTextProp: 'value',
    duplicates: false,
    transformTag: tagData => { tagData.class = 'tagify-mode-' + tagData.mode; },
    templates: {
      tag(tagData) {
        return `<tag title="${tagData.value}" contenteditable="false" spellcheck="false" class="tagify__tag" data-mode="${tagData.mode}">
          <x title="" class="tagify__tag__removeBtn" role="button" aria-label="remove tag"></x>
          <div><span class="tagify__tag-text">${tagData.value}</span></div>
        </tag>`;
      },
    },
  });

  // Pre-populate both badges so the modes are visible by default.
  // User can remove either to narrow the intent (or both for default RAG behavior).
  seedDefaultBadges();

  // Auto-convert typed "find:" or "chat:" prefix into a real tag badge
  // (only fires when the corresponding badge has been removed by the user)
  chatTagify.on('input', e => {
    const existingModes = (chatTagify.value || []).map(t => t.mode);
    const txtEl = chatTagify.DOM.input;
    // Get only text nodes outside of <tag> elements
    let typed = '';
    txtEl.childNodes.forEach(n => {
      if (n.nodeType === Node.TEXT_NODE) typed += n.textContent;
    });
    // Strip ALL leading "find:" / "chat:" prefixes the user typed (handles
    // repeats like "find: find: chat:") and add any missing badges once.
    let stripped = false;
    const seen = new Set(existingModes);
    while (true) {
      const m = typed.match(/^\s*(find|chat):\s?/i);
      if (!m) break;
      const mode = m[1].toLowerCase();
      typed = typed.slice(m[0].length);
      stripped = true;
      if (!seen.has(mode)) {
        chatTagify.addTags([{ value: mode + ':', mode }], false, true);
        seen.add(mode);
      }
    }
    if (stripped) {
      // Rewrite text nodes: clear all, then put the remaining typed text into
      // a single text node at the end.
      Array.from(txtEl.childNodes).forEach(n => { if (n.nodeType === Node.TEXT_NODE) n.remove(); });
      if (typed.length) txtEl.appendChild(document.createTextNode(typed));
      // Caret to end
      const range = document.createRange(); const sel = window.getSelection();
      range.selectNodeContents(txtEl); range.collapse(false);
      sel.removeAllRanges(); sel.addRange(range);
    }

    // Live graph search ONLY when the user is in pure 'find:' mode.
    // Natural-language chat queries shouldn't trigger literal vector searches —
    // Grok's tool-calling handles that smartly server-side instead.
    const { mode, query } = parseChatMode();
    clearTimeout(_chatSearchDebounce);
    if (mode !== 'find' || !query || query.length < 2) {
      document.getElementById('search-results').classList.remove('visible');
      return;
    }
    _chatSearchDebounce = setTimeout(() => searchNodes(query), 300);
  });

  // Enter (without Shift) sends the message
  chatTagify.DOM.input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      // If dropdown is open, let Tagify pick the suggestion
      if (chatTagify.dropdown.visible) return;
      e.preventDefault();
      sendChatMessage();
    }
    if (e.key === 'Escape') {
      document.getElementById('search-results').classList.remove('visible');
    }
  });
})();

/* Add both default badges (find: + chat:) to the chat input. Idempotent.
   Uses loadOriginalValues so it works at init time before the input has a caret. */
function seedDefaultBadges() {
  if (!chatTagify) return;
  const seed = '[[{"value":"find:","mode":"find"}]] [[{"value":"chat:","mode":"chat"}]] ';
  chatTagify.loadOriginalValues(seed);
}

/* Parse the chat-input value, returning {mode, query} where mode ∈ 'find'|'chat'|'both'. */
function parseChatMode() {
  if (!chatTagify) {
    const raw = (document.getElementById('chat-input').value || '').trim();
    const m = raw.match(/^(find|chat):\s*/i);
    return m ? { mode: m[1].toLowerCase(), query: raw.slice(m[0].length) } : { mode: 'both', query: raw };
  }
  const modes = (chatTagify.value || []).map(t => t.mode);
  const hasFind = modes.includes('find');
  const hasChat = modes.includes('chat');
  // Extract only text nodes outside of <tag> elements
  let query = '';
  chatTagify.DOM.input.childNodes.forEach(n => {
    if (n.nodeType === Node.TEXT_NODE) query += n.textContent;
    else if (n.nodeType === Node.ELEMENT_NODE && n.tagName && n.tagName.toLowerCase() !== 'tag') query += n.textContent;
  });
  query = query.trim();
  // Fallback: recognize typed prefixes when no badges exist
  if (!hasFind && !hasChat) {
    const m = query.match(/^(find|chat):\s*/i);
    if (m) {
      const single = m[1].toLowerCase();
      return { mode: single, query: query.slice(m[0].length) };
    }
    return { mode: 'both', query };
  }
  let mode = 'both';
  if (hasFind && !hasChat) mode = 'find';
  else if (hasChat && !hasFind) mode = 'chat';
  return { mode, query };
}

function clearChatInput() {
  if (chatTagify) {
    seedDefaultBadges();   // resets the entire input, badges + typed text
  } else {
    document.getElementById('chat-input').value = '';
  }
}

document.getElementById('chat-send').addEventListener('click', sendChatMessage);
document.getElementById('image-prompt').addEventListener('keydown', e => { if(e.key==='Enter') generateImage(); });
document.getElementById('image-generate-btn').addEventListener('click', generateImage);
document.getElementById('save-settings-btn').addEventListener('click', saveSettings);

/* ── Index Controls ────────────────────────────────────────────── */
async function fetchIndexCount() {
  try {
    const data = await apiFetch('/api/stats');
    const nodes = data.total_nodes || 0;
    const edges = data.total_edges || 0;
    document.getElementById('index-count-value').textContent = nodes.toLocaleString();
    const ev = document.getElementById('edge-count-value');
    if (ev) ev.textContent = edges.toLocaleString();
    return nodes;
  } catch (e) {
    document.getElementById('index-count-value').textContent = '0';
    const ev = document.getElementById('edge-count-value');
    if (ev) ev.textContent = '0';
    return 0;
  }
}

async function deleteIndex() {
  const count = document.getElementById('index-count-value').textContent;
  if (!confirm(`Delete the entire index (${count} nodes)? This cannot be undone.`)) return;
  const btn = document.getElementById('delete-index-btn');
  btn.disabled = true;
  try {
    const res = await fetch('/api/index', { method: 'DELETE' });
    if (res.ok) {
      showToast('Index deleted', 'success');
      graphCacheClear();
      document.getElementById('index-count-value').textContent = '0';
      const ev = document.getElementById('edge-count-value');
      if (ev) ev.textContent = '0';
      if (graphChart) { graphChart.dispose(); graphChart = null; }
      currentGraph = null;
      document.getElementById('status-nodes').textContent = '';
      document.getElementById('status-edges').textContent = '';
      loadFolderTree();
    } else { showToast('Failed to delete index', 'error'); }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; }
}

/* ══════════════════════════════════════════════════════════════
   Annotations: text highlights, notes, bookmarks (Phase 11 UI)
   ────────────────────────────────────────────────────────────
   Backend storage lives in `_apollo/annotations.json` and is exposed
   via /api/annotations*. This module wires up:
     - text-selection toolbar inside #left-detail-content
     - color highlights + note popover
     - bookmark toggle on the active node
     - sidebar list view (Notes / Bookmarks / Highlights)
   ══════════════════════════════════════════════════════════════ */
const Annotations = (function () {
  let toolbar, editor, editorQuote, editorTextarea;
  let pendingSel = null;          // {text, node_id, rect}
  let editingNoteId = null;       // when set, save updates instead of creates
  let _byNodeCache = {};          // node_id -> annotations[]
  let currentTab = 'notes';

  // Per-node note counts, fetched once via /api/annotations?type=note and used
  // to render the daisyUI indicator dots on the file-tree sidebar and the
  // node-detail page header.
  let _noteCountsByNodeId = {};
  let _noteCountsByFilePath = {};
  let _noteIndexLoaded = false;

  function init() {
    toolbar = document.getElementById('selection-toolbar');
    editor = document.getElementById('note-editor');
    editorQuote = document.getElementById('note-editor-quote');
    editorTextarea = document.getElementById('note-editor-textarea');
    const detail = document.getElementById('left-detail-content');
    if (!toolbar || !editor || !detail) return;

    detail.addEventListener('mouseup', () => setTimeout(onSelection, 0));
    detail.addEventListener('keyup', () => setTimeout(onSelection, 0));
    document.addEventListener('mousedown', onDocMouseDown, true);
    document.addEventListener('keydown', onDocKeyDown);
    document.addEventListener('scroll', () => { hideToolbar(); }, true);

    toolbar.querySelectorAll('.color-swatch').forEach(b => {
      b.addEventListener('mousedown', e => e.preventDefault());
      b.addEventListener('click', () => createHighlight(b.dataset.color));
    });
    toolbar.querySelector('[data-action="note"]').addEventListener('mousedown', e => e.preventDefault());
    toolbar.querySelector('[data-action="note"]').addEventListener('click', openNoteEditor);
    toolbar.querySelector('[data-action="close"]').addEventListener('click', () => {
      hideToolbar();
      window.getSelection().removeAllRanges();
    });

    editor.querySelector('[data-action="save"]').addEventListener('click', saveNote);
    editor.querySelector('[data-action="cancel"]').addEventListener('click', hideEditor);
  }

  function onSelection() {
    if (editor.classList.contains('visible')) return; // editor open: ignore new selections
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) { hideToolbar(); return; }
    const text = sel.toString().trim();
    if (!text || text.length < 2) { hideToolbar(); return; }
    if (!selectedNode) return;
    const detail = document.getElementById('left-detail-content');
    const range = sel.getRangeAt(0);
    if (!detail.contains(range.commonAncestorContainer)) { hideToolbar(); return; }
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return;
    pendingSel = { text, node_id: selectedNode };
    showToolbar(rect);
  }

  function showToolbar(rect) {
    // Position above the selection if room, else below
    toolbar.classList.add('visible');
    const tbRect = toolbar.getBoundingClientRect();
    let top = window.scrollY + rect.top - tbRect.height - 8;
    if (top < window.scrollY + 4) top = window.scrollY + rect.bottom + 8;
    let left = window.scrollX + rect.left + (rect.width / 2) - (tbRect.width / 2);
    left = Math.max(8, Math.min(left, window.scrollX + window.innerWidth - tbRect.width - 8));
    toolbar.style.left = left + 'px';
    toolbar.style.top = top + 'px';
  }

  function hideToolbar() {
    toolbar && toolbar.classList.remove('visible');
  }

  function hideEditor() {
    editor && editor.classList.remove('visible');
    editingNoteId = null;
  }

  function onDocMouseDown(e) {
    if (toolbar && toolbar.classList.contains('visible')) {
      if (toolbar.contains(e.target) || editor.contains(e.target)) return;
      // If user clicks anywhere else, hide toolbar
      hideToolbar();
    }
    if (editor && editor.classList.contains('visible')) {
      if (editor.contains(e.target)) return;
      // click outside editor: keep open if clicking inside toolbar; else close
      if (toolbar.contains(e.target)) return;
      hideEditor();
    }
  }

  function onDocKeyDown(e) {
    if (e.key === 'Escape') {
      if (editor.classList.contains('visible')) hideEditor();
      else if (toolbar.classList.contains('visible')) hideToolbar();
    }
  }

  async function createHighlight(color) {
    if (!pendingSel) return;
    try {
      await apiFetch('/api/annotations/create', {
        method: 'POST',
        body: {
          type: 'highlight',
          target: { type: 'node', node_id: pendingSel.node_id },
          content: pendingSel.text,
          color,
        },
      });
      _byNodeCache[pendingSel.node_id] = null;
      hideToolbar();
      window.getSelection().removeAllRanges();
      showToast('Highlight saved', 'success');
      await applyToDetail(pendingSel.node_id);
      pendingSel = null;
    } catch (e) {
      showToast('Failed to save highlight: ' + (e.message || e), 'error');
    }
  }

  function openNoteEditor() {
    if (!pendingSel) return;
    editorQuote.textContent = pendingSel.text.length > 280 ? pendingSel.text.slice(0, 280) + '…' : pendingSel.text;
    editorTextarea.value = '';
    editingNoteId = null;
    // Position the editor near the toolbar
    const tbRect = toolbar.getBoundingClientRect();
    editor.classList.add('visible');
    let top = window.scrollY + tbRect.bottom + 6;
    let left = window.scrollX + tbRect.left;
    const eRect = editor.getBoundingClientRect();
    left = Math.max(8, Math.min(left, window.scrollX + window.innerWidth - eRect.width - 8));
    editor.style.top = top + 'px';
    editor.style.left = left + 'px';
    hideToolbar();
    setTimeout(() => editorTextarea.focus(), 30);
  }

  async function saveNote() {
    const body = (editorTextarea.value || '').trim();
    if (!body) { showToast('Note is empty', 'info'); return; }
    try {
      if (editingNoteId) {
        await apiFetch(`/api/annotations/${encodeURIComponent(editingNoteId)}`, {
          method: 'PUT', body: { content: body },
        });
        showToast('Note updated', 'success');
      } else {
        if (!pendingSel) return;
        // Save the selected text in the note body as a Markdown blockquote
        const fullContent = `> ${pendingSel.text.replace(/\n/g, '\n> ')}\n\n${body}`;
        await apiFetch('/api/annotations/create', {
          method: 'POST',
          body: {
            type: 'note',
            target: { type: 'node', node_id: pendingSel.node_id },
            content: fullContent,
            color: 'yellow',
          },
        });
        _byNodeCache[pendingSel.node_id] = null;
        showToast('Note saved', 'success');
      }
      hideEditor();
      window.getSelection().removeAllRanges();
      const nid = pendingSel ? pendingSel.node_id : null;
      pendingSel = null;
      // Refresh detail panel if visible
      if (nid && selectedNode === nid) await applyToDetail(nid);
      // Refresh list view if open
      if (isHubAnnotationsVisible()) refreshList();
      // Refresh sidebar dots and detail-header indicator
      refreshIndicators();
    } catch (e) {
      showToast('Failed to save note: ' + (e.message || e), 'error');
    }
  }

  // ── Bookmark toggle ────────────────────────────────────────────
  async function toggleBookmark(nodeId, nodeName) {
    try {
      const ann = await getForNode(nodeId, false);
      const existing = ann.find(a => a.type === 'bookmark');
      if (existing) {
        await apiFetch(`/api/annotations/${encodeURIComponent(existing.id)}`, { method: 'DELETE' });
        showToast('Bookmark removed', 'success');
      } else {
        await apiFetch('/api/annotations/create', {
          method: 'POST',
          body: {
            type: 'bookmark',
            target: { type: 'node', node_id: nodeId },
            content: nodeName || nodeId,
          },
        });
        showToast('Bookmark added', 'success');
      }
      _byNodeCache[nodeId] = null;
      await applyToDetail(nodeId);
      if (isHubAnnotationsVisible()) refreshList();
    } catch (e) {
      showToast('Failed to toggle bookmark: ' + (e.message || e), 'error');
    }
  }

  // ── Read annotations for a node ───────────────────────────────
  async function getForNode(nodeId, useCache = true) {
    if (!nodeId) return [];
    if (useCache && _byNodeCache[nodeId]) return _byNodeCache[nodeId];
    try {
      const data = await apiFetch(`/api/annotations/by-target?node=${encodeURIComponent(nodeId)}`);
      _byNodeCache[nodeId] = data.annotations || [];
      return _byNodeCache[nodeId];
    } catch (e) {
      return [];
    }
  }

  // ── Render highlights + bookmark state into #left-detail-content
  async function applyToDetail(nodeId) {
    if (!nodeId) return;
    const annotations = await getForNode(nodeId, false);
    const detail = document.getElementById('left-detail-content');
    if (!detail) return;

    // Strip previously applied highlight wraps
    detail.querySelectorAll('.user-highlight').forEach(el => {
      const parent = el.parentNode;
      while (el.firstChild) parent.insertBefore(el.firstChild, el);
      parent.removeChild(el);
      parent.normalize();
    });

    // Refresh bookmark toggle button state
    const bm = detail.querySelector('.bookmark-toggle');
    if (bm) {
      const has = annotations.some(a => a.type === 'bookmark');
      bm.classList.toggle('active', has);
      bm.setAttribute('aria-pressed', has ? 'true' : 'false');
    }

    // Track {annId -> note body} for the per-line dot popovers below.
    const noteBodyById = {};

    // Apply visual highlights for highlight + note types
    for (const a of annotations) {
      if (a.type === 'highlight' && a.content) {
        wrapTextInElement(detail, a.content, a.color || 'yellow', a.id, false);
      } else if (a.type === 'note' && a.content) {
        // Pull the quoted selection from the front of the note body
        const m = a.content.match(/^>\s?([\s\S]+?)\n\n/);
        if (m) {
          wrapTextInElement(detail, m[1].replace(/\n>\s?/g, '\n'), a.color || 'yellow', a.id, true);
          noteBodyById[a.id] = m[2] || '';
        } else {
          noteBodyById[a.id] = a.content;
        }
      }
    }

    // Drop any line-note markers from a previous render, then add a dot
    // marker for every note highlight. Code views (.code-line rows) get a
    // right-margin dot per line; rendered prose (Markdown / HTML viewers
    // under .md-content) gets an inline dot rendered immediately after
    // the highlighted span. Either form shows a hover popover with the
    // note body so the user can read a saved note in place.
    detail.querySelectorAll('.line-note-marker').forEach(el => el.remove());
    detail.querySelectorAll('.code-line').forEach(line => line.classList.remove('has-note'));
    const seenAnnIds = new Set();
    const buildMarker = (annId) => {
      const body = noteBodyById[annId] || '';
      const marker = document.createElement('span');
      marker.className = 'line-note-marker';
      marker.dataset.annId = annId;
      marker.title = 'Note · click to view';
      marker.innerHTML = `<span class="line-note-popover">${escapeHtml(body)}</span>`;
      marker.addEventListener('click', e => onHighlightClick(e, annId, true));
      return marker;
    };
    detail.querySelectorAll('.user-highlight.is-note').forEach(span => {
      const annId = span.dataset.annId || '';
      if (seenAnnIds.has(annId)) return;
      seenAnnIds.add(annId);
      const line = span.closest('.code-line');
      if (line) {
        if (line.querySelector('.line-note-marker')) return;
        line.classList.add('has-note');
        line.appendChild(buildMarker(annId));
      } else {
        // Inline placement for markdown / HTML viewers without code rows.
        const marker = buildMarker(annId);
        marker.classList.add('inline');
        span.insertAdjacentElement('afterend', marker);
      }
    });

    // Fallback for notes whose quote text couldn't be wrapped (e.g. the
    // selection crossed nested elements in the markdown render).  Drop a
    // single inline marker at the top of the detail body so the user can
    // still see + open the note from the page.
    for (const a of annotations) {
      if (a.type !== 'note' || seenAnnIds.has(a.id)) continue;
      seenAnnIds.add(a.id);
      const marker = buildMarker(a.id);
      marker.classList.add('inline', 'orphan');
      detail.insertBefore(marker, detail.firstChild);
    }

    // Refresh the daisyUI note indicator in the detail header. Recompute the
    // count from the per-node fetch so it stays in sync even before the
    // global note index has refreshed.
    const noteCount = annotations.filter(a => a.type === 'note').length;
    _noteCountsByNodeId[nodeId] = noteCount;
    updateDetailNoteIndicator(nodeId);

    // Render the Google-Docs-style margin column with one card per note
    // (anchored vertically next to the highlighted text).
    renderMarginNotes(detail, annotations, noteBodyById, seenAnnIds);
  }

  // Build / update the right-margin notes column inside the detail panel.
  // Each note gets a card whose top is aligned with its anchor span (or
  // pinned to the top of the panel for "orphan" notes whose quote could
  // not be located in the rendered DOM).
  function renderMarginNotes(detail, annotations, noteBodyById, _seenAnnIds) {
    if (!detail) return;
    const notes = (annotations || []).filter(a => a.type === 'note');

    // Tear down any previous margin column.
    detail.querySelectorAll('.notes-margin').forEach(el => el.remove());
    detail.classList.toggle('has-margin-notes', notes.length > 0);
    if (!notes.length) return;

    const margin = document.createElement('aside');
    margin.className = 'notes-margin';
    margin.setAttribute('aria-label', 'Notes for this view');

    // Sort notes by vertical anchor position so they read top-to-bottom.
    const detailRect = detail.getBoundingClientRect();
    const placed = notes.map(a => {
      const anchor = detail.querySelector(`.user-highlight.is-note[data-ann-id="${cssEscape(a.id)}"]`);
      let top = 0;
      let orphan = false;
      if (anchor) {
        const r = anchor.getBoundingClientRect();
        top = (r.top - detailRect.top) + detail.scrollTop;
      } else {
        orphan = true;
        top = detail.scrollTop + 4;
      }
      return { ann: a, anchor, top, orphan };
    }).sort((x, y) => x.top - y.top);

    // Render cards into the margin (initial top will be re-laid out).
    placed.forEach(p => {
      const a = p.ann;
      const body = noteBodyById && noteBodyById[a.id] != null
        ? noteBodyById[a.id]
        : extractNoteBody(a.content || '');
      const quote = extractNoteQuote(a.content || '');
      const card = document.createElement('div');
      card.className = 'notes-margin-card' + (p.orphan ? ' orphan' : '');
      card.dataset.annId = a.id;
      const colorAttr = a.color ? ` data-color="${escapeHtml(a.color)}"` : '';
      card.innerHTML = `
        <div class="nm-card-head">
          <span class="nm-dot"${colorAttr}></span>
          <span class="nm-time">${escapeHtml((a.created_at || '').split('T')[0] || '')}</span>
          ${p.orphan ? '<span class="nm-orphan-tag" title="Original anchor not found in current view">orphan</span>' : ''}
          <button type="button" class="nm-card-delete" title="Delete note" aria-label="Delete note">✕</button>
        </div>
        ${quote ? `<div class="nm-quote">${escapeHtml(quote)}</div>` : ''}
        <div class="nm-body">${escapeHtml(body)}</div>
      `;
      card.addEventListener('click', e => {
        if (e.target.closest('.nm-card-delete')) return;
        if (p.anchor) {
          p.anchor.scrollIntoView({ block: 'center', behavior: 'smooth' });
          flashAnchor(p.anchor);
        }
        // Open editor on click of body so the user can edit the note in place.
        showNoteViewer(a, e.clientX, e.clientY);
      });
      card.querySelector('.nm-card-delete').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm('Delete this note?')) return;
        try {
          await apiFetch(`/api/annotations/${encodeURIComponent(a.id)}`, { method: 'DELETE' });
          if (selectedNode) _byNodeCache[selectedNode] = null;
          showToast('Note deleted', 'success');
          if (selectedNode) await applyToDetail(selectedNode);
          if (isHubAnnotationsVisible()) refreshList();
          refreshIndicators();
        } catch (err) {
          showToast('Failed to delete note', 'error');
        }
      });
      margin.appendChild(card);
    });

    detail.appendChild(margin);

    // After cards are in the DOM we know their heights, so do a second
    // pass to vertically stack them without overlapping.  Each card is
    // pulled toward its anchor `top` but pushed down past the previous
    // card if needed (Google-Docs-style "comment column").
    requestAnimationFrame(() => layoutMarginCards(detail, margin, placed));
  }

  function layoutMarginCards(detail, margin, placed) {
    const cards = Array.from(margin.querySelectorAll('.notes-margin-card'));
    if (!cards.length) return;
    const GAP = 8;
    let cursor = 0;
    cards.forEach((card, i) => {
      const wanted = Math.max(placed[i] ? placed[i].top : 0, cursor);
      card.style.top = wanted + 'px';
      cursor = wanted + card.getBoundingClientRect().height + GAP;
    });
  }

  function extractNoteQuote(content) {
    const m = (content || '').match(/^>\s?([\s\S]+?)\n\n/);
    return m ? m[1].replace(/\n>\s?/g, '\n') : '';
  }
  function extractNoteBody(content) {
    const m = (content || '').match(/^>\s?[\s\S]+?\n\n([\s\S]*)$/);
    return m ? m[1] : (content || '');
  }
  function cssEscape(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }
  function flashAnchor(el) {
    if (!el) return;
    el.classList.remove('nm-flash');
    // force reflow so the animation restarts
    void el.offsetWidth;
    el.classList.add('nm-flash');
    setTimeout(() => el.classList.remove('nm-flash'), 1400);
  }

  // Walk text nodes building a flat character index, find the first
  // occurrence of `text` (whitespace-normalised), and wrap each text-node
  // fragment that participates in the match with a `.user-highlight` span
  // sharing the same data-ann-id. Works across element boundaries (e.g.
  // markdown <strong>, syntax-highlighted hljs spans).
  function wrapTextInElement(root, text, color, annId, isNote) {
    if (!text || text.trim().length < 2) return false;

    // Collect candidate text nodes in document order, skipping nodes that
    // already live inside a user-highlight / script / style block.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
        if (!n.parentElement) return NodeFilter.FILTER_REJECT;
        if (n.parentElement.closest('.user-highlight')) return NodeFilter.FILTER_REJECT;
        if (n.parentElement.closest('script,style')) return NodeFilter.FILTER_REJECT;
        // Skip our own gutter / line-number cells so we never highlight the
        // line numbers in lined code blocks.
        if (n.parentElement.closest('.ln,.line-note-marker,.notes-margin')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    // Build a normalised string + offset map.  Every run of whitespace
    // (including line breaks coming from <br>/markdown re-flow) collapses
    // to a single space so the match survives wrapping differences.
    const segments = [];           // {node, start, end} into normalized string
    let normalized = '';
    let prevWasSpace = true;       // collapse leading whitespace
    let node;
    while ((node = walker.nextNode())) {
      const raw = node.nodeValue;
      const segStart = normalized.length;
      // Track per-character mapping back to the original node index.
      const charMap = [];
      for (let i = 0; i < raw.length; i++) {
        const ch = raw[i];
        const isWs = /\s/.test(ch);
        if (isWs) {
          if (prevWasSpace) continue;
          normalized += ' ';
          charMap.push(i);
          prevWasSpace = true;
        } else {
          normalized += ch;
          charMap.push(i);
          prevWasSpace = false;
        }
      }
      const segEnd = normalized.length;
      segments.push({ node, start: segStart, end: segEnd, charMap });
    }

    const needle = text.replace(/\s+/g, ' ').trim();
    if (!needle) return false;

    // Strip leading whitespace from the normalized haystack for search but
    // keep it for offset lookups.
    const idx = normalized.indexOf(needle);
    if (idx === -1) return false;
    const matchStart = idx;
    const matchEnd = idx + needle.length;

    // Walk the segments, splitting and wrapping the slice that overlaps
    // [matchStart, matchEnd).  Multiple spans share the same annId so the
    // margin panel can find every fragment.
    let wrappedAny = false;
    for (const seg of segments) {
      if (seg.end <= matchStart || seg.start >= matchEnd) continue;
      const localFrom = Math.max(matchStart, seg.start) - seg.start;
      const localTo   = Math.min(matchEnd,   seg.end)   - seg.start;
      // Map from normalized-local offsets back to raw text-node offsets.
      const rawFrom = seg.charMap[localFrom] != null ? seg.charMap[localFrom] : seg.node.nodeValue.length;
      // localTo is exclusive; charMap[localTo-1]+1 is the right edge.
      let rawTo;
      if (localTo <= 0) continue;
      if (localTo - 1 >= seg.charMap.length) {
        rawTo = seg.node.nodeValue.length;
      } else {
        rawTo = seg.charMap[localTo - 1] + 1;
      }
      if (rawTo <= rawFrom) continue;
      try {
        const range = document.createRange();
        range.setStart(seg.node, rawFrom);
        range.setEnd(seg.node, rawTo);
        const span = document.createElement('span');
        span.className = 'user-highlight' + (isNote ? ' is-note' : '');
        span.dataset.color = color;
        span.dataset.annId = annId;
        span.title = isNote ? 'Note · click to view' : 'Highlight · click to remove';
        span.addEventListener('click', e => onHighlightClick(e, annId, isNote));
        range.surroundContents(span);
        wrappedAny = true;
      } catch (e) {
        // Boundary collision (rare here since we operate on a single text
        // node at a time); skip this fragment but keep going.
      }
    }
    return wrappedAny;
  }

  async function onHighlightClick(e, annId, isNote) {
    e.stopPropagation();
    if (isNote) {
      try {
        const ann = await apiFetch(`/api/annotations/${encodeURIComponent(annId)}`);
        showNoteViewer(ann, e.clientX, e.clientY);
      } catch (err) {
        showToast('Failed to load note', 'error');
      }
    } else {
      if (!confirm('Remove this highlight?')) return;
      try {
        await apiFetch(`/api/annotations/${encodeURIComponent(annId)}`, { method: 'DELETE' });
        if (selectedNode) _byNodeCache[selectedNode] = null;
        showToast('Highlight removed', 'success');
        if (selectedNode) await applyToDetail(selectedNode);
        if (isHubAnnotationsVisible()) refreshList();
      } catch (err) {
        showToast('Failed: ' + (err.message || err), 'error');
      }
    }
  }

  // Reuse the editor as a viewer/editor for an existing note
  function showNoteViewer(ann, x, y) {
    editingNoteId = ann.id;
    pendingSel = null;
    const m = (ann.content || '').match(/^>\s?([\s\S]+?)\n\n([\s\S]*)$/);
    const quote = m ? m[1].replace(/\n>\s?/g, '\n') : '';
    const body = m ? m[2] : (ann.content || '');
    editorQuote.textContent = quote;
    editorTextarea.value = body;
    editor.classList.add('visible');
    const eRect = editor.getBoundingClientRect();
    let left = window.scrollX + (x || window.innerWidth / 2) - 20;
    let top  = window.scrollY + (y || window.innerHeight / 2) + 12;
    left = Math.max(8, Math.min(left, window.scrollX + window.innerWidth  - eRect.width  - 8));
    top  = Math.max(8, Math.min(top,  window.scrollY + window.innerHeight - eRect.height - 8));
    editor.style.left = left + 'px';
    editor.style.top  = top  + 'px';
    setTimeout(() => editorTextarea.focus(), 30);
  }

  // ── Annotations sub-tab inside My Hub ─────────────────────────
  function openTab(tab) {
    currentTab = tab;
    document.querySelectorAll('#hub-pane-annotations .ann-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.annTab === tab);
    });
    refreshList();
  }

  function currentSubFilter() { return currentTab; }

  function isHubAnnotationsVisible() {
    const pane = document.getElementById('hub-pane-annotations');
    return !!(pane && !pane.classList.contains('hidden'));
  }

  async function refreshList() {
    const list = document.getElementById('annotations-list');
    if (!list) return;
    list.innerHTML = '<div class="ann-empty">Loading…</div>';
    let typeFilter = currentTab === 'notes' ? 'note' : currentTab === 'bookmarks' ? 'bookmark' : 'highlight';
    try {
      const data = await apiFetch(`/api/annotations?type=${encodeURIComponent(typeFilter)}`);
      const items = data.annotations || [];
      if (!items.length) {
        list.innerHTML = `<div class="ann-empty">No ${currentTab} yet.<br>Select text in a node detail panel to create one.</div>`;
        return;
      }
      list.innerHTML = items.map(renderRow).join('');
      list.querySelectorAll('[data-go-node]').forEach(el => {
        el.addEventListener('click', () => {
          const nid = el.dataset.goNode;
          switchView('graph');
          setTimeout(() => { focusNodeInGraph(nid); selectNode(nid); }, 80);
        });
      });
      list.querySelectorAll('[data-delete-id]').forEach(el => {
        el.addEventListener('click', async () => {
          const id = el.dataset.deleteId;
          if (!confirm('Delete this annotation?')) return;
          try {
            await apiFetch(`/api/annotations/${encodeURIComponent(id)}`, { method: 'DELETE' });
            const nid = el.dataset.deleteNode;
            if (nid) _byNodeCache[nid] = null;
            if (nid && selectedNode === nid) await applyToDetail(nid);
            refreshList();
            refreshIndicators();
          } catch (e) {
            showToast('Failed to delete', 'error');
          }
        });
      });
    } catch (e) {
      list.innerHTML = `<div class="ann-empty">Failed to load annotations.<br>${escapeHtml(e.message || String(e))}</div>`;
    }
  }

  function renderRow(a) {
    const tgt = a.target || {};
    const tgtLabel = tgt.type === 'node' ? (tgt.node_id || '') : (tgt.file_path || '');
    const when = (a.created_at || '').split('T')[0] || '';
    const colorDot = a.color
      ? `<span class="ann-row-color-dot" data-color="${escapeHtml(a.color)}" title="${escapeHtml(a.color)}"></span>`
      : '';
    const stale = a.stale ? `<span class="badge badge-xs badge-warning">stale</span>` : '';
    let body = '';
    if (a.type === 'note' && a.content) {
      const m = a.content.match(/^>\s?([\s\S]+?)\n\n([\s\S]*)$/);
      const quote = m ? m[1].replace(/\n>\s?/g, '\n') : '';
      const note = m ? m[2] : a.content;
      body = `${quote ? `<div class="ann-row-content" style="opacity:0.7;border-left:3px solid oklch(var(--p));padding-left:8px;margin-bottom:4px">${escapeHtml(quote)}</div>` : ''}<div class="ann-row-content">${escapeHtml(note)}</div>`;
    } else if (a.content) {
      body = `<div class="ann-row-content">${escapeHtml(a.content)}</div>`;
    }
    const goAttr = tgt.type === 'node' && tgt.node_id ? `data-go-node="${escapeHtml(tgt.node_id)}"` : '';
    return `
      <div class="ann-row">
        <div class="ann-row-head">
          <span class="ann-row-type">${escapeHtml(a.type)}</span>
          ${colorDot}
          <span class="ann-row-target" ${goAttr} title="${escapeHtml(tgtLabel)}">${escapeHtml(tgtLabel)}</span>
          ${stale}
          <span class="ann-row-time">${escapeHtml(when)}</span>
        </div>
        ${body}
        <div class="ann-row-actions">
          ${tgt.type === 'node' && tgt.node_id ? `<button type="button" class="ann-action" ${goAttr}>Open</button>` : ''}
          <button type="button" class="ann-action danger" data-delete-id="${escapeHtml(a.id)}" data-delete-node="${escapeHtml(tgt.node_id || '')}">Delete</button>
        </div>
      </div>
    `;
  }

  // ── Note index (powers tree dots + detail-header indicator) ─────
  async function loadNoteIndex() {
    try {
      const data = await apiFetch('/api/annotations?type=note');
      const items = data.annotations || [];
      const byNode = {};
      const byFile = {};
      for (const a of items) {
        const t = a.target || {};
        if (t.type === 'node' && t.node_id) {
          byNode[t.node_id] = (byNode[t.node_id] || 0) + 1;
        } else if (t.type === 'file' && t.file_path) {
          byFile[t.file_path] = (byFile[t.file_path] || 0) + 1;
        }
      }
      _noteCountsByNodeId = byNode;
      _noteCountsByFilePath = byFile;
      _noteIndexLoaded = true;
    } catch (e) {
      _noteCountsByNodeId = {};
      _noteCountsByFilePath = {};
      _noteIndexLoaded = true;
    }
  }

  function noteCountForNode(nodeId) {
    return _noteCountsByNodeId[nodeId] || 0;
  }

  // Decorate every file row in the sidebar tree with a daisyUI indicator dot
  // when the corresponding node has at least one saved note.
  function applyTreeIndicators() {
    const rows = document.querySelectorAll('#folder-tree .tree-row.is-file');
    rows.forEach(row => {
      // The node id was stashed on the row's click handler; we need a stable
      // way to recover it, so we re-derive from the title (full path) instead.
      // The renderer doesn't put node-id on the DOM, so we tag it via a
      // dataset attribute below.
      const nid = row.dataset.nodeId;
      const existing = row.querySelector('.tree-row-note-dot');
      const count = nid ? noteCountForNode(nid) : 0;
      if (count > 0) {
        if (!existing) {
          const dot = document.createElement('span');
          dot.className = 'tree-row-note-dot';
          dot.title = count === 1 ? '1 note' : `${count} notes`;
          dot.setAttribute('aria-label', dot.title);
          row.appendChild(dot);
        } else {
          existing.title = count === 1 ? '1 note' : `${count} notes`;
        }
      } else if (existing) {
        existing.remove();
      }
    });
  }

  async function refreshIndicators() {
    await loadNoteIndex();
    applyTreeIndicators();
    if (selectedNode) updateDetailNoteIndicator(selectedNode);
  }

  // Toggle the visibility of per-line note markers + the inline note
  // highlights inside the detail panel so the user can read the source
  // unobstructed and bring the notes back with a single click.
  function toggleNotesVisibility() {
    const detail = document.getElementById('left-detail-content');
    if (!detail) return;
    const hidden = detail.classList.toggle('notes-hidden');
    const label = document.querySelector('#detail-note-indicator .notes-toggle-label');
    if (label) label.textContent = hidden ? 'show notes' : 'notes';
  }

  // Update / inject the indicator badge in the detail header. The header is
  // re-rendered on every showDetail() call, so this just patches the count
  // when notes change without rerendering everything else.
  function updateDetailNoteIndicator(nodeId) {
    if (!nodeId) return;
    const wrap = document.getElementById('detail-note-indicator');
    if (!wrap) return;
    const count = noteCountForNode(nodeId);
    const badge = wrap.querySelector('.indicator-item');
    if (count > 0) {
      wrap.classList.remove('hidden');
      if (badge) badge.textContent = String(count);
    } else {
      wrap.classList.add('hidden');
    }
  }

  return {
    init,
    applyToDetail,
    toggleBookmark,
    getForNode,
    openTab,
    refreshList,
    currentSubFilter,
    loadNoteIndex,
    noteCountForNode,
    applyTreeIndicators,
    refreshIndicators,
    updateDetailNoteIndicator,
    toggleNotesVisibility,
  };
})();

/* Build the bookmark-star button HTML for a node detail header. */
function bookmarkButtonHtml(nodeId, nodeName) {
  const safeId = String(nodeId || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  const safeName = String(nodeName || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  return `<button type="button" class="bookmark-toggle" title="Toggle bookmark" aria-pressed="false" onclick="Annotations.toggleBookmark('${safeId}', '${safeName}')">★</button>`;
}

/* Build the daisyUI indicator showing how many notes target this node.
   Hidden by default; populated lazily by Annotations.updateDetailNoteIndicator()
   so we don't have to await the index before rendering the header.
   Clicking it toggles the visibility of the per-line note dots / popovers. */
function noteIndicatorHtml(nodeId) {
  const count = (typeof Annotations !== 'undefined' && Annotations.noteCountForNode)
    ? Annotations.noteCountForNode(nodeId) : 0;
  const hidden = count > 0 ? '' : ' hidden';
  const noteIcon = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3 h-3"><path stroke-linecap="round" stroke-linejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" /></svg>';
  return `
    <div id="detail-note-indicator" class="indicator${hidden}" title="Click to show/hide notes on this page">
      <span class="indicator-item badge badge-primary badge-xs">${count}</span>
      <button type="button" class="badge badge-sm badge-ghost gap-1" onclick="Annotations.toggleNotesVisibility()">${noteIcon} <span class="notes-toggle-label">notes</span></button>
    </div>`;
}

/* ── Init ──────────────────────────────────────────────────────── */
const IS_DEV_MODE = new URLSearchParams(location.search).get('dev') === 'true';
if (IS_DEV_MODE) {
  document.getElementById('dev-toolbar')?.classList.remove('hidden');
  // Reveal Graph 1 / Graph 2 variant tabs above the graph chart so we
  // can A/B test stable-layout (Graph 2) vs. force-directed (Graph 1).
  document.getElementById('graph-variant-tabs')?.classList.remove('hidden');
}
fetchIndexCount(); checkChatStatus(); loadFolderTree();
// Load the graph on startup, but show the Welcome tab by default
// (no auto-selection of the largest node).
const _boot = () => { showWelcomePanel(); loadGraph(); Annotations.init(); };
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _boot);
} else {
  _boot();
}
