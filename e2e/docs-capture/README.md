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
the US English "kristin" voice. Nothing is sent to an online service.

The voice was chosen for its licence and for being easy to follow (checked
2026-10-21). Public domain and trained from scratch on LibriVox: kristin,
norman (male), en_GB cori (tried first; viewers found the British accent
harder to follow), john (male; built on kristin). Not usable: alan, alba, aru,
jenny_dioco, northern_english_male, vctk, joe, kusal and l2arctic are
fine-tuned from the US "lessac" voice, whose Blizzard 2013 dataset licence is
"Research Purposes only" and excludes anything based on it; semaine and ryan
are CC BY-NC-SA, l2arctic is also CC BY-NC. Piper has no Indian English voice
(the hi_IN ones are CC BY-NC-SA or lessac-based); AI4Bharat Indic Parler-TTS
(Apache-2.0, CC0/CC BY 4.0 data) has one but is gated on Hugging Face and
needs ~4 GB + PyTorch. Check the MODEL_CARD's licence AND its "finetuned
from" line before changing voice.
`run.sh` downloads both into `e2e/.cache/piper` the first time (~130 MB, voice
checked by SHA-256); spoken lines are cached in `e2e/.cache/narration`, so
re-runs only synthesise new sentences. A caption waits for the previous one to
finish being spoken, so actions written after a caption happen while it is
being said. When the written words read badly aloud, pass
`caption(page, '60–89%', { say: 'sixty to eighty-nine percent' })`.
`mux.py` mixes the lines into the MP4 at the moments their captions appeared.
`TRACEIQ_DOCS_VOICE=off` records silent videos.

The voice speaks at 1.3× its natural length (Piper `length_scale`), with
longer pauses between sentences, because the natural pace was too fast to
follow. `TRACEIQ_DOCS_VOICE_PACE=1.5` slows it further; the pace is part of
the narration cache key, so changing it re-synthesises every line.

The finished MP4 is then played back 1.25× faster (picture and voice together;
the voice keeps its pitch), because that is the speed viewers found right.
`TRACEIQ_DOCS_VIDEO_SPEED=1` keeps the recorded speed.

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

**Markdown copies.** `export_md.py` (run by `run.sh` after every capture, or by
hand: `python3 e2e/docs-capture/export_md.py .`) writes `<page>.md` next to
each guide page's HTML: the same text, the same pictures, and at the end the
page's **video script** — every spoken line, in order, read from the page's
spec in `pages/`, which it links. The HTML stays the source; the `.md` files
are regenerated, never edited.

**Working on a page.**
- `TRACEIQ_DOCS_VIDEO=off e2e/docs-capture/run.sh pages/<spec>` takes the
  pictures only (about 10 s instead of about 2 min). Record the video once
  the pictures are right.
- `e2e/docs-capture/check-page.sh <group>/<page> [shot.png]` renders the guide
  page straight from disk (no frontend rebuild) and reports console errors,
  broken pictures, missing alt text, the menu entry, the pager and horizontal
  scrolling at desktop and phone width; the optional full-page screenshot is
  for looking it over.
- Captures take turns (a file lock), so several people or agents can queue
  runs; a waiting run says so.


**Instance-admin pages** (Worker fleet, Instance settings) refuse the docs
account. `admin-capture.sh pages/<spec> …` takes the capture lock, makes the
docs account an instance admin, runs the specs, and revokes the grant on exit
(even on failure), so no other capture ever runs with it. Local stack only.

**Fresh stack / new laptop.** The docs account lives in `e2e/.auth/`
(gitignored), so a new stack gets a new one on the first `run.sh`: signup,
then `POST /demo-workspace`. The first run also downloads Piper and the voice
(~130 MB, checksum-pinned) into `e2e/.cache/`. Host needs docker, node,
python3 and ffmpeg.
