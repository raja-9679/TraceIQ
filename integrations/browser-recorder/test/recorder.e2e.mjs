// End-to-end check of the recorder in a real Chromium with the extension
// loaded: a stand-in for the TraceIQ editor on one origin, a site to record on
// another, and an origin that was never approved.
//
//   integrations/browser-recorder/test/run.sh
//
// Extensions only load in a persistent context, and headless needs the
// "chromium" channel (the old headless shell has no extension support).
import { chromium } from '@playwright/test';
import http from 'node:http';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EXT = process.env.RECORDER_DIR || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const APP = `<!doctype html><title>Editor</title><h1>Editor stand-in</h1><script>
  window.msgs = [];
  window.addEventListener('message', (e) => { if (e.data && e.data.source === 'traceiq-recorder') window.msgs.push(e.data); });
  window.ask = (type, extra) => window.postMessage({ source: 'traceiq-app', type, ...extra }, location.origin);
</script>`;

const SITE = `<!doctype html><title>Shop sign in</title>
<h1>Welcome to the shop</h1>
<form onsubmit="event.preventDefault(); document.getElementById('out').textContent = 'Signed in'">
  <label>User <input name="username"></label>
  <label>Password <input type="password" name="user_password"></label>
  <label>Plan <select data-testid="plan"><option value="free">Free</option><option value="pro">Pro</option></select></label>
  <label><input type="checkbox" id="terms"> I agree</label>
  <button type="submit"><span>Sign in</span></button>
</form>
<p id="out"></p>
<button id=":r7:" class="x">Generated id</button>
<ul><li><button>Delete</button></li><li><button>Delete</button></li></ul>
<nav id="menu"><a href="#">Docs</a></nav><footer><a href="#">Docs</a></footer>
<p><a href="#more-1">More</a> <a href="#more-2">More</a></p>
<iframe id="help" src="/frame"></iframe>`;

const FRAME = `<!doctype html><button aria-label="Open help">?</button>`;

function serve(routes) {
    return new Promise((resolve) => {
        const server = http.createServer((req, res) => {
            const body = routes[req.url.split('?')[0]];
            res.writeHead(body ? 200 : 404, { 'content-type': 'text/html' });
            res.end(body || 'not found');
        });
        server.listen(0, '127.0.0.1', () => resolve({ server, origin: `http://127.0.0.1:${server.address().port}` }));
    });
}

const app = await serve({ '/': APP });
const site = await serve({ '/': SITE, '/frame': FRAME });
const stranger = await serve({ '/': APP });

const context = await chromium.launchPersistentContext(mkdtempSync(path.join(tmpdir(), 'tiq-rec-')), {
    channel: 'chromium',
    headless: true,
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
});

const until = (page, type) => page.waitForFunction((t) => window.msgs.find((m) => m.type === t), type).then((h) => h.jsonValue());
const bar = (page) => page.locator('traceiq-recorder-bar');
const count = (page, n) => bar(page).getByRole('status').filter({ hasText: `${n} step` }).waitFor();

