import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/trust-center.html
// Nothing here changes data: no Save, Upload, Create post or "Verify audit
// chain now" is pressed and no box is ticked. Only the page's Workspace menu
// is changed (page state), because the capture account's first workspace is
// not the demo one.

const DIR = 'security/trust-center';

const card = (p: Page, id: string) => p.locator(`section[aria-labelledby="${id}"]`);
const wsPicker = (p: Page) => p.locator('main label').filter({ hasText: /^Workspace/ }).first();

async function open(p: Page) {
    await p.goto('/trust-center');
    await expect(p.getByRole('heading', { name: 'Trust center', level: 1 })).toBeVisible();
    await settle(p);
    await wsPicker(p).locator('select').selectOption({ label: 'Demo workspace' });
    await settle(p);
    await expect(p.locator('section[aria-labelledby="trust-general"]')).toBeVisible();
}


test('Security › Trust center', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    await page.setViewportSize({ width: 1440, height: 2300 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: wsPicker(page) },
        { n: 2, target: card(page, 'trust-general') },
        { n: 3, target: card(page, 'trust-posture'), side: 'right' },
        { n: 4, target: card(page, 'trust-status') },
        { n: 5, target: card(page, 'trust-docs'), side: 'right' },
        { n: 6, target: card(page, 'trust-incidents') },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups, one column wide (in two columns a box stretches to its neighbour's height).
    await page.setViewportSize({ width: 1180, height: 2300 });
    await settle(page);
    for (const [id, file] of [['trust-general', 'public-page'], ['trust-posture', 'posture'], ['trust-status', 'uptime'],
        ['trust-docs', 'documents'], ['trust-incidents', 'incidents']] as const) {
        await card(page, id).screenshot({ path: out(`${DIR}/${file}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'A trust center is a public web page that shows customers how you look after their data.', 2000);
        await caption(p, 'It is off until a workspace admin switches it on, and shows only what you choose.');
        await pointAt(p, m.getByLabel('Publish the trust center'), 2200);
        await caption(p, 'Give it a title, a contact address for security reports, and a short introduction.');
        await pointAt(p, m.getByLabel('Title', { exact: true }).first(), 1000);
        await pointAt(p, m.getByLabel('Security contact email'), 1400);
        const posture = card(p, 'trust-posture');
        await caption(p, 'Security posture: simple statements, such as whether administrators use two-step login.');
        await posture.scrollIntoViewIfNeeded();
        await pointAt(p, posture.getByText('MFA for administrators'), 2000);
        await caption(p, 'Under each one, you see what it would show today, before you publish it.');
        await posture.getByText('Would show:').nth(1).scrollIntoViewIfNeeded();
        await pointAt(p, posture.getByText('Would show:').nth(1), 2200);
        const uptime = card(p, 'trust-status');
        await caption(p, 'Uptime shows whether your monitored sites are up, from the status pages you pick.');
        await uptime.scrollIntoViewIfNeeded();
        await pointAt(p, uptime, 2000);
        const docs = card(p, 'trust-docs');
        await caption(p, 'Gated documents, such as an audit report, can only be downloaded with a link you send to a named person.');
        await docs.scrollIntoViewIfNeeded();
        await pointAt(p, docs.getByLabel('File'), 2400);
        const inc = card(p, 'trust-incidents');
        await caption(p, 'Incident communications are updates you write when something goes wrong. Only published posts are public.');
        await inc.scrollIntoViewIfNeeded();
        await pointAt(p, inc, 2400);
        await caption(p, '');
    });
});
