# Guide pictures (docs-capture)

Takes the screenshots and screen recordings used by the user guide at
`/docs/` (source: `frontend/public/docs/`). Output goes to
`frontend/public/docs/media/<group>/<page>/`, and the guide's HTML refers to
those files by name, so re-running refreshes the guide without editing it.

```bash
e2e/docs-capture/run.sh                         # every page
e2e/docs-capture/run.sh pages/today-overview    # one page
```

Needs a running stack (it finds the network of `traceiq-frontend-1`) built
from the code you are documenting: rebuild the frontend image first if the
UI changed. ffmpeg on the host converts the recordings to MP4.

The first run signs up a dedicated account ("Alex Morgan",
`docs-guide-…@example.com`) and gives it the built-in demo workspace, so
every picture shows sample data and never a real project. Credentials stay in
`e2e/.auth/` (gitignored) and are reused.

## Adding a page

1. `pages/<group>-<page>.spec.ts`: open the page, `settle()`, take
   `page.png`, an annotated `page-annotated.png` (`annotate()` draws numbered
   badges the guide's text refers to as ①②③), close-ups, and a short
   `record()`ed walkthrough with `caption()`s that say what is on screen.
2. `frontend/public/docs/<group>/<page>.html`: copy an existing page (same
   sections: In short, What it is for, tour of the screen with numbered parts,
   video, How to…, Good to know, Related) and set `ready: true` for it in
   `frontend/public/docs/_assets/nav.js`.
3. Check every statement against the code (the page component and the API it
   calls), not against what the screen seems to show.
