// Service worker: owns the recording.
//
// Content scripts only report what happened; the list of steps lives here (in
// chrome.storage.session, because a service worker is stopped whenever it is
// idle). One owner means two frames of the same page cannot overwrite each
// other's steps.
//
// A recording is always started by a TraceIQ page (the test editor) and its
// steps go back to that same tab, so the extension holds no API key and never
// talks to a server itself. Because the steps include what the user typed,
// only origins the user has approved may start one — see `trusted`.

const VERSION = chrome.runtime.getManifest().version;
const MAX_STEPS = 500;
// A page load this soon after a recorded action was caused by it (a link, a
// form submit), so it is not a step of its own.
const NAV_QUIET_MS = 5000;
// An unanswered approval window blocks a second one for this long (closing it
// counts as "no" and clears it sooner).
const APPROVAL_WAIT_MS = 60000;

const EMPTY = {
    recording: false, paused: false, mode: null, steps: [],
    appTabId: null, appOrigin: null, lastStepAt: 0,
    tabs: [],             // the tab the recorder opened, and tabs opened from it
    pending: null,        // finished steps the app has not confirmed yet
    request: null,        // a start waiting for the user's approval
};

// ---- state ---------------------------------------------------------------

async function load() {
    const { tiq = EMPTY } = await chrome.storage.session.get('tiq');
    return { ...EMPTY, ...tiq };
}

let queue = Promise.resolve();
// Every change goes through here, one at a time.
function mutate(fn) {
    const run = queue.then(async () => {
        const state = await load();
        const result = await fn(state);
        await chrome.storage.session.set({ tiq: state });
        await broadcast(state);
        return result;
    });
    queue = run.catch(() => undefined);
    return run;
}

function viewFor(state, tabId) {
    return {
        recording: state.recording,
        paused: state.paused,
        mode: state.mode,
        count: state.steps.length,
        // Only the tab the recorder opened (and tabs opened from it) is
        // recorded. Whatever else is open — mail, a bank — is left alone, and
        // so is the TraceIQ tab even when the site under test shares its host.
        inRecording: tabId != null && state.tabs.includes(tabId),
    };
}

async function broadcast(state) {
    const tabs = await chrome.tabs.query({});
    await Promise.all(tabs.map((tab) =>
        chrome.tabs.sendMessage(tab.id, { action: 'state', state: viewFor(state, tab.id) }).catch(() => undefined)));
}

async function trusted() {
    const { tiq_trusted = [] } = await chrome.storage.local.get('tiq_trusted');
    return tiq_trusted;
}

// ---- steps ---------------------------------------------------------------

// Steps recorded inside an iframe carry `_frame` (its selector in the parent
// page). The worker needs explicit `switch-frame` steps instead, including
// one back to the main page.
function weaveFrameSwitches(steps) {
    const out = [];
    let current = '';
    for (const step of steps) {
        const frame = step._frame || '';
        if (frame !== current) {
            out.push({
                id: crypto.randomUUID(),
                type: 'switch-frame',
                selector: frame || 'main',
                intent: frame ? `Enter iframe ${frame}` : 'Return to the main page',
            });
            current = frame;
        }
        const { _frame, ...clean } = step;
        out.push(clean);
    }
    return out;
}

function addStep(state, step) {
    if (!state.recording || state.paused || state.steps.length >= MAX_STEPS) return;
    state.steps.push(step);
    state.lastStepAt = Date.now();
}

// ---- talking to the TraceIQ tab -----------------------------------------

// `origin` travels with the message and the content script posts it only if
// the tab is still on that origin: the editor's tab may since have been taken
// to another site, and the steps hold what the user typed.
function toApp(tabId, origin, message) {
    if (tabId == null) return Promise.resolve();
    return chrome.tabs.sendMessage(tabId, { action: 'to-app', origin, message }, { frameId: 0 }).catch(() => undefined);
}

async function begin(state, { origin, tabId, url }) {
    Object.assign(state, {
        recording: true, paused: false, mode: null, steps: [],
        appTabId: tabId, appOrigin: origin, lastStepAt: 0, pending: null, request: null, tabs: [],
    });
    // Open next to the editor. Without windowId the tab would go to whichever
    // window has focus — the approval popup, the first time.
    const appTab = await chrome.tabs.get(tabId);
    const tab = await chrome.tabs.create({ url, windowId: appTab.windowId, openerTabId: tabId });
    state.tabs = [tab.id];
    await toApp(tabId, origin, { type: 'started' });
}

