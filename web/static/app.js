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
  variable: '#6bcb77', file: '#8b8b8b', directory: '#666666', import: '#b088f9',
};
let graphChart = null, wordCloudChart = null, currentGraph = null, selectedNode = null, currentView = 'graph';
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
  if (graphChart) { graphChart.dispose(); graphChart = null; loadGraph(); }
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
  setTimeout(() => { if (graphChart) graphChart.resize(); if (wordCloudChart) wordCloudChart.resize(); }, 300);
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
        detail = j.detail || j.error || j.message || detail;
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
    .then(data => {
      if (!data.path) { dot.className = 'w-1.5 h-1.5 rounded-full bg-base-content/30'; txt.textContent = 'Ready'; return; }
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
function submitFolderPicker() {
  const errEl = document.getElementById('folder-picker-error');
  const loadEl = document.getElementById('folder-picker-loading');
  const goBtn = document.getElementById('folder-picker-go');
  errEl.classList.add('hidden');
  loadEl.classList.remove('hidden');
  goBtn.disabled = true;
  closeFolderPicker();
  showIndexingModal(_browseCurrentPath);
  fetch('/api/index', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ directory: _browseCurrentPath }) })
    .then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || `Error ${r.status}`); }); return r.json(); })
    .then(() => { graphCacheClear(); fetchIndexCount(); })
    .catch(e => { errEl.textContent = e.message; errEl.classList.remove('hidden'); })
    .finally(() => { loadEl.classList.add('hidden'); goBtn.disabled = false; });
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
  if (view === 'graph') setTimeout(() => { if (graphChart) graphChart.resize(); if (wordCloudChart) wordCloudChart.resize(); }, 50);
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
    if (!selectedNode && data.nodes && data.nodes.length) {
      const largest = data.nodes.reduce((a, b) => ((b.symbolSize||0) > (a.symbolSize||0) ? b : a), data.nodes[0]);
      setTimeout(() => softSelectNode(largest.id), 300);
    }
  } catch (e) { console.error(e); document.getElementById('status-text').textContent = 'Error'; }
  finally { overlay.classList.add('hidden'); }
}

function renderGraph(data) {
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
  const nodes = (data.nodes||[]).map(n => {
    const t = n.value||n.type||'unknown';
    const sz = Math.max(8,Math.min(40,n.symbolSize||12));
    return { id:n.id, name:n.name||n.id, symbolSize:sz, category:catIdx[t]??0,
      itemStyle:{color:NODE_COLORS[t]||'#888'}, label:{show:!large&&sz>18,fontSize:10,color:tc},
      _type:t, _path:n.attributes?.path||n.path, _line:n.attributes?.line_start||n.line };
  });
  const edges = (data.edges||[]).map(e => ({ source:e.source, target:e.target, lineStyle:{color:ec,opacity:0.5}, _rel:e.type||e.rel }));
  graphChart.setOption({
    tooltip: { trigger:'item', formatter: p => { if(p.dataType==='node'){const d=p.data;let t=`<b>${d.name}</b><br/>Type: ${d._type}`;if(d._path)t+=`<br/>${d._path}${d._line!=null?':'+d._line:''}`;return t;}return'';},
      backgroundColor:isDark?'#2b2b2b':'#fff', borderColor:isDark?'#3a3a3a':'#ddd', textStyle:{color:tc,fontSize:11} },
    legend: { data:categories.map(c=>c.name), bottom:8, textStyle:{color:tc,fontSize:11}, selectedMode:true,
      icon:'circle', itemWidth:10, itemHeight:10, itemGap:12 },
    animationDurationUpdate: large ? 0 : 500,
    animation: !large,
    series: [{ type:'graph', layout:'force', data:nodes, links:edges, categories, roam:true, draggable:true,
      large: large,
      force:{repulsion:large?80:120,edgeLength:large?[40,120]:[60,200],gravity:large?0.15:0.08,layoutAnimation:!large,friction:large?0.4:0.6},
      emphasis:{focus:'adjacency',lineStyle:{width:3,color:'#7c3aed'}},
      select:{itemStyle:{borderColor:'#7c3aed',borderWidth:3,shadowBlur:10,shadowColor:'#7c3aed'},label:{show:true,fontSize:12,fontWeight:'bold',color:tc}},
      selectedMode:'single',
      edgeLabel:{show:false}, lineStyle:{curveness:0.1} }],
  }, true);
}

