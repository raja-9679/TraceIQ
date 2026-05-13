// Content script — captures user interactions while recording is active.
// Steps it generates:
//   goto         (on navigation)
//   click        (on click; uses a stable selector when possible)
//   fill         (on input change)
//   press-key    (on Enter / Escape in inputs)
//
// State is held in chrome.storage.session under "tiq_recording" so popup
// can read/clear it.

(function () {
    let recording = false;
    let steps = [];

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
        // Best-effort stable selector: id > data-testid > tag+text-fragment > tag
        let selector = id;
        const testId = el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy');
        if (!selector && testId) selector = `[data-testid="${testId}"]`;
        if (!selector && text) selector = `text="${text.replace(/"/g, '\\"')}"`;
        if (!selector) selector = tag;
        const intent = aria || (role && text ? `${role}: ${text}` : text || tag);
        return { selector, intent };
    }

    // Navigation
    if (recording) {
        steps.push(makeStep('goto', { value: location.href, intent: `Open ${location.href}` }));
        persist();
    }

    // Clicks (capture phase so we see it even if the app stops propagation)
    document.addEventListener('click', (ev) => {
        if (!recording) return;
        const target = ev.target instanceof Element ? ev.target : null;
        const { selector, intent } = describeElement(target);
        steps.push(makeStep('click', { selector, intent }));
        persist();
    }, true);

    // Text input
    let lastFill = { selector: '', value: '', at: 0 };
    document.addEventListener('input', (ev) => {
        if (!recording) return;
        const target = ev.target;
        if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
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

    // Enter / Escape on inputs
    document.addEventListener('keydown', (ev) => {
        if (!recording) return;
        if (ev.key !== 'Enter' && ev.key !== 'Escape') return;
        steps.push(makeStep('press-key', { value: ev.key, intent: `Press ${ev.key}` }));
        persist();
    }, true);
})();
