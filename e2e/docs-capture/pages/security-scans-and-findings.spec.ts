import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/scans-and-findings.html
// Nothing here changes data: the Authorization and New scan panels are opened
// and closed again (never saved, never run), the workspace switch is never
// clicked, and the register's owner / due date / status controls are only
// pointed at.

const DIR = 'security/scans-and-findings';

async function open(p: Page) {
    await p.goto('/security');
    await expect(p.getByRole('heading', { name: 'Security', level: 1 })).toBeVisible();
    await settle(p);
}

test('Security › Scans & findings', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    await expect(main.getByText('No scans yet', { exact: true })).toBeVisible();

    // 1. The page as a reader first meets it (the Scans view).
    await page.screenshot({ path: out(`${DIR}/page.png`) });
    const viewTabs = main.getByRole('tablist', { name: 'Security view' });
    const actions = main.getByRole('button', { name: 'Authorization', exact: true }).locator('xpath=..');

    // 2. The two panels behind the header buttons (opened, looked at, closed).
    await main.getByRole('button', { name: 'Authorization', exact: true }).click();
    const auth = main.locator('section').filter({ has: page.getByRole('heading', { name: 'Authorization' }) });
    await expect(auth.getByText('Security scanning enabled')).toBeVisible();
    await settle(page);
    await auth.screenshot({ path: out(`${DIR}/authorization.png`) });
    await main.getByRole('button', { name: 'Authorization', exact: true }).click();
    await main.getByRole('button', { name: 'New scan', exact: true }).click();
    const scan = main.locator('section').filter({ has: page.getByRole('heading', { name: 'New scan' }) });
    await expect(scan.getByLabel('Target URL')).toBeVisible();
    await scan.getByRole('button', { name: 'API import and token auth' }).click();
    await scan.screenshot({ path: out(`${DIR}/new-scan.png`) });
    await main.getByRole('button', { name: 'New scan', exact: true }).click();
    await expect(scan).toHaveCount(0);

    // 3. The Findings register view, numbered.
    await viewTabs.getByRole('tab', { name: 'Findings register' }).click();
    await settle(page);
    const register = main.locator('section').filter({ has: page.getByRole('heading', { name: 'Findings register' }) });
    const sla = main.locator('section').filter({ has: page.getByRole('heading', { name: 'Remediation SLA' }) });
    await expect(register.locator('tbody tr').first()).toBeVisible();
    const toolbar = register.getByLabel('Filter findings by status').locator('xpath=..');
    const table = register.locator('table');
    await page.setViewportSize({ width: 1440, height: 1060 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: viewTabs },
        { n: 2, target: actions, side: 'right' },
        { n: 3, target: toolbar, side: 'right' },
        { n: 4, target: table },
        { n: 5, target: sla },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 4. Close-ups used next to each explanation.
    await viewTabs.screenshot({ path: out(`${DIR}/view-tabs.png`) });
    await actions.screenshot({ path: out(`${DIR}/actions.png`) });
    await register.screenshot({ path: out(`${DIR}/register.png`) });
    await sla.screenshot({ path: out(`${DIR}/sla.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'Scans and findings is where TraceIQ keeps the security problems it has found in your app.', 2000);
        await caption(p, 'The Scans view lists the security scans run for this project. The demo project has none yet.');
        await pointAt(p, m.getByText('No scans yet', { exact: true }), 2000);
        await caption(p, 'Before anyone can scan, a project admin says which sites may be scanned, under Authorization.');
        const authBtn = m.getByRole('button', { name: 'Authorization', exact: true });
        await pointAt(p, authBtn, 800);
        await authBtn.click();
        const a = m.locator('section').filter({ has: p.getByRole('heading', { name: 'Authorization' }) });
        await expect(a.getByText('Authorized domains')).toBeVisible();
        await pointAt(p, a.getByText('Authorized domains'), 2200);
        await authBtn.click();
        await caption(p, 'New scan starts a scan of one web address. You must confirm the site is yours.');
        const newBtn = m.getByRole('button', { name: 'New scan', exact: true });
        await pointAt(p, newBtn, 800);
        await newBtn.click();
        const s = m.locator('section').filter({ has: p.getByRole('heading', { name: 'New scan' }) });
        await pointAt(p, s.getByText('I am authorized to scan this target', { exact: false }), 2400);
        await newBtn.click();
        await caption(p, 'The Findings register lists every security problem found, from scans and from ordinary test runs.');
        const regTab = m.getByRole('tab', { name: 'Findings register' });
        await pointAt(p, regTab, 800);
        await regTab.click();
        await settle(p);
        const r = m.locator('section').filter({ has: p.getByRole('heading', { name: 'Findings register' }) });
        const row = r.locator('tbody tr').first();
        await pointAt(p, row.locator('td').nth(1), 2000);
        await caption(p, 'Each finding has a severity: how serious it is.');
        await pointAt(p, row.locator('td').first(), 2000);
        await caption(p, 'An owner, and a date it should be fixed by.');
        await pointAt(p, row.getByLabel(/Owner of finding/), 1200);
        await pointAt(p, row.getByLabel(/Due date of finding/), 1400);
        await caption(p, 'And a status. Marking it fixed asks TraceIQ to check again; only that check can confirm the fix.');
        await pointAt(p, row.getByLabel(/Status of finding/), 2600);
        const sl = m.locator('section').filter({ has: p.getByRole('heading', { name: 'Remediation SLA' }) });
        await caption(p, 'Remediation SLA sets how many days each severity has, from the day it was first found.');
        await sl.scrollIntoViewIfNeeded();
        await pointAt(p, sl.getByLabel('high'), 2400);
        await caption(p, '');
    });
});