async function onGraphClick(p) {
  if (p.dataType === 'node') await selectNode(p.data.id);
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
  const el = document.getElementById('left-detail-content');
  document.querySelector('#left-content h3').textContent = data.name || data.path || 'File';

  // Brief loading state.
  el.innerHTML = '<div class="text-xs opacity-60 py-4">Loading file…</div>';

  let file;
  try {
    file = await apiFetch(`/api/file/content?path=${encodeURIComponent(data.path)}`);
  } catch (e) {
    el.innerHTML = `<div class="text-xs text-error py-4">Failed to load file: ${escapeHtml(e.message || String(e))}</div>`;
    return;
  }

  const ext = (file.extension || '').toLowerCase();
  const lang = file.language || '';
  const typeColor = NODE_COLORS.file || '#888';
  const sizeKb = (file.size_bytes / 1024).toFixed(1);

  const header = `
    <div class="flex items-center gap-2 flex-wrap mb-3">
      <span class="badge badge-sm font-semibold" style="background:${typeColor};color:#000">file</span>
      ${lang ? `<span class="badge badge-sm badge-outline font-mono">${escapeHtml(lang)}</span>` : ''}
      <span class="badge badge-sm badge-ghost font-mono" title="${escapeHtml(file.path)}">${escapeHtml(relativePath(data.path) || file.relative_path || relativePath(file.path))}</span>
      <span class="badge badge-sm badge-ghost font-mono">${sizeKb} KB</span>
      ${file.truncated ? '<span class="badge badge-sm badge-warning">truncated</span>' : ''}
      ${file.is_binary ? '<span class="badge badge-sm badge-warning">binary</span>' : ''}
    </div>`;

  let body;
  if (file.is_binary) {
    body = '<div class="text-xs opacity-60 py-4 italic">Binary file — preview not available.</div>';
  } else if (ext === '.md' || ext === '.markdown') {
    const html = (typeof marked !== 'undefined') ? marked.parse(file.content) : `<pre>${escapeHtml(file.content)}</pre>`;
    body = `<div class="md-content">${html}</div>`;
  } else if (ext === '.html' || ext === '.htm') {
    const safeSrc = file.content.replace(/<\/script>/gi, '<\\/script>');
    const iframe = `<iframe sandbox="allow-same-origin" style="width:100%;height:480px;border:1px solid var(--fallback-b3,#444);border-radius:6px;background:#fff" srcdoc="${escapeHtml(safeSrc)}"></iframe>`;
    let highlighted;
    try { highlighted = hljs.highlight(file.content, { language: 'html', ignoreIllegals: true }).value; }
    catch(e) { highlighted = escapeHtml(file.content); }
    body = `
      <div role="tablist" class="tabs tabs-bordered tabs-sm mb-2">
        <button role="tab" class="tab tab-active" data-tab="detail-tab-preview" onclick="switchDetailTab(this)">Preview</button>
        <button role="tab" class="tab" data-tab="detail-tab-source" onclick="switchDetailTab(this)">Source</button>
      </div>
      <div id="detail-tab-preview">${iframe}</div>
      <div id="detail-tab-source" class="hidden"><pre class="detail-code-block"><code class="hljs">${highlighted}</code></pre></div>`;
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

  el.innerHTML = header + body;
}

/* Full select: detail panel + ring + adjacency focus */
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
  const el = document.getElementById('left-detail-content');
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

  const headerHtml = isDir
    ? `<div class="flex items-center gap-2 flex-wrap mb-3">
        <span class="badge badge-sm font-semibold" style="background:${typeColor};color:#000">${data.type}</span>
      </div>`
    : `<div class="flex items-center gap-2 flex-wrap mb-3">
        <span class="badge badge-sm font-semibold" style="background:${typeColor};color:#000">${data.type}</span>
        ${lang ? `<span class="badge badge-sm badge-outline font-mono">${lang}</span>` : ''}
        ${data.path ? `<span class="badge badge-sm badge-ghost font-mono gap-1" title="${escapeHtml(data.path)}">${fileIcon} ${escapeHtml(relativePath(data.path))}</span>` : ''}
        ${data.line_start!=null ? `<span class="badge badge-sm badge-ghost font-mono gap-1">${lineIcon} L${data.line_start}${data.line_end ? '-'+data.line_end : ''}</span>` : ''}
      </div>`;

  const sourceSectionHtml = isDir
    ? ''
    : `<div class="mb-3">
        <div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1">Source</div>
        ${codeHtml}
      </div>`;

  el.innerHTML = `
    ${headerHtml}
    <div role="tablist" class="tabs tabs-bordered tabs-sm mb-2">
      <button role="tab" class="tab tab-active" data-tab="detail-tab-content" onclick="switchDetailTab(this)">Details</button>
      <button role="tab" class="tab" data-tab="detail-tab-connections" onclick="switchDetailTab(this)">Connections <span class="badge badge-xs badge-primary ml-1">${visibleIn.length + visibleOut.length}</span></button>
    </div>
    <div id="detail-tab-content">
      ${sourceSectionHtml}
    </div>
    <div id="detail-tab-connections" class="hidden">
      ${connectionRows}
    </div>
  `;
  document.querySelector('#left-content h3').textContent = data.name || 'Node Detail';

  // Auto-scroll the highlighted region into view (if any).
  if (useFullFile && startLine) {
    requestAnimationFrame(() => {
      const target = el.querySelector('.code-line.hl-start');
      if (target) target.scrollIntoView({ block: 'center' });
    });
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
  const out = [];
  for (let k = 0; k < lines.length; k++) {
    const ln = k + 1;
    const cls = ['code-line'];
    if (start && ln >= start && ln <= (end || start)) cls.push('hl');
    if (start && ln === start) cls.push('hl-start');
    if (start && ln === (end || start)) cls.push('hl-end');
    out.push(`<div class="${cls.join(' ')}" data-line="${ln}"><span class="ln">${ln}</span><span class="lc">${lines[k] || ' '}</span></div>`);
  }
  return `<pre class="detail-code-block lined"><code class="hljs">${out.join('')}</code></pre>`;
}

function switchDetailTab(tabEl) {
  const parent = tabEl.closest('[role="tablist"]');
  parent.querySelectorAll('.tab').forEach(t => t.classList.remove('tab-active'));
  tabEl.classList.add('tab-active');
  const targetId = tabEl.dataset.tab;
  const container = parent.parentElement;
  container.querySelectorAll('[id^="detail-tab-"]').forEach(p => p.classList.toggle('hidden', p.id !== targetId));
}

function buildConnectionRows(edgesIn, edgesOut) {
  const inArr = edgesIn || [];
  const outArr = edgesOut || [];

  if (!inArr.length && !outArr.length) {
    return '<div class="text-xs opacity-60 py-4 text-center">No connections</div>';
  }

  let html = '';

  if (inArr.length) {
    html += '<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Incoming</div>';
    html += '<div class="space-y-1 mb-3">';
    inArr.forEach(e => {
      const name = e.name || e.source;
      const rel = e.rel || e.type || 'related';
      const nodeType = e.source_type || e.type || '';
      const color = NODE_COLORS[nodeType] || '#888';
      const escapedId = (e.source_id || e.source || '').replace(/'/g, "\\'");
      html += `<div class="flex items-center gap-2 p-1.5 rounded hover:bg-base-300 cursor-pointer connection-row" onclick="connectionClick('${escapedId}')">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5 opacity-50 flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3" /></svg>
        <span class="badge badge-xs font-semibold flex-shrink-0" style="background:${color};color:#000">${nodeType || '?'}</span>
        <span class="text-xs font-medium flex-1 truncate">${name}</span>
        <span class="badge badge-xs badge-outline flex-shrink-0">${rel}</span>
      </div>`;
    });
    html += '</div>';
  }

  if (outArr.length) {
    html += '<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Outgoing</div>';
    html += '<div class="space-y-1 mb-3">';
    outArr.forEach(e => {
      const name = e.name || e.target;
      const rel = e.rel || e.type || 'related';
      const nodeType = e.target_type || e.type || '';
      const color = NODE_COLORS[nodeType] || '#888';
      const escapedId = (e.target_id || e.target || '').replace(/'/g, "\\'");
      html += `<div class="flex items-center gap-2 p-1.5 rounded hover:bg-base-300 cursor-pointer connection-row" onclick="connectionClick('${escapedId}')">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5 opacity-50 flex-shrink-0"><path stroke-linecap="round" stroke-linejoin="round" d="M15 15l6-6m0 0l-6-6m6 6H9a6 6 0 000 12h3" /></svg>
        <span class="badge badge-xs font-semibold flex-shrink-0" style="background:${color};color:#000">${nodeType || '?'}</span>
        <span class="text-xs font-medium flex-1 truncate">${name}</span>
        <span class="badge badge-xs badge-outline flex-shrink-0">${rel}</span>
      </div>`;
    });
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
        <span class="badge badge-xs font-semibold" style="background:${NODE_COLORS[r.type]||'#444'};color:#000">${r.type}</span>
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
  const el = document.getElementById('left-detail-content');
  document.querySelector('#left-content h3').textContent = 'Apollo';
  const stats = await loadStats();

  let statsHtml = '';
  if (stats) {
    statsHtml += `<div class="stats stats-horizontal shadow bg-base-200 w-full mb-4">
      <div class="stat py-2 px-4"><div class="stat-title text-[10px]">Nodes</div><div class="stat-value text-base text-primary">${(stats.total_nodes||0).toLocaleString()}</div></div>
      <div class="stat py-2 px-4"><div class="stat-title text-[10px]">Edges</div><div class="stat-value text-base text-secondary">${(stats.total_edges||0).toLocaleString()}</div></div>
    </div>`;

    if (stats.node_types && Object.keys(stats.node_types).length) {
      statsHtml += '<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Node Types</div><div class="space-y-1 mb-3">';
      for (const [t,c] of Object.entries(stats.node_types))
        statsHtml += `<div class="flex justify-between items-center text-xs"><span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full inline-block flex-shrink-0" style="background:${NODE_COLORS[t]||'#555'}"></span>${t}</span><span class="badge badge-xs badge-primary">${c.toLocaleString()}</span></div>`;
      statsHtml += '</div>';
    }

    if (stats.edge_types && Object.keys(stats.edge_types).length) {
      statsHtml += '<div class="text-xs font-semibold uppercase tracking-wider opacity-70 mb-1.5">Edge Types</div><div class="space-y-1 mb-3">';
      for (const [t,c] of Object.entries(stats.edge_types))
        statsHtml += `<div class="flex justify-between items-center text-xs"><span>${t}</span><span class="badge badge-xs badge-outline">${c.toLocaleString()}</span></div>`;
      statsHtml += '</div>';
    }
  }

  el.innerHTML = `
    <div class="md-content">
      <h2>Welcome</h2>
      <p>Explore your codebase as an interactive graph. Click any node to inspect its source code, connections, and relationships.</p>
      <h3>Quick Start</h3>
      <ul>
        <li><strong>My Files</strong> &mdash; index a folder to build the graph</li>
        <li><strong>AI Chat</strong> &mdash; type a question; add <code>find:</code> for graph-only search or <code>chat:</code> for plain chat</li>
        <li><strong>Click a node</strong> &mdash; view source and connections</li>
        <li><strong>Click empty space</strong> &mdash; reset the view</li>
        <li><strong>Depth slider</strong> &mdash; control how many nodes are shown</li>
      </ul>
      ${stats ? '<h3>Index Analytics</h3>' : ''}
    </div>
    ${statsHtml}
  `;
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
  ad.innerHTML = '...';
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
      document.getElementById('chat-messages').scrollTop=document.getElementById('chat-messages').scrollHeight;
    }}
    ad.querySelectorAll('pre code:not(.hljs)').forEach(b => hljs.highlightElement(b));
    _wireChipHandlers(ad);
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

function _esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function renderProvidersUI(d) {
  _providerRegistry = d.providers || [];
  const active = d.chat?.active_provider || (_providerRegistry[0] && _providerRegistry[0].id);
  const perProvider = d.chat?.providers || {};

  // Active-provider selector
  const sel = document.getElementById('active-provider');
  sel.innerHTML = _providerRegistry.map(p =>
    `<option value="${_esc(p.id)}"${p.id===active?' selected':''}>${_esc(p.label)}</option>`
  ).join('');

  // Per-provider cards
  const list = document.getElementById('providers-list');
  list.innerHTML = _providerRegistry.map(p => {
    const masked = d.api_keys?.[p.id] || '';
    const inputId = `pk-${p.id}`;
    const modelId = `pm-${p.id}`;
    const selectedModel = perProvider[p.id]?.model || p.default_model;
    const opts = p.models.map(m => `<option value="${_esc(m)}"${m===selectedModel?' selected':''}>${_esc(m)}</option>`).join('');
    const status = masked ? '<span class="badge badge-success badge-xs">key set</span>' : '<span class="badge badge-ghost badge-xs">no key</span>';
    return `
      <div class="card bg-base-200 border border-base-300" data-provider="${_esc(p.id)}">
        <div class="card-body p-4">
          <div class="flex items-center justify-between gap-2 mb-2">
            <div class="flex items-center gap-2">
              <h4 class="font-semibold text-sm">${_esc(p.label)}</h4>
              ${status}
            </div>
            <a href="${_esc(p.key_url)}" target="_blank" class="link link-primary text-xs">Get key ↗</a>
          </div>
          <label class="label py-1"><span class="label-text text-xs font-medium">API Key (env: ${_esc(p.env)})</span></label>
          <div class="flex gap-2 mb-3">
            <input id="${inputId}" type="password" placeholder="${_esc(masked || p.key_placeholder)}" class="input input-sm input-bordered flex-1 font-mono" autocomplete="off" />
            <button type="button" class="btn btn-sm btn-ghost btn-square" onclick="togglePasswordVisibility('${inputId}')" aria-label="Toggle key visibility">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4"><path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
            </button>
          </div>
          <label class="label py-1"><span class="label-text text-xs font-medium">Model</span></label>
          <select id="${modelId}" class="select select-sm select-bordered w-full">${opts}</select>
        </div>
      </div>`;
  }).join('');
}

async function loadSettings() {
  try {
    const d = await apiFetch('/api/settings');
    renderProvidersUI(d);
  } catch (e) {
    console.error('Settings API not available:', e);
  }
}

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
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_keys: apiKeys,
        chat: { active_provider: active, providers },
      }),
    });
    if (res.ok) {
      showToast('Settings saved', 'success');
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
window.addEventListener('resize', () => { if(graphChart)graphChart.resize(); if(wordCloudChart)wordCloudChart.resize(); });
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

/* ── Init ──────────────────────────────────────────────────────── */
const IS_DEV_MODE = new URLSearchParams(location.search).get('dev') === 'true';
if (IS_DEV_MODE) {
  document.getElementById('dev-toolbar')?.classList.remove('hidden');
}
fetchIndexCount(); checkChatStatus(); loadFolderTree();
if (IS_DEV_MODE) {
  // Dev mode: keep the original welcome panel and require a manual Load click.
  showWelcomePanel();
} else {
  // Normal mode: auto-load the graph as soon as the DOM is ready.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadGraph);
  } else {
    loadGraph();
  }
}
