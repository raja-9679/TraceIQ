import { chromium, expect, test } from '@playwright/test';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { out, project, settle } from '../capture';

// Pictures for "Recording a test" on the Suites guide page.
//
// The recorder is a browser extension, and extensions load only in a
// persistent Chromium context, so this capture starts its own browser and
// signs in with the docs account instead of using the shared session. The
// site being recorded is a small sample page served from this process: the
// demo workspace's hosts are not real sites, so the browser is handed the
// page for https://todo.example.com itself and nothing leaves the machine.

const EXTENSION = join(__dirname, '../../../integrations/browser-recorder');
const SHOT = (name: string) => out(`tests/suites/${name}`);

const SAMPLE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sign in · Todo app</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; margin: 0; background: #f7f5f0; color: #1c1b22; }
  main { max-width: 380px; margin: 56px auto; padding: 28px; background: #fff; border: 1px solid #e4e1d9; border-radius: 20px; }
  h1 { margin: 0 0 16px; font-size: 24px; } label { display: block; margin: 12px 0 4px; font-weight: 600; font-size: 14px; }
  input { width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #d9d5ca; border-radius: 10px; font: inherit; }
  button { margin-top: 18px; padding: 10px 18px; border: 0; border-radius: 999px; background: #1c1b22; color: #fff; font: inherit; font-weight: 600; }
  #welcome { margin-top: 16px; font-weight: 600; }
</style></head><body><main>
<h1>Sign in to Todo</h1>
<form onsubmit="event.preventDefault(); document.getElementById('welcome').textContent = 'Welcome back, alice';">
  <label for="login-username">User name</label><input id="login-username" name="username" autocomplete="off">
  <label for="login-password">Password</label><input id="login-password" name="password" type="password">
  <button id="login-submit" type="submit">Sign in</button>
</form>
<p id="welcome"></p>
</main></body></html>`;

test('Suites: recording a test', async () => {
    const creds = JSON.parse(readFileSync('.auth/docs-credentials.json', 'utf8'));
    const site = 'https://todo.example.com/login';

    const context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'tiq-docs-rec-')), {
        channel: 'chromium',
        headless: true,
        viewport: { width: 1440, height: 900 },
        baseURL: process.env.TRACEIQ_DOCS_BASE_URL || 'http://frontend',
        args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
    });
    await context.route('https://todo.example.com/**', (route) => route.fulfill({ contentType: 'text/html', body: SAMPLE }));
    try {
        const page = await context.newPage();
        await page.goto('/login');
        await page.getByLabel('Work Email').fill(creds.email);
        await page.getByLabel('Password').fill(creds.password);
        await page.getByRole('button', { name: 'Sign In' }).click();
        await page.waitForURL(/\/overview/);
        await page.evaluate((id) => localStorage.setItem('activeProjectId', String(id)), project().id);

        // Any suite of the demo project: the test is never saved.
        const suites = await page.evaluate(async (pid) => {
            const res = await fetch(`/api/suites?project_id=${pid}`, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } });
            return res.json();
        }, project().id);
        await page.goto(`/suites/${suites[0].id}/builder`);
        await settle(page);

        await page.getByRole('toolbar', { name: 'Add a step' }).getByRole('button', { name: 'Record' }).click();
        const dialog = page.getByRole('dialog');
        await dialog.getByLabel('Page to start on').fill(site);
        await dialog.screenshot({ path: SHOT('recorder-dialog.png') });

        // First use: the extension asks whether this TraceIQ may record.
        const asking = context.waitForEvent('page');
        await dialog.getByRole('button', { name: 'Start recording' }).click();
        const approve = await asking;
        await approve.setViewportSize({ width: 460, height: 330 });
        await approve.getByRole('button', { name: 'Allow and start recording' }).waitFor();
        await approve.screenshot({ path: SHOT('recorder-allow.png') });

        // The new tab is announced before it has an address.
        const opening = context.waitForEvent('page');
        await approve.getByRole('button', { name: 'Allow and start recording' }).click();
        const shop = await opening;
        await shop.waitForURL(site);
        await shop.setViewportSize({ width: 900, height: 560 });
        const bar = shop.locator('traceiq-recorder-bar');
        const steps = (n: number) => bar.getByRole('status').filter({ hasText: `${n} step` }).waitFor();

        await steps(1);
        await shop.getByLabel('User name').pressSequentially('alice');
        await shop.getByLabel('Password').pressSequentially('not-recorded');
        await shop.getByRole('button', { name: 'Sign in' }).click();
        await steps(4);
        await shop.mouse.move(450, 40);
        await shop.screenshot({ path: SHOT('recorder-bar.png') });

        await bar.getByRole('button', { name: 'Add check' }).click();
        await bar.getByRole('button', { name: 'Text is shown' }).waitFor();
        await bar.getByRole('toolbar').screenshot({ path: SHOT('recorder-checks.png') });
        await bar.getByRole('button', { name: 'Text is shown' }).click();
        await shop.getByText('Welcome back, alice').click();
        await steps(5);

        await bar.getByRole('button', { name: 'Finish' }).click();
        await expect(page.getByText('5 recorded steps added')).toBeVisible();
        await expect(dialog).toBeHidden();
        // The confirmation message covers the last step until it goes away.
        await expect(page.getByText('5 recorded steps added')).toBeHidden({ timeout: 20_000 });
        await page.setViewportSize({ width: 1440, height: 1500 });
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.mouse.move(700, 5);
        const timeline = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Execution Timeline' }) });
        await timeline.screenshot({ path: SHOT('recorder-result.png') });
        // Leave nothing behind: the draft is discarded, the test was never saved.
        await page.evaluate(() => {
            for (const key of Object.keys(localStorage)) if (/draft/i.test(key)) localStorage.removeItem(key);
        });
    } finally {
        await context.close();
    }
});
