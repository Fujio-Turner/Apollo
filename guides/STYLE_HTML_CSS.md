# HTML & CSS Style Guide — Apollo

> Component reference: https://daisyui.com/components/
>
> Component gallery & inspiration: https://willpinha.github.io/daisy-components/

---

## 1. Golden Rules

- **No HTML emojis.** Use SVG icons (Heroicons outline, 24x24 viewBox, `stroke-width="1.5"`) for all icons.
- **DaisyUI first.** Use DaisyUI component classes before writing custom CSS. Only add custom styles when DaisyUI doesn't cover the need.
- **Tailwind utilities** for layout, spacing, and one-off tweaks. Never write raw CSS for something Tailwind covers (`flex`, `gap-2`, `p-4`, `text-xs`, etc.).
- **Separate files.** HTML structure in `index.html`, styles in `app.css`, logic in `app.js`. All live under `apollo/web/static/`.

---

## 2. File Structure

```
apollo/web/static/
├── index.html    # HTML structure only — no inline <style> or <script> blocks
├── app.css       # All custom CSS (things DaisyUI + Tailwind can't cover)
└── app.js        # All application JavaScript
```

### Rules

- `index.html` contains **only** markup and `<link>`/`<script>` tags to load external resources.
- The one exception: the Tailwind config snippet stays inline since it must run before DOM parsing.
- New features add to the existing `app.css` / `app.js` — do not create additional files unless splitting into a clearly scoped module (e.g., `chart-utils.js`).

---

## 3. CDN Stack

Load in this order in `index.html`:

```html
<!-- Fonts -->
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<!-- DaisyUI (includes Tailwind base) -->
<link href="https://cdn.jsdelivr.net/npm/daisyui@4/dist/full.min.css" rel="stylesheet">
<!-- Tailwind runtime -->
<script src="https://cdn.tailwindcss.com"></script>
<!-- ECharts -->
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts-wordcloud@2/dist/echarts-wordcloud.min.js"></script>
<!-- Markdown -->
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<!-- Syntax Highlighting -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/atom-one-dark.min.css">
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js"></script>
<!-- App -->
<link rel="stylesheet" href="/static/app.css">
<!-- app.js loaded at end of <body> -->
<script src="/static/app.js"></script>
```

Pin major versions (e.g., `daisyui@4`, `echarts@5`). Do not add new CDN dependencies without discussion.

---

## 4. Theming

### Dark / Light Mode

