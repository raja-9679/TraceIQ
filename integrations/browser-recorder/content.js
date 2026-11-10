// Content script: reports what the user does to the service worker, shows the
// recording toolbar, and relays messages between a TraceIQ page and the
// extension.
//
// It keeps no list of steps — background.js owns that. Runs in every
// same-origin frame; a step recorded inside an iframe is tagged `_frame` and
// the service worker turns the tags into `switch-frame` steps at the end.
// Cross-origin iframes cannot be recorded (the browser hides
// window.frameElement), so interactions inside them are skipped.

(function () {
    const R = globalThis.__traceiqRecorder;
    const isTop = window === window.top;

    let frameSelector = '';
    if (!isTop) {
        try {
            const fe = window.frameElement; // null / throws when cross-origin
            if (!fe) return;
            frameSelector = R.selectorFor(fe);
        } catch {
            return;
        }
    }

    let state = { recording: false, paused: false, mode: null, count: 0, inRecording: false };
    // After the extension is updated or removed, this copy of the script is
    // left behind in open tabs and chrome.runtime throws; it must stay quiet.
    const send = (message) => {
        try {
            return chrome.runtime.sendMessage(message).catch(() => undefined);
        } catch {
            return Promise.resolve(undefined);
        }
    };
    // Only in the tab the recorder opened (the service worker decides which).
    const active = () => state.recording && !state.paused && state.inRecording;

    // ---- bridge: TraceIQ page <-> extension (top frame only) -------------
    //
    // The page talks with window.postMessage; this script is the only part of
    // the extension it can reach. Nothing is trusted here: the service worker
    // decides what an origin may do, from the sender the browser reports.
    function toPage(message) {
        window.postMessage({ source: 'traceiq-recorder', ...message }, location.origin);
    }
    if (isTop) {
        window.addEventListener('message', (ev) => {
            if (ev.source !== window || ev.origin !== location.origin) return;
            const data = ev.data;
            if (!data || data.source !== 'traceiq-app' || typeof data.type !== 'string') return;
            send({ action: 'page', message: { type: data.type, url: data.url } })
                .then((reply) => { if (reply) toPage({ ...reply, replyTo: data.type }); });
        });
    }

    chrome.runtime.onMessage.addListener((msg) => {
        if (msg.action === 'state') applyState(msg.state);
        // Steps are for the site that asked; this tab may have moved on.
        if (msg.action === 'to-app' && isTop && msg.origin === location.origin) toPage(msg.message);
    });

    function applyState(next) {
        const wasActive = active();
        state = next;
        if (!active() || !state.mode) clearHighlight();
        if (isTop) renderToolbar();
        if (!wasActive && active()) {
            lastFill = null;
            // The tab can load before the service worker has stored the new
            // recording, in which case the first answer said "not recording"
            // and the opening page was never reported.
            if (isTop && state.count === 0) send({ action: 'navigated', url: location.href });
        }
    }

    send({ action: 'get-state' }).then((s) => {
        if (!s) return;
        const already = active();
        applyState(s);
        // A page loaded in the middle of a recording (applyState reports the
        // very first one itself).
        if (isTop && active() && !already && state.count > 0) send({ action: 'navigated', url: location.href });
    });

    // ---- recording -------------------------------------------------------

    let lastFill = null; // { el, id } — typing in one field is one step

    function record(type, extras) {
        const step = { id: R.newId(), type, ...(frameSelector ? { _frame: frameSelector } : {}), ...extras };
        lastFill = null;
        send({ action: 'step', step });
        return step;
    }

    const inToolbar = (ev) => !!toolbarHost && ev.composedPath().includes(toolbarHost);
    // Events made by the page's own scripts (dispatchEvent, el.click()) are
    // not the user: a recorded page must not be able to write steps.
    const fromUser = (ev) => ev.isTrusted === true;

    // Remember every field that stops being a password field ("show
    // password"), including ones the user has not touched yet.
    new MutationObserver((records) => {
        for (const r of records) {
            if (r.oldValue === 'password' && r.target instanceof HTMLInputElement) R.everPassword.add(r.target);
        }
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ['type'], attributeOldValue: true });
    const elementOf = (ev) => (ev.target instanceof Element ? ev.target : null);

    // -- picking an element for a check ("Add check" on the toolbar) --------
    let highlight = null;
    function clearHighlight() {
        if (highlight) highlight.remove();
        highlight = null;
    }
    function showHighlight(el) {
        if (!highlight) {
            highlight = document.createElement('div');
            highlight.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;border:2px solid #7c5cff;background:rgba(124,92,255,.12);border-radius:4px;';
            document.documentElement.appendChild(highlight);
        }
        const r = el.getBoundingClientRect();
        Object.assign(highlight.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
    }
    const picking = () => active() && !!state.mode;

    document.addEventListener('mousemove', (ev) => {
        if (!picking() || inToolbar(ev)) return clearHighlight();
        const el = elementOf(ev);
        if (el) showHighlight(el);
    }, true);

    // While picking, the page must not react to the click that chooses the
    // element — otherwise "check this button is visible" would also press it.
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
        document.addEventListener(type, (ev) => {
            if (picking() && !inToolbar(ev)) { ev.preventDefault(); ev.stopImmediatePropagation(); }
        }, true);
    }

    function recordCheck(el) {
        const selector = R.selectorFor(el);
        const name = R.intentFor(el);
        if (state.mode === 'text') {
            const text = R.textOf(el).slice(0, 120);
            if (text) {
                record('expect-text', { selector, value: text, intent: `"${text}" is shown` });
                return;
            }
        }
        record('expect-visible', { selector, intent: `${name} is visible` });
    }

    // -- clicks -------------------------------------------------------------
    let enterAt = 0; // Enter in a field also "clicks" the form's submit button

    document.addEventListener('click', (ev) => {
        if (inToolbar(ev) || !fromUser(ev)) return;
        const raw = elementOf(ev);
        if (!raw) return;
        if (picking()) {
            ev.preventDefault();
            ev.stopImmediatePropagation();
            clearHighlight();
            recordCheck(raw);
            send({ action: 'set-mode', mode: null });
            return;
        }
        if (!active()) return;
        // The browser turns Enter in a form field into a click on the submit
        // button; the key press is already recorded.
        if (ev.detail === 0 && Date.now() - enterAt < 300) return;
        const el = R.clickTarget(raw);
        // These have their own step (select-option, check, fill), and
        // clicking a label is only a way of reaching its control.
        if (el instanceof HTMLSelectElement || R.isTextField(el)) return;
        if (el instanceof HTMLInputElement && ['checkbox', 'radio', 'file', 'range', 'color'].includes(el.type)) return;
        if (el instanceof HTMLLabelElement && el.control) return;
        record('click', { selector: R.selectorFor(el), intent: R.intentFor(el) });
    }, true);

    // -- selects, checkboxes, radios, sliders -------------------------------
    document.addEventListener('change', (ev) => {
        if (!active() || inToolbar(ev) || !fromUser(ev)) return;
        const el = ev.target;
        if (el instanceof HTMLSelectElement) {
            record('select-option', { selector: R.selectorFor(el), value: el.value, intent: `Select "${el.value}" in ${R.intentFor(el)}` });
        } else if (el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio')) {
            record(el.checked ? 'check' : 'uncheck', { selector: R.selectorFor(el), intent: R.intentFor(el) });
        } else if (el instanceof HTMLInputElement && (el.type === 'range' || el.type === 'color')) {
            record('fill', { selector: R.selectorFor(el), value: el.value, intent: R.intentFor(el) });
        }
    }, true);

    // -- typing ---------------------------------------------------------------
    document.addEventListener('input', (ev) => {
        if (!active() || inToolbar(ev) || !fromUser(ev)) return;
        const el = ev.target;
        if (!R.isTextField(el)) return;
        const value = R.fillValue(el);
        if (lastFill && lastFill.el === el) {
            send({ action: 'update-step', id: lastFill.id, value });
            return;
        }
        const step = record('fill', {
            selector: R.selectorFor(el), value, intent: R.intentFor(el),
            ...(R.isSecretField(el) ? { secret: true } : {}),
        });
        lastFill = { el, id: step.id };
    }, true);

    // -- drag and drop (HTML5) ----------------------------------------------
    let dragSource = null;
    document.addEventListener('dragstart', (ev) => {
        if (active() && fromUser(ev)) dragSource = elementOf(ev);
    }, true);
    document.addEventListener('drop', (ev) => {
        const target = elementOf(ev);
        if (!active() || !fromUser(ev) || !dragSource || !target) return;
        record('drag-and-drop', {
            selector: R.selectorFor(dragSource),
            value: R.selectorFor(target),
            intent: `Drag ${R.intentFor(dragSource)} onto ${R.intentFor(target)}`,
        });
        dragSource = null;
    }, true);

    // -- keys ---------------------------------------------------------------
    // Hovering is constant, so it is only recorded on request: Ctrl+Shift+H
    // over the element.
    let lastHover = null;
    document.addEventListener('mouseover', (ev) => { lastHover = elementOf(ev); }, true);

    document.addEventListener('keydown', (ev) => {
        if (!active() || inToolbar(ev) || !fromUser(ev)) return;
        if (ev.key === 'Escape' && state.mode) {
            send({ action: 'set-mode', mode: null });
            ev.preventDefault();
            return;
        }
        if (ev.ctrlKey && ev.shiftKey && ev.key.toLowerCase() === 'h') {
            if (lastHover) record('hover', { selector: R.selectorFor(lastHover), intent: `Hover ${R.intentFor(lastHover)}` });
            ev.preventDefault();
            return;
        }
        const inField = R.isTextField(ev.target) || ev.target instanceof HTMLSelectElement;
        if (ev.key === 'Enter' && inField && !(ev.target instanceof HTMLTextAreaElement)) {
            enterAt = Date.now();
            record('press-key', { value: 'Enter', intent: 'Press Enter' });
        } else if (ev.key === 'Escape') {
            record('press-key', { value: 'Escape', intent: 'Press Escape' });
        }
    }, true);

    // ---- toolbar (top frame) ---------------------------------------------
    //
    // Always on screen while this tab is being recorded, so recording can
    // never happen unseen. It lives in a shadow root: the page's CSS
    // cannot restyle it and its own clicks are not recorded.
    let toolbarHost = null;
    let toolbarRoot = null;
    let checkMenuOpen = false;

    const CSS_TEXT = `
        :host { all: initial; }
        .bar { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
               max-width: min(560px, calc(100vw - 32px)); padding: 8px 10px; border-radius: 16px; background: #fff; color: #1c1b22;
               border: 1px solid #e4e1d9; box-shadow: 0 12px 40px rgba(28,27,34,.18); font: 13px/1.3 system-ui, sans-serif; }
        .status { display: flex; align-items: center; gap: 6px; padding: 0 6px; font-weight: 600; }
        .dot { width: 9px; height: 9px; border-radius: 50%; background: #e0603a; }
        .paused .dot { background: #b8b4a8; }
        button { font: inherit; cursor: pointer; border-radius: 999px; border: 1px solid #e4e1d9; background: #fff; color: #1c1b22; padding: 6px 12px; }
        button:hover { background: #f6f4ee; }
        button.primary { background: #1c1b22; color: #fff; border-color: #1c1b22; }
        button.primary:hover { background: #33313c; }
        button[aria-pressed="true"] { background: #ece7ff; border-color: #c9bdff; }
        .hint { flex-basis: 100%; padding: 2px 6px; color: #6b6878; }
    `;

    function button(label, onClick, extra = {}) {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        if (extra.primary) b.className = 'primary';
        if (extra.pressed !== undefined) b.setAttribute('aria-pressed', String(extra.pressed));
        b.addEventListener('click', onClick);
        return b;
    }

    function renderToolbar() {
        const show = state.recording && state.inRecording;
        if (!show) {
            if (toolbarHost) toolbarHost.remove();
            toolbarHost = null;
            toolbarRoot = null;
            checkMenuOpen = false;
            return;
        }
        if (!toolbarHost || !toolbarHost.isConnected) {
            toolbarHost = document.createElement('traceiq-recorder-bar');
            toolbarRoot = toolbarHost.attachShadow({ mode: 'open' });
            document.documentElement.appendChild(toolbarHost);
        }
        if (state.mode) checkMenuOpen = false;

        const bar = document.createElement('div');
        bar.className = `bar${state.paused ? ' paused' : ''}`;
        bar.setAttribute('role', 'toolbar');
        bar.setAttribute('aria-label', 'TraceIQ recorder');

        const status = document.createElement('span');
        status.className = 'status';
        status.setAttribute('role', 'status');
        const dot = document.createElement('span');
        dot.className = 'dot';
        const label = document.createElement('span');
        label.textContent = `${state.paused ? 'Paused' : 'Recording'} · ${state.count} step${state.count === 1 ? '' : 's'}`;
        status.append(dot, label);
        bar.append(status);

        const hint = (text) => {
            const h = document.createElement('span');
            h.className = 'hint';
            h.textContent = text;
            return h;
        };

        if (state.mode) {
            bar.append(button('Cancel check', () => send({ action: 'set-mode', mode: null })));
            bar.append(hint(state.mode === 'text'
                ? 'Click the text that must be shown. The page will not react to this click.'
                : 'Click the element that must be visible. The page will not react to this click.'));
        } else if (checkMenuOpen) {
            const add = (lbl, fn) => bar.append(button(lbl, () => { checkMenuOpen = false; fn(); renderToolbar(); }));
            add('Element is visible', () => send({ action: 'set-mode', mode: 'visible' }));
            add('Text is shown', () => send({ action: 'set-mode', mode: 'text' }));
            add('Page address', () => record('expect-url', { value: R.urlPattern(location), intent: `Address is ${location.pathname}` }));
            add('Page title', () => record('expect-title', { value: document.title, intent: `Title contains "${document.title}"` }));
            add('Back', () => undefined);
            bar.append(hint('A check makes the test fail when the page is not as expected.'));
        } else {
            if (!state.paused) bar.append(button('Add check', () => { checkMenuOpen = true; renderToolbar(); }));
            bar.append(button(state.paused ? 'Resume' : 'Pause', () => send({ action: 'pause', paused: !state.paused }), { pressed: state.paused }));
            bar.append(button('Undo last step', () => send({ action: 'undo' })));
            bar.append(button('Finish', () => send({ action: 'finish' }), { primary: true }));
        }

        // A constructed stylesheet, not a <style> element: the recorded page's
        // Content-Security-Policy would block an inline <style>.
        if (!toolbarRoot.adoptedStyleSheets.length) {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync(CSS_TEXT);
            toolbarRoot.adoptedStyleSheets = [sheet];
        }
        toolbarRoot.replaceChildren(bar);
    }
})();
