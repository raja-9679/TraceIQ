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

**Narration.** Every `caption()` is also spoken, by
[Piper](https://github.com/rhasspy/piper) (offline text-to-speech, MIT) with
the public-domain LJSpeech voice. Nothing is sent to an online service.
`run.sh` downloads both into `e2e/.cache/piper` the first time (~130 MB, voice
checked by SHA-256); spoken lines are cached in `e2e/.cache/narration`, so
re-runs only synthesise new sentences. A caption waits for the previous one to
finish being spoken, so actions written after a caption happen while it is
being said. When the written words read badly aloud, pass
`caption(page, '60–89%', { say: 'sixty to eighty-nine percent' })`.
`mux.py` mixes the lines into the MP4 at the moments their captions appeared.
`TRACEIQ_DOCS_VOICE=off` records silent videos.

The first run signs up a dedicated account ("Alex Morgan",
`docs-guide-…@example.com`) and gives it the built-in demo workspace, so
every picture shows sample data and never a real project. Credentials stay in
`e2e/.auth/` (gitignored) and are reused.

## Adding a page

1. `pages/<group>-<page>.spec.ts`: open the page, `settle()`, take
   `page.png`, an annotated `page-annotated.png` (`annotate()` draws numbered
   badges the guide's text refers to as ①②③), close-ups, and a short
   `record()`ed walkthrough with `caption()`s that say what is on screen
   (they become the spoken commentary too).
2. `frontend/public/docs/<group>/<page>.html`: copy an existing page (same
   sections: In short, What it is for, tour of the screen with numbered parts,
   video, How to…, Good to know, Related) and set `ready: true` for it in
   `frontend/public/docs/_assets/nav.js`.
3. Check every statement against the code (the page component and the API it
   calls), not against what the screen seems to show.
