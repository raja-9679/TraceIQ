# TraceIQ Browser Recorder

A minimal Manifest V3 Chrome extension that records browser interactions
(navigation, clicks, fills, selects, checkboxes, drag-and-drop, hovers, key
presses — including inside same-origin iframes) and saves them as a TraceIQ
`TestCase`. The lowest-friction way to onboard a SaaS app: record five
journeys → push to TraceIQ → run them against every AI-authored PR.

## Install (dev mode)

1. Open `chrome://extensions`.
2. Toggle **Developer mode** on (top right).
3. **Load unpacked** → select this directory.

## Use

1. Click the toolbar icon → fill **Base URL**, **API key** (from TraceIQ
   workspace settings), and a **Suite ID** to drop the case into.
2. Click **Start recording**. Drive the app normally — every click, fill,
   and key press is captured.
3. Click **Save to TraceIQ**. The extension POSTs to `/api/cases` with
   `X-API-Key` auth.

## What it records

| User action | Generated step |
|---|---|
| Page navigation while recording | `goto` with current URL |
| Click on an element | `click` with selector and `intent` |
| Typing in `<input>` / `<textarea>` | `fill` with the resulting value |
| Choosing in a `<select>` | `select-option` with the chosen value |
| Toggling a checkbox / radio | `check` / `uncheck` |
| HTML5 drag onto a drop target | `drag-and-drop` (source selector → target selector) |
| **Ctrl+Shift+H** over an element | `hover` (explicit — auto-recording hovers is too noisy) |
| Enter / Escape inside an input | `press-key` |
| Acting inside a same-origin iframe | steps are wrapped in `switch-frame` / back-to-`main` automatically at save |

Selector preference order: `#id` → `[data-testid]` → `tag[name=…]` →
`[aria-label=…]` → `text="…"` → tag. Cross-origin iframes cannot be
recorded (the browser isolates them); interactions there are skipped.

Selectors are best-effort: `data-testid` is preferred, then `id`, then a
text-content fragment, then the tag name. The `intent` field stores the
element's `aria-label` / role / visible text so the runner's semantic-
selector fallback can re-resolve if the selector breaks.

## Limitations

- Drag-and-drop, hover-only flows, and iframe interactions are not
  captured by this scaffold.
- The recorder does not yet capture network responses (planned for
  parity with the `feed-check` assertion path).
- Selector quality is heuristic. Review the generated case in TraceIQ
  before relying on it.
