// Service worker — handles "Save to TraceIQ" by POSTing recorded steps
// to the configured backend.

async function getSettings() {
    return new Promise((resolve) => {
        chrome.storage.local.get(['tiq_base_url', 'tiq_api_key', 'tiq_default_suite_id'], resolve);
    });
}

async function saveCase({ name, suiteId, steps }) {
    const { tiq_base_url, tiq_api_key } = await getSettings();
    if (!tiq_base_url || !tiq_api_key) {
        throw new Error('TraceIQ base URL or API key not configured in the extension settings.');
    }
    const res = await fetch(`${tiq_base_url.replace(/\/$/, '')}/api/cases`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-API-Key': tiq_api_key,
            'X-Agent-Id': 'traceiq-recorder',
        },
        body: JSON.stringify({
            name,
            steps,
            test_suite_id: suiteId,
        }),
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`TraceIQ rejected the case: ${res.status} ${text}`);
    }
    return res.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.action === 'save-case') {
        saveCase(msg.payload)
            .then((data) => sendResponse({ ok: true, data }))
            .catch((err) => sendResponse({ ok: false, error: err.message }));
        return true; // keep the channel open for the async response
    }
});