async function finish(state) {
    if (!state.recording) return;
    state.recording = false;
    state.paused = false;
    state.mode = null;
    state.pending = weaveFrameSwitches(state.steps);
    state.steps = [];
    state.tabs = [];
    await deliver(state);
    if (state.appTabId != null) {
        // Bring the editor back to the front; the tab may be gone, in which
        // case the steps wait in `pending` for the next editor that asks.
        const tab = await chrome.tabs.update(state.appTabId, { active: true }).catch(() => null);
        if (tab) await chrome.windows.update(tab.windowId, { focused: true }).catch(() => undefined);
    }
}

function deliver(state) {
    if (!state.pending || !state.pending.length) return Promise.resolve();
    return toApp(state.appTabId, state.appOrigin, { type: 'steps', steps: state.pending });
}

function httpUrl(value) {
    try {
        const u = new URL(String(value));
        return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
    } catch {
        return null;
    }
}

// Messages relayed from a web page. `sender` is filled in by the browser, so
// the origin cannot be forged by the page.
async function fromPage(msg, sender) {
    if (!sender.tab || sender.frameId !== 0) return { type: 'error', error: 'Not a top-level page.' };
    const origin = new URL(sender.url).origin;
    const tabId = sender.tab.id;
    const isTrusted = (await trusted()).includes(origin);

    if (msg.type === 'ping') {
        const state = await load();
        const mine = isTrusted && state.appOrigin === origin;
        return {
            type: 'pong', version: VERSION, trusted: isTrusted,
            recording: mine && state.recording,
            pending: mine && state.pending ? state.pending.length : 0,
        };
    }

    if (msg.type === 'start') {
        const url = httpUrl(msg.url);
        if (!url) return { type: 'error', error: 'The address must start with http:// or https://.' };
        return mutate(async (state) => {
            if (state.recording) return { type: 'error', error: 'A recording is already in progress. Finish or cancel it first.' };
            if (isTrusted) {
                await begin(state, { origin, tabId, url });
                return { type: 'starting' };
            }
            // One question at a time: a page must not be able to stack
            // approval windows until one is clicked by mistake.
            if (state.request && Date.now() - state.request.at < APPROVAL_WAIT_MS) return { type: 'needs-approval' };
            state.request = { origin, tabId, url, at: Date.now() };
            await chrome.windows.create({ url: chrome.runtime.getURL('approve.html'), type: 'popup', width: 460, height: 380 });
            return { type: 'needs-approval' };
        });
    }

    // Everything below acts on an existing recording, so it must come from
    // the origin that started it.
    if (!isTrusted) return { type: 'error', error: 'This site is not allowed to use the recorder.' };
    return mutate(async (state) => {
        if (state.appOrigin !== origin) return { type: 'error', error: 'No recording belongs to this site.' };
        if (msg.type === 'collect') {
            state.appTabId = tabId; // the editor may have been reopened in another tab
            await deliver(state);
            return { type: 'ok' };
        }
        if (msg.type === 'received') {
            state.pending = null;
            return { type: 'ok' };
        }
        if (msg.type === 'cancel') {
            Object.assign(state, { recording: false, paused: false, mode: null, steps: [], pending: null, tabs: [] });
            return { type: 'ok' };
        }
        return { type: 'error', error: 'Unknown request.' };
    });
}

// ---- message router ------------------------------------------------------

