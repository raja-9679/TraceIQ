// The toolbar menu: what the recorder is doing, a way out when the recorded
// tab is gone, and the list of sites allowed to start a recording.
const $ = (id) => document.getElementById(id);
const ask = (message) => chrome.runtime.sendMessage(message);

async function render() {
    const info = await ask({ action: 'popup-info' });
    const pending = info.pending ? info.pending.length : 0;

    $('status').textContent = info.recording
        ? `${info.paused ? 'Paused' : 'Recording'} · ${info.count} step${info.count === 1 ? '' : 's'} so far.`
        : pending
            ? `${pending} recorded step${pending === 1 ? ' is' : 's are'} waiting to be added to a test.`
            : 'Not recording. Open a test in TraceIQ and press Record.';
    $('recording').hidden = !info.recording;
    $('pending').hidden = info.recording || !pending;
    $('copy').onclick = async () => {
        await navigator.clipboard.writeText(JSON.stringify(info.pending, null, 2));
        $('copy').textContent = 'Copied';
    };

    $('none').hidden = info.trusted.length > 0;
    $('trusted').replaceChildren(...info.trusted.map((origin) => {
        const li = document.createElement('li');
        const name = document.createElement('span');
        name.textContent = origin;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = 'Remove';
        remove.setAttribute('aria-label', `Remove ${origin}`);
        remove.onclick = () => ask({ action: 'untrust', origin }).then(render);
        li.append(name, remove);
        return li;
    }));
    $('version').textContent = `Version ${info.version}`;
}

$('finish').addEventListener('click', () => ask({ action: 'finish' }).then(() => window.close()));
$('cancel').addEventListener('click', () => ask({ action: 'cancel' }).then(render));
render();
