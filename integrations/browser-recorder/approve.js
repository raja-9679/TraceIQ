// Shown by the service worker when an origin it has not seen before asks to
// start a recording. The window belongs to ONE request (the nonce in its
// address); its answer is ignored for any other. Closing it counts as "no".
const nonce = new URLSearchParams(location.search).get('req') || '';
let answered = false;

function answer(allow) {
    answered = true;
    chrome.runtime.sendMessage({ action: 'approve', allow, nonce }).finally(() => window.close());
}

chrome.runtime.sendMessage({ action: 'approval-info', nonce }).then((request) => {
    if (!request) return window.close();
    document.getElementById('origin').textContent = request.origin;
});
document.getElementById('allow').addEventListener('click', () => answer(true));
document.getElementById('deny').addEventListener('click', () => answer(false));
window.addEventListener('pagehide', () => {
    if (!answered) chrome.runtime.sendMessage({ action: 'approve', allow: false, nonce });
});
