# Adding a New File-Type Icon (Folder Tree Badge)

> Companion to [STYLE_HTML_CSS.md](./STYLE_HTML_CSS.md). Read that first for general HTML/CSS conventions.

The sidebar folder tree shows a small colored badge next to each file (e.g. yellow `JS`, blue `TS`, red `PDF`). Badges are **inline-styled spans**, not image files — so adding a new type means adding one entry to a JS map, **not** creating an SVG/PNG.

---

## 1. Where the Badges Live

| Concern | File | Symbol |
|---------|------|--------|
| Map of extension → label/color | `apollo/web/static/app.js` | `FILE_TYPE_BADGES` |
| Lookup helper | `apollo/web/static/app.js` | `fileBadgeHtml(name)` |
| Visual styling | `apollo/web/static/app.css` | `.file-badge` |

The badge is rendered by `renderTreeNode()` for every file in the tree. Folders use a Heroicon SVG (`_FOLDER_SVG`) and never go through the badge map.

---

## 2. Anatomy of a Badge Entry

```js
ext: { label: 'XXX', color: '#rrggbb', fg: '#000' /* optional */ },
```

| Field   | Required | Description                                                                                        |
|---------|----------|----------------------------------------------------------------------------------------------------|
| key     | yes      | Lowercased extension **without leading dot** (e.g. `js`, `tsx`, `docx`). Special cases like `dockerfile` use the bare filename. |
| `label` | yes      | 1–3 character text shown inside the badge. Must be uppercase-ready (CSS `text-transform: uppercase`). |
| `color` | yes      | Background hex color. See §4 for color rules.                                                       |
| `fg`    | optional | Foreground (text) color. Defaults to `#fff`. Set to `#000` when the background is light/yellow.     |

### Example

```js
graphql: { label: 'GQL', color: '#e10098' },
prisma:  { label: 'PRI', color: '#0c344b' },
ipynb:   { label: 'NB',  color: '#f37626', fg: '#000' },
```

---

## 3. Naming Standards

### Label rules

1. **1–3 characters max.** Anything longer overflows the 18×14 px box.
2. **Match the de facto community shorthand** when one exists:
   - Languages → official short name (`JS`, `TS`, `PY`, `GO`, `RS`, `RB`).
   - Microsoft Office → single-letter brand glyph (`W`, `X`, `P`).
   - Image formats → `IMG` for raster bitmaps, keep `SVG` distinct.
   - Configs → `YML`, `TML`, `XML`, `ENV`.
3. **Use `{}` for JSON** (it's the established convention; matches VSCode).
4. **Avoid emojis or non-ASCII glyphs.** Per `STYLE_HTML_CSS.md` §1.
5. **No punctuation or whitespace** in the label.

### Key rules

- Always **lowercase, no leading dot**.
- One alias per language family — point both `js`/`mjs`/`cjs` at the same label `JS` rather than inventing new labels.
- For extension-less files matched by name (e.g. `Dockerfile`, `Makefile`, `Procfile`), match in `fileBadgeHtml()` using a `lower === 'name'` branch and use the **lowercased filename** as the key.

---

## 4. Color Standards

### Source of truth

Use the language/tool's **official brand color**. When uncertain, copy from:

- https://github.com/ozh/github-colors/blob/master/colors.json (used by GitHub linguist)
- The product's own brand guidelines (Office, Adobe, etc.).

### Contrast & readability

- Default text is white (`#fff`). If the background is **lighter than ~50% luminance** (yellow, light cyan, beige), set `fg: '#000'` so the label stays readable.
  - Quick check: any color where the JS Yellow rule applies — `#f7df1e`, `#ffb13b`, `#ecd53f`, `#cbcb41`, `#61dafb`, `#dea584` — uses `fg: '#000'`.
- Avoid pure `#000` backgrounds (badge disappears on dark mode borders) — use `#222` or `#555` instead.

### Family grouping

Group related types under one color so the tree reads at a glance:

| Family                  | Color base   | Examples                                |
|-------------------------|--------------|-----------------------------------------|
| Office documents        | brand color  | `W` blue `#2b579a`, `X` green `#217346` |
| Shell scripts           | `#4eaa25`    | `sh`, `bash`, `zsh`                     |
| Images (raster)         | `#a259ff`    | `png`, `jpg`, `jpeg`, `gif`, `webp`     |
| Archives                | `#8a8a8a`    | `zip`, `tar`, `gz`                      |
| YAML / TOML configs     | red/brown    | `yml`/`yaml` `#cb171e`, `toml` `#9c4221`|
| Plain text / logs       | gray `#666`  | `txt`, `log`, `lock`                    |

If you're adding a new family member, **reuse the family color** instead of inventing a new one.

---

## 5. Step-by-Step: Adding a New Type

1. Open `apollo/web/static/app.js`.
2. Locate the `FILE_TYPE_BADGES` map (just above `fileBadgeHtml`).
3. Add an entry in alphabetical-ish order within its family:
   ```js
   graphql: { label: 'GQL', color: '#e10098' },
   ```
4. If the file has **no extension** but a well-known name, add a name branch in `fileBadgeHtml()`:
   ```js
   if (lower === 'makefile') key = 'makefile';
   ```
   …and add `makefile: { label: 'MK', color: '#427819' }` to the map.
5. Reload the web app — no build step. Verify in the sidebar tree that the badge appears with the right label and color.
6. Eyeball it in **both light and dark theme** (toggle via the moon/sun nav button). Adjust `fg` if contrast is poor in either theme.

---

## 6. When a Badge Is Not Enough

A text badge is the default. Only escalate to an SVG icon if **all** of these are true:

- The badge label can't represent the type clearly in 1–3 chars.
- The format has a universally recognizable visual mark (e.g. Figma's `F`, Photoshop's `Ps`).
- Adding the SVG won't bloat `app.js` past ~2 kB.

If you decide to use an SVG:

1. Use **Heroicons Outline** style or a single-color brand glyph — keep it monochrome so it inherits `currentColor`.
2. Inline the SVG in `app.js` next to the badge map (do **not** add image files under `static/`). Per `STYLE_HTML_CSS.md` §8.
3. Render with the same `.file-badge` size box (18×14) for alignment.
4. Document the source/license of the brand mark in a comment above the SVG constant.

---

## 7. Don'ts

| Don't                                                | Do instead                                                  |
|------------------------------------------------------|-------------------------------------------------------------|
| Add `.png`/`.svg` files to `web/static/` for badges  | Add an entry to `FILE_TYPE_BADGES` in `app.js`              |
| Use 4+ character labels                              | Pick the canonical 2–3 letter shorthand                     |
| Invent new colors when the language has a brand color| Look it up in github-colors or the official brand guide     |
| Use white text on yellow/light backgrounds           | Add `fg: '#000'` to the entry                               |
| Add emojis to labels                                 | Use ASCII letters only (per `STYLE_HTML_CSS.md` §1)         |
| Hardcode badge styling inline anywhere else          | Reuse the `.file-badge` class in `app.css`                  |

---

## 8. Testing Checklist

Before merging a new file-type entry:

- [ ] Label is ≤ 3 ASCII characters.
- [ ] Color is a recognizable brand color (or matches an existing family).
- [ ] Contrast passes the eye-test in both `dark` and `light` themes.
- [ ] Extension key is lowercase with no leading dot.
- [ ] If the file is name-matched (no extension), `fileBadgeHtml()` has the branch.
- [ ] Reloaded the web app and confirmed the badge renders for a real file of that type.
