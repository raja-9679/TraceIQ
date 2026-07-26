// Content script — captures user interactions while recording is active.
// Steps it generates:
//   goto           (on navigation, top frame only)
//   click          (on click; skips controls with dedicated step types)
//   fill           (on input change, keystrokes coalesced)
//   select-option  (on <select> change)
//   check/uncheck  (on checkbox/radio change)
//   drag-and-drop  (HTML5 dragstart → drop)
//   hover          (explicit: Ctrl+Shift+H over the element)
//   press-key      (Enter / Escape in inputs)
//
// Frames: runs in every same-origin frame (manifest all_frames). Steps
// recorded inside a frame are tagged with `_frame` (the iframe's selector in
// the parent document); the popup weaves `switch-frame` steps from those tags
// at save time. Cross-origin iframes cannot be recorded (the browser hides
// window.frameElement) — interactions inside them are silently skipped.
//
// State is held in chrome.storage.session under "tiq_recording" so popup
// can read/clear it.

(function () {
    let recording = false;
    let steps = [];

    // '' = top frame. For same-origin child frames, a selector that the
    // runner's `switch-frame` step can resolve in the PARENT document.
    let frameSelector = '';
    if (window !== window.top) {
        try {
            const fe = window.frameElement; // null / throws when cross-origin
            if (!fe) return; // cross-origin frame — cannot record here
            if (fe.id) frameSelector = `#${CSS.escape(fe.id)}`;
            else if (fe.getAttribute('name')) frameSelector = `iframe[name="${fe.getAttribute('name')}"]`;
            else if (fe.getAttribute('src')) frameSelector = `iframe[src*="${fe.getAttribute('src').split('/').pop().split('?')[0]}"]`;
            else frameSelector = 'iframe';
        } catch {
            return; // cross-origin — bail out quietly
        }
    }

    chrome.storage.session.get(['tiq_recording', 'tiq_steps'], (data) => {
        recording = data.tiq_recording === true;
        steps = Array.isArray(data.tiq_steps) ? data.tiq_steps : [];
    });

    chrome.storage.onChanged.addListener((changes, area) => {
        if (area !== 'session') return;
        if (changes.tiq_recording) recording = changes.tiq_recording.newValue === true;
        if (changes.tiq_steps) steps = Array.isArray(changes.tiq_steps.newValue) ? changes.tiq_steps.newValue : [];
    });

    function persist() {
        chrome.storage.session.set({ tiq_steps: steps });
    }

    function makeStep(type, extras) {
        return {
            id: crypto.randomUUID(),
            type,
            ...(frameSelector ? { _frame: frameSelector } : {}),
            ...extras,
        };
    }

    function describeElement(el) {
        if (!el) return { selector: '', intent: '' };
        const id = el.id ? `#${CSS.escape(el.id)}` : '';
        const role = el.getAttribute('role');
        const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
        const text = (el.textContent || '').trim().slice(0, 60);
        const tag = el.tagName ? el.tagName.toLowerCase() : 'unknown';
        // Best-effort stable selector:
        // id > data-testid > name > aria-label > tag+text-fragment > tag
        let selector = id;
        const testId = el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy');
        if (!selector && testId) selector = `[data-testid="${testId}"]`;
        const name = el.getAttribute('name');
        if (!selector && name) selector = `${tag}[name="${name}"]`;
        if (!selector && aria && el.getAttribute('aria-label')) selector = `[aria-label="${el.getAttribute('aria-label').replace(/"/g, '\\"')}"]`;
        if (!selector && text) selector = `text="${text.replace(/"/g, '\\"')}"`;
        if (!selector) selector = tag;
        const intent = aria || (role && text ? `${role}: ${text}` : text || tag);
        return { selector, intent };
    }

    // Navigation (top frame only — frame loads are implementation detail)
    if (recording && !frameSelector) {
        steps.push(makeStep('goto', { value: location.href, intent: `Open ${location.href}` }));
        persist();
    }

    // Controls with a dedicated step type must not double-record as clicks.
    function hasDedicatedStep(el) {
        if (el instanceof HTMLSelectElement) return true;
        if (el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio' || el.type === 'file')) return true;
        return false;
    }

    // Clicks (capture phase so we see it even if the app stops propagation)
    document.addEventListener('click', (ev) => {
        if (!recording) return;
        const target = ev.target instanceof Element ? ev.target : null;
        if (!target || hasDedicatedStep(target.closest('select, input') || target)) return;
        const { selector, intent } = describeElement(target);
        steps.push(makeStep('click', { selector, intent }));
        persist();
    }, true);

    // Select dropdowns + checkboxes / radios
    document.addEventListener('change', (ev) => {
        if (!recording) return;
        const target = ev.target;
        if (target instanceof HTMLSelectElement) {
            const { selector, intent } = describeElement(target);
            steps.push(makeStep('select-option', {
                selector, value: target.value,
                intent: intent ? `Select "${target.value}" in ${intent}` : `Select "${target.value}"`,
            }));
            persist();
        } else if (target instanceof HTMLInputElement && (target.type === 'checkbox' || target.type === 'radio')) {
            const { selector, intent } = describeElement(target);
            steps.push(makeStep(target.checked ? 'check' : 'uncheck', { selector, intent }));
            persist();
        }
    }, true);

    // Text input
    let lastFill = { selector: '', value: '', at: 0 };
    document.addEventListener('input', (ev) => {
        if (!recording) return;
        const target = ev.target;
        if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
        if (target instanceof HTMLInputElement && (target.type === 'checkbox' || target.type === 'radio' || target.type === 'file')) return;
        const { selector, intent } = describeElement(target);
        // Coalesce successive keystrokes on the same field into a single fill step.
        if (lastFill.selector === selector && Date.now() - lastFill.at < 500) {
            steps[steps.length - 1].value = target.value;
            lastFill.value = target.value;
            lastFill.at = Date.now();
            persist();
            return;
        }
        const step = makeStep('fill', { selector, value: target.value, intent });
        steps.push(step);
        lastFill = { selector, value: target.value, at: Date.now() };
        persist();
    }, true);

    // Drag & drop (HTML5 DnD): remember the dragged source, record on drop.
    let dragSource = null;
    document.addEventListener('dragstart', (ev) => {
        if (!recording) return;
        dragSource = ev.target instanceof Element ? ev.target : null;
    }, true);
    document.addEventListener('drop', (ev) => {
        if (!recording || !dragSource) return;
        const target = ev.target instanceof Element ? ev.target : null;
        const src = describeElement(dragSource);
        const dst = describeElement(target);
        steps.push(makeStep('drag-and-drop', {
            selector: src.selector,
            value: dst.selector,
            intent: `Drag ${src.intent || 'element'} onto ${dst.intent || 'target'}`,
        }));
        dragSource = null;
        persist();
    }, true);

    // Hover is too noisy to auto-record — press Ctrl+Shift+H while pointing
    // at the element to capture an explicit hover step.
    let lastHoverTarget = null;
    document.addEventListener('mouseover', (ev) => {
        if (ev.target instanceof Element) lastHoverTarget = ev.target;
    }, true);

    // Enter / Escape on inputs + the explicit hover chord
    document.addEventListener('keydown', (ev) => {
        if (!recording) return;
        if (ev.ctrlKey && ev.shiftKey && (ev.key === 'H' || ev.key === 'h')) {
            const { selector, intent } = describeElement(lastHoverTarget);
            if (selector) {
                steps.push(makeStep('hover', { selector, intent: intent ? `Hover ${intent}` : 'Hover' }));
                persist();
            }
            ev.preventDefault();
            return;
        }
        if (ev.key !== 'Enter' && ev.key !== 'Escape') return;
        steps.push(makeStep('press-key', { value: ev.key, intent: `Press ${ev.key}` }));
        persist();
    }, true);
})();