- Use DaisyUI's `data-theme` attribute on `<html>`: `data-theme="dark"` or `data-theme="light"`.
- Default to **dark** theme.
- Toggle with a DaisyUI `toggle` component wired to a `toggleTheme()` function.
- DaisyUI supports many themes (see the [daisy-components gallery](https://willpinha.github.io/daisy-components/) for previews): `dark`, `light`, `cupcake`, `cyberpunk`, `dracula`, `nord`, `sunset`, etc. We use `dark`/`light` but the architecture supports swapping.

### Use semantic color classes

| Use this             | Not this                |
|----------------------|-------------------------|
| `bg-base-100`        | `bg-[#1e1e1e]`          |
| `bg-base-200`        | `bg-[#252525]`          |
| `bg-base-300`        | `bg-[#2b2b2b]`          |
| `text-base-content`  | `text-[#dcddde]`        |
| `border-base-300`    | `border-[#333]`         |
| `text-primary`       | `text-[#7c3aed]`        |
| `bg-primary`         | `bg-[#7c3aed]`          |
| `text-success`       | `text-[#6bcb77]`        |
| `text-error`         | `text-[#ef4444]`        |

### When raw colors are acceptable

- **Node type colors** in the graph (e.g., `#00d9ff` for functions) — data-driven, not UI chrome.
- **Inline styles on chart elements** controlled by ECharts.
- **Theme-conditional custom CSS** using `[data-theme="dark"]` / `[data-theme="light"]` selectors for things DaisyUI doesn't cover (scrollbar thumb, code block backgrounds).

---

## 5. Typography

- **Font family:** `Inter` via Google Fonts, fallback to system sans-serif.
- **Code/monospace:** `'JetBrains Mono', 'Fira Code', 'Consolas', monospace`.
- Configure Tailwind:

```js
tailwind.config = {
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: { extend: { fontFamily: { sans: ['Inter', 'sans-serif'] } } }
};
```

### Size scale

| Context          | Class          |
|------------------|----------------|
| Page title       | `text-xl`      |
| Section header   | `text-sm`      |
| Body text        | `text-xs`      |
| Labels/captions  | `text-[10px]`  |
| Status bar       | `text-[10px]`  |

---

## 6. Layout

### Overall structure

```
body (flex row)
├── nav.nav-sidebar  (left nav, 240px / 64px collapsed)
└── div.flex-1       (main content column)
    ├── #view-*      (one visible view at a time)
    └── status-bar   (24px footer)
```

### Split pane (graph view)

- Left pane: text/detail content (default 35% width).
- Draggable handle: 5px wide, `cursor: col-resize`, highlights `bg-primary` on hover.
- Right pane: graph canvas + filter sidebar.
- Min widths: 200px per side. Clamp drag between 15%–70%.

### Collapsible sidebar

- Nav sidebar collapses from 240px to 64px (icon-only mode).
- Use CSS transition `width 0.25s ease`.
- Hide text labels via `.nav-sidebar.collapsed .nav-label { display: none; }`.

---

## 7. Components — DaisyUI Usage

Always check the [DaisyUI docs](https://daisyui.com/components/) and the [daisy-components gallery](https://willpinha.github.io/daisy-components/) for available components before building custom ones.

### Buttons

```html
<button class="btn btn-sm btn-primary">Save</button>
<button class="btn btn-sm btn-ghost btn-square">...</button>
<button class="btn btn-xs btn-ghost">Close</button>
```

- Always include a size modifier: `btn-xs`, `btn-sm`.
- Primary actions: `btn-primary`. Secondary: `btn-ghost`. Danger: `btn-error`.

### Inputs

```html
<input class="input input-sm input-bordered w-full" />
<input class="input input-sm input-bordered flex-1 font-mono" />
```

- Always `input-sm` and `input-bordered`.
- Add `font-mono` for API key / code fields.

### Selects

```html
<select class="select select-sm select-bordered">
  <optgroup label="Category">
    <option value="id">Display Name (price info)</option>
  </optgroup>
</select>
```

- Use `<optgroup>` to group related options.
- Include pricing/context in the option text for model selectors.

### Checkboxes & Toggles

```html
<input type="checkbox" class="checkbox checkbox-xs checkbox-primary" />
<input type="checkbox" class="toggle toggle-sm toggle-primary" />
```

### Collapse (accordion)

```html
<div class="collapse collapse-arrow bg-base-300 rounded-lg mb-2">
  <input type="checkbox" checked />
  <div class="collapse-title text-xs font-semibold uppercase tracking-wider py-2 min-h-0">Title</div>
  <div class="collapse-content">...</div>
</div>
```

### Badges

```html
<span class="badge badge-xs" style="background:${color};color:#000">${type}</span>
```

### Tables

```html
<table class="table table-xs table-zebra">
  <thead><tr><th>...</th></tr></thead>
  <tbody><tr><td>...</td></tr></tbody>
</table>
```

### Loading states

```html
<span class="loading loading-spinner loading-xs"></span>
<span class="loading loading-spinner loading-sm"></span>
```

### Stats

```html
<div class="stats shadow bg-base-200">
  <div class="stat">
    <div class="stat-title text-xs">Label</div>
    <div class="stat-value text-lg text-primary">42</div>
  </div>
</div>
```

### Links

```html
<a href="..." class="link link-primary">Link text</a>
```

### Form controls

```html
<div class="form-control mb-4">
  <label class="label py-1"><span class="label-text text-xs font-medium">Field</span></label>
  <input class="input input-sm input-bordered" />
</div>
```

---

## 8. Icons

Use **Heroicons Outline** (24x24, stroke-based). Inline SVG only — no icon fonts, no image files, no emoji.

```html
<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"
     stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
  <path stroke-linecap="round" stroke-linejoin="round" d="..." />
</svg>
```

- Size with Tailwind: `w-4 h-4` (16px), `w-5 h-5` (20px).
- In nav items, icons are 20x20 with `margin-right: 10px`.
- Color inherits from parent via `stroke="currentColor"`.
- Reference: https://heroicons.com/

---

## 9. Navigation Pattern

### Nav items

```html
<div class="nav-item hover:bg-base-300" data-view="view-id" onclick="switchView('view-id')">
  <svg>...</svg>
  <span class="nav-label">Label</span>
</div>
```

- Active state: add `active bg-primary text-primary-content`, remove `hover:bg-base-300`.
- `data-view` attribute ties the item to its view ID.
- Views are toggled by `switchView()` — one visible at a time, others get `display: none`.

---

## 10. Forms & Settings Pages

```html
<div class="form-control mb-4">
  <label class="label py-1"><span class="label-text text-xs font-medium">Field Name</span></label>
  <p class="text-xs opacity-40 mb-1">Helper text with <a class="link link-primary">links</a></p>
  <div class="flex gap-2">
    <input class="input input-sm input-bordered flex-1" />
    <button class="btn btn-sm btn-ghost btn-square">...</button>
  </div>
</div>
```

### Sections

```html
<div class="mb-8">
  <h3 class="text-sm font-semibold text-primary mb-4 pb-2 border-b border-base-300">Section Title</h3>
  <!-- fields -->
</div>
```

### Password fields

- Default to `type="password"`.
- Add a show/hide toggle button with the eye SVG icon.
- Use `autocomplete="off"` for API key fields.
- Display saved keys as masked values (with `\u2022` bullet chars) — never send masked values back to the server.

---

## 11. Markdown Rendering

- Use `marked.js` for all user-facing rendered content (chat responses, node details).
- Configure: `marked.setOptions({ breaks: true, gfm: true });`
- Wrap rendered content in a `<div class="md-content">`.
- Style `.md-content` elements in `app.css` with theme-aware rules.
- Code blocks use the monospace font stack and theme-aware backgrounds.

---

## 12. Toasts / Notifications

```js
function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = `toast-msg ${type === 'success' ? 'bg-success text-success-content' : 'bg-error text-error-content'}`;
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 3000);
}
```

- Position: fixed, bottom-right.
- Auto-dismiss after 3 seconds with fade transition.
- Use DaisyUI semantic colors: `bg-success`/`bg-error`.

---

## 13. CSS Architecture (`app.css`)

### What goes in `app.css`

Only write custom CSS for things DaisyUI + Tailwind can't handle:

- **Nav sidebar** collapse/expand behavior (width transitions, label hiding).
- **Split pane** handle and drag behavior.
- **Chart containers** (100% width/height for ECharts).
- **Scrollbar** styling (`::-webkit-scrollbar`).
- **Markdown content** typography (`.md-content`).
- **Toast** positioning and animation.
- **Search dropdown** absolute positioning.

### Naming

- Custom classes use kebab-case: `nav-sidebar`, `split-handle`, `filter-sidebar`.
- Section comments: `/* ── Section Name ──── */`.

### Avoid

- `!important` — fix specificity instead.
- Inline `<style>` blocks in `index.html` — put it in `app.css`.
- Inline styles — except for dynamic data-driven values (node colors, chart config).
- `z-index` wars — keep layers minimal: nav (60), search dropdown (200), toast (9999).

---

## 14. JavaScript Architecture (`app.js`)

### Structure

Organize `app.js` by section with comment headers:

```js
/* ── Markdown config ──── */
/* ── Constants ──── */
/* ── Theme ──── */
/* ── Nav ──── */
/* ── Helpers ──── */
/* ── Split Pane ──── */
/* ── View Switch ──── */
/* ── Graph ──── */
/* ── Detail Panel ──── */
/* ── Search ──── */
/* ── Word Cloud ──── */
/* ── Stats ──── */
/* ── Chat ──── */
/* ── Image Generation ──── */
/* ── Settings ──── */
/* ── Events ──── */
/* ── Init ──── */
```

### Conventions

- All functions at top level (no modules, no bundler).
- `async/await` for API calls, never raw `.then()` chains.
- DOM queries: `document.getElementById()` for known IDs, `document.querySelectorAll()` for batch operations.
- Event binding: `addEventListener` in the Events section, or `onclick` attributes for simple nav actions.
- State variables (`graphChart`, `currentGraph`, `selectedNode`, etc.) as top-level `let` declarations.
- API helper: `apiFetch(url)` wraps fetch + error check + JSON parse.

---

## 15. Accessibility (Baseline)

- All `<input>` elements must have associated `<label>` or `placeholder`.
- Interactive elements must be focusable (`<button>`, `<a>`, or `<input>`).
- Use semantic HTML where possible (`<nav>`, `<aside>`, `<main>`).
- Color is never the only indicator — pair with text labels or shape (dot + label in filters).

---

## 16. Don'ts

| Don't | Do instead |
|-------|------------|
| Use HTML emoji | Use Heroicons SVG |
| Add new CDN deps without discussion | Check if DaisyUI/Tailwind covers it |
| Hardcode dark-only colors in CSS | Use DaisyUI semantic classes |
| Put `<style>` or `<script>` in `index.html` | Use `app.css` and `app.js` |
| Use `px` for spacing | Use Tailwind spacing (`p-4`, `gap-2`) |
| Nest Tailwind `@apply` | Use utility classes directly |
| Write media queries | Use Tailwind responsive prefixes if needed |
| Build custom components from scratch | Check [DaisyUI](https://daisyui.com/components/) and [daisy-components](https://willpinha.github.io/daisy-components/) first |
