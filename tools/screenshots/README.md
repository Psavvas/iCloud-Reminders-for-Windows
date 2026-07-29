# UI screenshots

Renders `src/index.html` in Chromium against a stubbed Tauri bridge, so the UI
can be reviewed without building or running the app.

```bash
npm install
npm run shoot        # writes docs/screenshots/*.png
```

`mock.js` stubs `window.__TAURI__` with data shaped like a real account: the
same 14 lists, Apple's JSON colour blobs, mixed priorities, tags, an unsynced
edit, and a conflict.

Both the mock and the temporary page are copied into `src/` only while the run
is in progress, then removed. Everything under `src/` is bundled into the app,
so neither may be left behind.

Screenshots use whatever fonts the host has. Without SF Pro or Segoe UI
Variable the type falls back, so spacing can differ slightly from Windows.

## Print layout

```bash
npm run print       # writes docs/screenshots/print-preview.pdf
```

Builds the print view through the real `@media print` stylesheet and renders it
to PDF, so the paper layout can be checked without a printer — page breaks,
grouping, and the hand-tickable boxes included.
