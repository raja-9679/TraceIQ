// Shown by the service worker when an origin it has not seen before asks to
// start a recording. Closing the window without choosing counts as "no".
let answered = false;

function answer(allow) {
    answered = true;
    chrome.runtime.sendMessage({ action: 'approve', allow }).finally(() => window.close());
}

chrome.runtime.sendMessage({ action: 'approval-info' }).then((request) => {
    if (!request) return window.close();
    document.getElementById('origin').textContent = request.origin;
});
document.getElementById('allow').addEventListener('click', () => answer(true));
document.getElementById('deny').addEventListener('click', () => answer(false));
window.addEventListener('pagehide', () => {
    if (!answered) chrome.runtime.sendMessage({ action: 'approve', allow: false });
});
