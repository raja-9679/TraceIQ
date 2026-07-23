# TraceIQ documentation site

`index.html` is a single-page, self-contained feature reference for the whole
platform — what every feature does and how to use it, across all six testing
pillars plus the platform, admin, and integration surface.

## Viewing it

Open it directly, or serve the folder so relative image paths resolve:

```bash
# from the repo root
python3 -m http.server 8100 --directory docs
# then open http://localhost:8100/
```

- `index.html` — the documentation page (light/dark aware, sticky sidebar TOC).
- `screenshots/` — real screenshots captured from the running app; the page
  references them with relative paths (`screenshots/*.png`).

## Regenerating the screenshots

The screenshots are captured with Playwright against a running frontend
(`npm run dev` in `frontend/`, default `http://localhost:5173`) using a logged-in
session. To refresh them, re-run the capture with a valid JWT and replace the
files in `screenshots/`. Pages with no data yet (AI Usage, Monitors on a fresh
workspace) are shown as illustrative mockups inside the page instead.

## Updating the content

Edit `index.html` directly — it has no build step and no external dependencies
(all CSS/JS is inline). Sections are plain `<section id="…">` blocks wired to the
sidebar; add a feature by copying an existing `.card` and adding a nav link.
