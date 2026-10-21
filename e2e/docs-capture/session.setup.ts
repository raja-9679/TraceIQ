import { expect, test as setup } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { api } from '../tests/support/account';

// One dedicated "docs" account per stack, reused across capture runs so the
// pictures stay stable. Its credentials stay in e2e/.auth/ (gitignored).
// The account gets the built-in demo workspace (POST /demo-workspace: 30 days
// of seeded runs, flakes, findings…), so every page has realistic data and no
// real project's names or URLs ever appear in the guide.

const CREDS = '.auth/docs-credentials.json';
const STATE = '.auth/docs-user.json';
const PROJECT_FILE = '.auth/docs-project.json';

type Creds = { email: string; password: string; fullName: string };

function creds(): Creds {
    if (existsSync(CREDS)) return JSON.parse(readFileSync(CREDS, 'utf8'));
    const c = {
        email: `docs-guide-${randomBytes(3).toString('hex')}@example.com`,
        password: `Docs-${randomBytes(12).toString('hex')}`,
        // Shown in "Welcome back, Alex" and the account menu.
        fullName: 'Alex Morgan',
    };
    mkdirSync('.auth', { recursive: true });
    writeFileSync(CREDS, JSON.stringify(c));
    return c;
}

setup('docs account with a demo workspace', async ({ page }) => {
    const c = creds();
    await page.goto('/login');
    await page.getByLabel('Work Email').fill(c.email);
    await page.getByLabel('Password').fill(c.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    const signedIn = await page.waitForURL(/\/overview/, { timeout: 8_000 }).then(() => true, () => false);

    if (!signedIn) {
        await page.goto('/signup');
        await page.getByLabel('Full Name').fill(c.fullName);
        await page.getByLabel('Org Name (Optional)').fill('Docs Guide');
        await page.getByLabel('Project Name (Optional)').fill('My first project');
        await page.getByLabel('Work Email').fill(c.email);
        await page.getByLabel('Password', { exact: true }).fill(c.password);
        await page.getByLabel('Confirm').fill(c.password);
        await page.getByRole('button', { name: 'Create Account' }).click();
        await page.getByRole('button', { name: 'Skip for now' }).click();
        await expect(page).toHaveURL(/\/overview/);
    }

    type Demo = { workspace_id: number; projects: { id: number; name: string }[] } | null;
    let demo = (await api<{ demo: Demo }>(page, 'GET', '/demo-workspace')).demo;
    if (!demo) demo = (await api<{ demo: Demo }>(page, 'POST', '/demo-workspace')).demo;
    if (!demo || demo.projects.length === 0) throw new Error('demo workspace has no projects');

    // The web project carries the UI runs; it is the first one created.
    const project = demo.projects[0];
    writeFileSync(PROJECT_FILE, JSON.stringify(project));
    await page.evaluate((id) => localStorage.setItem('activeProjectId', String(id)), project.id);
    await page.context().storageState({ path: STATE });
});