const handlers = {
    'get-state': async (_msg, sender) => viewFor(await load(), sender.tab ? sender.tab.id : null),

    page: (msg, sender) => fromPage(msg.message || {}, sender),

    step: (msg, sender) => mutate((state) => {
        if (sender.tab && state.tabs.includes(sender.tab.id)) addStep(state, msg.step);
    }),

    'update-step': (msg) => mutate((state) => {
        const last = state.steps[state.steps.length - 1];
        if (state.recording && !state.paused && last && last.id === msg.id) last.value = msg.value;
    }),

    // Only the first page, or one the user went to by hand, becomes a step.
    navigated: (msg) => mutate((state) => {
        if (!state.recording || state.paused) return;
        const quiet = Date.now() - state.lastStepAt > NAV_QUIET_MS;
        const lastGoto = [...state.steps].reverse().find((s) => s.type === 'goto');
        if (state.steps.length && (!quiet || (lastGoto && lastGoto.value === msg.url))) return;
        addStep(state, { id: crypto.randomUUID(), type: 'goto', value: msg.url, intent: `Open ${msg.url}` });
    }),

    undo: () => mutate((state) => { if (state.recording) state.steps.pop(); }),
    'set-mode': (msg) => mutate((state) => {
        if (state.recording) state.mode = ['visible', 'text'].includes(msg.mode) ? msg.mode : null;
    }),
    pause: (msg) => mutate((state) => {
        if (state.recording) { state.paused = msg.paused === true; state.mode = null; }
    }),
    finish: () => mutate(finish),
    cancel: () => mutate((state) => {
        Object.assign(state, { recording: false, paused: false, mode: null, steps: [], tabs: [] });
    }),

    // approve.html and popup.html (extension pages only — see the guard below)
    'approval-info': async () => (await load()).request,
    approve: (msg) => mutate(async (state) => {
        const request = state.request;
        state.request = null;
        if (!request) return;
        if (!msg.allow) return toApp(request.tabId, request.origin, { type: 'denied' });
        const list = await trusted();
        if (!list.includes(request.origin)) await chrome.storage.local.set({ tiq_trusted: [...list, request.origin] });
        await begin(state, request);
    }),
    'popup-info': async () => {
        const state = await load();
        return {
            recording: state.recording, paused: state.paused, count: state.steps.length,
            pending: state.pending, trusted: await trusted(), version: VERSION,
        };
    },
    untrust: async (msg) => {
        await chrome.storage.local.set({ tiq_trusted: (await trusted()).filter((o) => o !== msg.origin) });
    },
};

const EXTENSION_ONLY = new Set(['approval-info', 'approve', 'popup-info', 'untrust']);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    const handler = handlers[msg && msg.action];
    if (!handler) return false;
    // A content script runs inside an arbitrary site; it must not be able to
    // approve an origin on the user's behalf.
    if (EXTENSION_ONLY.has(msg.action) && sender.tab && !String(sender.url).startsWith(chrome.runtime.getURL(''))) {
        sendResponse(undefined);
        return false;
    }
    Promise.resolve(handler(msg, sender)).then(sendResponse, (err) => sendResponse({ type: 'error', error: String(err && err.message || err) }));
    return true;
});

// Closing the TraceIQ tab mid-recording leaves nowhere to send the steps
// right now; keep recording, and `collect` re-attaches a reopened editor.
chrome.tabs.onRemoved.addListener((tabId) => {
    mutate((state) => {
        if (state.appTabId === tabId) state.appTabId = null;
        state.tabs = state.tabs.filter((id) => id !== tabId);
    });
});

// A link that opens a new tab continues the journey there. A tab the user
// opens by hand also names the current tab as its opener, but starts on the
// browser's own new-tab page rather than a web address — that one is theirs.
chrome.tabs.onCreated.addListener(async (tab) => {
    if (tab.openerTabId == null) return;
    // The address is often not known yet at the moment the tab is created.
    let url = tab.pendingUrl || tab.url || '';
    for (let i = 0; i < 10 && !url; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        const now = await chrome.tabs.get(tab.id).catch(() => null);
        if (!now) return;
        url = now.pendingUrl || now.url || '';
    }
    if (!/^https?:/.test(url)) return;
    mutate((state) => {
        if (state.recording && state.tabs.includes(tab.openerTabId) && !state.tabs.includes(tab.id)) state.tabs.push(tab.id);
    });
});

// Chrome does not put a new extension's content scripts into tabs that are
// already open, so the editor tab that told the user to install the recorder
// would not see it until reloaded. Inject into open pages once, at install.
chrome.runtime.onInstalled.addListener(async () => {
    const tabs = await chrome.tabs.query({ url: ['http://*/*', 'https://*/*'] });
    for (const tab of tabs) {
        chrome.scripting.executeScript({
            target: { tabId: tab.id, allFrames: true },
            files: ['selectors.js', 'content.js'],
        }).catch(() => undefined); // pages the browser protects (the store, chrome://)
    }
});
