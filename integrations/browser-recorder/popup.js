const $ = (id) => document.getElementById(id);

function setStatus(msg, cls = '') {
    const el = $('status');
    el.textContent = msg;
    el.className = cls;
}

async function init() {
    chrome.storage.local.get(['tiq_base_url', 'tiq_api_key', 'tiq_default_suite_id'], (cfg) => {
        if (cfg.tiq_base_url) $('base-url').value = cfg.tiq_base_url;
        if (cfg.tiq_api_key) $('api-key').value = cfg.tiq_api_key;
        if (cfg.tiq_default_suite_id) $('suite-id').value = cfg.tiq_default_suite_id;
    });
    chrome.storage.session.get(['tiq_recording', 'tiq_steps'], (s) => {
        updateToggle(s.tiq_recording === true);
        setStatus(`${(s.tiq_steps || []).length} step(s) captured.`);
    });
}

function updateToggle(active) {
    $('toggle').textContent = active ? 'Stop recording' : 'Start recording';
}

function persistConfig() {
    chrome.storage.local.set({
        tiq_base_url: $('base-url').value.trim(),
        tiq_api_key: $('api-key').value.trim(),
        tiq_default_suite_id: $('suite-id').value.trim(),
    });
}

$('toggle').addEventListener('click', async () => {
    const { tiq_recording = false } = await chrome.storage.session.get('tiq_recording');
    const next = !tiq_recording;
    await chrome.storage.session.set({ tiq_recording: next, tiq_steps: next ? [] : (await chrome.storage.session.get('tiq_steps')).tiq_steps || [] });
    updateToggle(next);
    setStatus(next ? 'Recording…' : 'Stopped.');
    persistConfig();
});

$('clear').addEventListener('click', async () => {
    await chrome.storage.session.set({ tiq_steps: [] });
    setStatus('Cleared.');
});

// Steps recorded inside iframes carry `_frame` (the iframe's selector).
// Weave explicit `switch-frame` steps wherever the frame context changes —
// including a switch back to 'main' — and strip the tag before saving.
function weaveFrameSwitches(steps) {
    const out = [];
    let currentFrame = '';
    for (const step of steps) {
        const frame = step._frame || '';
        if (frame !== currentFrame) {
            out.push({
                id: crypto.randomUUID(),
                type: 'switch-frame',
                selector: frame || 'main',
                intent: frame ? `Enter iframe ${frame}` : 'Return to the main page',
            });
            currentFrame = frame;
        }
        const { _frame, ...clean } = step;
        out.push(clean);
    }
    return out;
}

$('save').addEventListener('click', async () => {
    persistConfig();
    const { tiq_steps = [] } = await chrome.storage.session.get('tiq_steps');
    if (!tiq_steps.length) return setStatus('No steps to save.', 'err');
    const suiteId = parseInt($('suite-id').value, 10);
    const name = $('case-name').value.trim() || 'Recorded case';
    if (!suiteId) return setStatus('Suite ID required.', 'err');

    setStatus('Saving…');
    chrome.runtime.sendMessage(
        { action: 'save-case', payload: { name, suiteId, steps: weaveFrameSwitches(tiq_steps) } },
        (resp) => {
            if (!resp?.ok) return setStatus(resp?.error || 'Save failed.', 'err');
            setStatus(`Saved as case #${resp.data?.id}.`, 'ok');
        },
    );
});

init();
