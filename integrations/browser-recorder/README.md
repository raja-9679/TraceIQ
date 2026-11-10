# TraceIQ Recorder

A Manifest V3 extension for Chrome, Edge and other Chromium browsers. The user
presses **Record** in the TraceIQ test editor, uses their site in a new tab,
and the steps come back into the editor as unsaved changes to review and save.

User-facing instructions are in the guide: `/docs/tests/suites.html#record`.

## How it fits together

```
TraceIQ test editor  --window.postMessage-->  content.js  --runtime message-->  background.js
(RecorderDialog.tsx,                          (bridge, recording,               (owns the steps,
 lib/recorder.ts)     <--------- steps ------  toolbar)        <--- state ---    trusted origins)
```

- **No API key, no network.** The extension never calls a server. It hands
  the steps to the editor tab that started the recording; saving them is the
  signed-in user's own action, under their own role.
- **Only approved origins may start a recording.** The steps contain what the
  user typed, so the first `start` from an origin opens `approve.html`, and the
  answer is stored in `chrome.storage.local` (`tiq_trusted`). The origin comes
  from the `sender` the browser reports, never from the message. The popup
  lists the approved origins with *Remove*.
- **Passwords never leave the page.** A `type=password` field is recorded as
  `{{secret.<FIELD NAME>}}`; the editor tells the user which secrets to store.
- **Only the tab the recorder opened is recorded**, plus tabs a link in it
  opens (`state.tabs`; a tab the user opens by hand starts on the browser's
  new-tab page and is left out). Steps from any other tab are refused by the
  service worker, and the toolbar is always visible in a recorded tab.
- **Steps are handed over only to the origin that asked**: the content script
  checks the tab is still on that origin before posting them to the page.
- **One approval window at a time**, so a page cannot stack them.
- `background.js` owns the step list (in `chrome.storage.session`, since the
  service worker is stopped when idle) and applies changes one at a time, so
  frames of one page cannot overwrite each other.
- The editor rebuilds every step it receives field by field and drops any type
  the recorder does not produce (`lib/recorder.ts:cleanRecordedSteps`).

## What it records

| User action | Step |
|---|---|
| First page, or an address typed during the recording | `goto` |
| Click (on the control, not the icon inside it) | `click` |
| Typing in a field (one step per field) | `fill` |
| Choosing in a `<select>` | `select-option` |
| Ticking / unticking a checkbox or radio | `check` / `uncheck` |
| HTML5 drag onto a drop target | `drag-and-drop` |
| Enter in a field, Escape | `press-key` |
| **Ctrl+Shift+H** over an element | `hover` |
| Acting inside a same-origin iframe | wrapped in `switch-frame` steps |
| Toolbar → **Add check** | `expect-visible`, `expect-text`, `expect-url`, `expect-title` |

Not recorded: file uploads, right-clicks, native dialogs, cross-origin
iframes, `contenteditable` editors. A double-click becomes two clicks.

## Selectors (`selectors.js`)

In order, each kept only if it matches exactly one element: test id
(`data-testid`, `data-test`, `data-cy`, `data-qa`) → hand-written `id`
(framework-generated ids such as `:r1:` are skipped) → `name` / `aria-label` /
`placeholder` / `title` / `alt` → `text="…"` → `a[href="…"]` → text scoped to
the nearest ancestor with an id or test id (`#menu >> text="Docs"`) → CSS path
anchored on that ancestor.

`text="…"` is counted the way Playwright resolves it (the smallest element
with exactly that text). A tag-scoped form such as `button:text-is("Sign in")`
matches nothing when the text sits in a `<span>` inside the button — the test
replays every recorded selector through Playwright to catch that class of
mistake.

## Develop

Load unpacked from this folder (`chrome://extensions` → Developer mode).

```bash
integrations/browser-recorder/test/run.sh    # real Chromium with the extension loaded (needs Docker)
integrations/browser-recorder/pack.py        # rebuild frontend/public/recorder/traceiq-recorder.zip
integrations/browser-recorder/pack.py --check
```

**After changing any file here, run `pack.py` and commit the zip** — the app
serves it at `/recorder/traceiq-recorder.zip`, and the frontend image is built
from `frontend/` alone. The guide's pictures come from
`e2e/docs-capture/pages/tests-suites-recorder.spec.ts`.

## Publishing to the Chrome Web Store

Not done yet (needs the owner's developer account). Upload the zip `pack.py`
writes. For the review form: `host_permissions` is all sites because the user
chooses which site to record; `scripting` is used once, at install, to reach
tabs that were already open; `storage` holds the approved origins and the
recording in progress; no data is sent anywhere by the extension. Once it is
listed, build the frontend with `VITE_RECORDER_STORE_URL=<listing URL>` and the
editor shows "Add TraceIQ Recorder to Chrome" instead of the manual steps.

The earlier version of this extension posted cases straight to `/api/cases`
with a pasted API key. That mode is gone: a long-lived key sat in extension
storage, and the case was saved without review.