try {
    const editor = await context.newPage();
    await editor.goto(app.origin);

    // A tab that is already open before any recording (the user's mail, say).
    const bystander = await context.newPage();
    await bystander.goto(site.origin + '/');

    // 1. Any page can find out the extension is there, nothing more.
    await editor.evaluate(() => window.ask('ping'));
    const pong = await until(editor, 'pong');
    assert.equal(pong.trusted, false);
    assert.equal(pong.pending, 0);

    // 2. An origin the user has not approved cannot start recording by itself.
    const approval = context.waitForEvent('page');
    await editor.evaluate((url) => window.ask('start', { url }), site.origin + '/');
    assert.equal((await until(editor, 'needs-approval')).type, 'needs-approval');
    const approvePage = await approval;
    await approvePage.waitForURL(/approve\.html$/);
    await approvePage.getByText(app.origin).waitFor();
    assert.equal(context.pages().filter((p) => p.url().startsWith(site.origin)).length, 1, 'no new tab before approval');

    // A new tab is announced before it has an address.
    const opened = context.waitForEvent('page');
    await approvePage.getByRole('button', { name: 'Allow and start recording' }).click();
    const shop = await opened;
    await shop.waitForURL(site.origin + '/');
    await until(editor, 'started');

    // 3. Record a journey.
    await count(shop, 1); // the goto
    await shop.locator('[name=username]').pressSequentially('alice');
    await shop.locator('[name=user_password]').pressSequentially('hunter2-secret');
    await shop.getByTestId('plan').selectOption('pro');
    await shop.locator('#terms').check();
    await shop.getByText('Sign in', { exact: true }).click(); // the <span> inside the button
    await count(shop, 6);

    // A check: the click picks the element and must not reach the page.
    await bar(shop).getByRole('button', { name: 'Add check' }).click();
    await bar(shop).getByRole('button', { name: 'Text is shown' }).click();
    await bar(shop).getByText('Click the text that must be shown').waitFor();
    await shop.locator('#out').click();
    await count(shop, 7);
    await bar(shop).getByRole('button', { name: 'Add check' }).click();
    await bar(shop).getByRole('button', { name: 'Page address' }).click();
    await count(shop, 8);

    // Selector fallbacks, a step that gets undone, and a frame.
    await shop.locator('.x').click();
    await shop.locator('li:nth-of-type(2) button').click();
    await shop.locator('#menu a').click();          // same text elsewhere: scoped to #menu
    await shop.locator('a[href="#more-2"]').click(); // same text, no named ancestor: by address
    await count(shop, 12);
    await shop.locator('h1').click();
    await count(shop, 13);
    await bar(shop).getByRole('button', { name: 'Undo last step' }).click();
    await count(shop, 12);
    await shop.frameLocator('#help').getByRole('button').click();
    await count(shop, 13);

    // Paused: nothing is recorded.
    await bar(shop).getByRole('button', { name: 'Pause' }).click();
    await bar(shop).getByText('Paused').waitFor();
    await shop.locator('.x').click();
    await bar(shop).getByRole('button', { name: 'Resume' }).click();
    await count(shop, 13);

    // Other tabs are not part of the recording, even though they are open.
    await bystander.locator('[name=username]').pressSequentially('typed in another tab');
    await bystander.locator('.x').click();
    assert.equal(await bar(bystander).count(), 0, 'no recorder bar in a tab the recorder did not open');
    await bystander.close();
    await count(shop, 13);

    // A link that opens a new tab carries the recording into it.
    await shop.evaluate(() => {
        const a = Object.assign(document.createElement('a'), { href: '/?next', target: '_blank', id: 'newtab', textContent: 'Open in new tab' });
        document.body.append(a);
    });
    const linked = context.waitForEvent('page');
    await shop.locator('#newtab').click();
    const next = await linked;
    await count(next, 14);
    await next.locator('#terms').check();
    await count(next, 15);
    await next.close();

    // 4. Finish: the steps go to the editor tab, and only there.
    await bar(shop).getByRole('button', { name: 'Finish' }).click();
    const { steps } = await until(editor, 'steps');
    const brief = steps.map((s) => [s.type, s.selector ?? '', s.value ?? '']);
    assert.deepEqual(brief, [
        ['goto', '', site.origin + '/'],
        ['fill', 'input[name="username"]', 'alice'],
        ['fill', 'input[name="user_password"]', '{{secret.USER_PASSWORD}}'],
        ['select-option', '[data-testid="plan"]', 'pro'],
        ['check', '#terms', ''],
        ['click', 'text="Sign in"', ''],
        ['expect-text', '#out', 'Signed in'],
        ['expect-url', '', '**/'],
        ['click', 'text="Generated id"', ''],
        ['click', 'body > ul > li:nth-of-type(2) > button', ''],
        ['click', '#menu >> text="Docs"', ''],
        ['click', 'a[href="#more-2"]', ''],
        ['switch-frame', '#help', ''],
        ['click', 'button[aria-label="Open help"]', ''],
        ['switch-frame', 'main', ''],
        ['click', '#newtab', ''],
        ['check', '#terms', ''],
    ]);
    assert.ok(!JSON.stringify(steps).includes('hunter2'), 'the typed password is not in the steps');
    assert.ok(!JSON.stringify(steps).includes('another tab'), 'nothing from the other tab');
    assert.ok(steps.every((s) => typeof s.id === 'string' && s.id && !('_frame' in s)));
    await bar(shop).waitFor({ state: 'detached' });

    // Every recorded selector is one Playwright (the worker) resolves to
    // exactly one element on a fresh copy of the page.
    const replay = await context.newPage();
    await replay.goto(site.origin + '/');
    let scope = replay;
    for (const step of steps) {
        if (step.type === 'switch-frame') {
            scope = step.selector === 'main' ? replay : replay.frameLocator(step.selector);
        } else if (step.selector && step.type !== 'expect-text' && step.selector !== '#newtab') { // #out is empty before sign-in; #newtab was added by this test
            assert.equal(await scope.locator(step.selector).count(), 1, `${step.selector} matches one element`);
        }
    }
    await replay.close();

    // The steps wait until the editor confirms it has them.
    await editor.evaluate(() => { window.msgs = []; window.ask('ping'); });
    assert.equal((await until(editor, 'pong')).pending, 17);
    await editor.evaluate(() => window.ask('received'));
    await until(editor, 'ok');
    await editor.evaluate(() => { window.msgs = []; window.ask('ping'); });
    assert.equal((await until(editor, 'pong')).pending, 0);

    // If the editor's tab has gone to another site, steps are not handed to it.
    await editor.goto(stranger.origin);
    await editor.evaluate(() => window.ask('ping'));
    await until(editor, 'pong');
    const sw = context.serviceWorkers()[0];
    await sw.evaluate(async (origin) => {
        const [tab] = await chrome.tabs.query({ url: origin + '/*' });
        await chrome.tabs.sendMessage(tab.id, { action: 'to-app', origin: 'http://elsewhere.example', message: { type: 'steps', steps: [{ type: 'click' }] } });
    }, stranger.origin);
    await editor.waitForTimeout(300);
    assert.equal(await editor.evaluate(() => window.msgs.some((m) => m.type === 'steps')), false);
    await editor.goto(app.origin);

    // 5. A site that was never approved gets nothing.
    const other = await context.newPage();
    await other.goto(stranger.origin);
    await other.evaluate(() => window.ask('collect'));
    assert.match((await until(other, 'error')).error, /not allowed/);
    await other.evaluate(() => { window.msgs = []; window.ask('start', { url: 'javascript:alert(1)' }); });
    assert.match((await until(other, 'error')).error, /http/);

    // 6. Saying no: nothing is recorded and the site is not remembered.
    const again = context.waitForEvent('page');
    await other.evaluate((url) => { window.msgs = []; window.ask('start', { url }); }, site.origin + '/');
    await (await again).getByRole('button', { name: "Don't allow" }).click();
    await until(other, 'denied');
    await other.evaluate(() => { window.msgs = []; window.ask('ping'); });
    assert.equal((await until(other, 'pong')).trusted, false);

    // 7. An approved origin starts straight away the second time.
    const second = context.waitForEvent('page');
    await editor.evaluate((url) => { window.msgs = []; window.ask('start', { url }); }, site.origin + '/');
    const shop2 = await second;
    await shop2.waitForURL(site.origin + '/');
    await count(shop2, 1);
    await editor.evaluate(() => window.ask('cancel'));
    await bar(shop2).waitFor({ state: 'detached' });

    console.log('recorder e2e: all checks passed');
} finally {
    await context.close();
    for (const s of [app, site, stranger]) s.server.close();
}
