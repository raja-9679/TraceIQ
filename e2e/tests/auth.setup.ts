import { expect, test as setup } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { credentials, AUTH_FILE, CREDS_FILE } from './support/account';
import { settle, watch } from './support/health';

// (1) Register, then log in with a fresh browser. The first account on a fresh
// stack is the admin of the first tenant, i.e. the instance admin, so every
// sidebar destination (Worker fleet, Instance settings) is reachable later.

setup('register a new account and log in with it', async ({ browser, baseURL }) => {
    const creds = credentials();
    mkdirSync('.auth', { recursive: true });
    writeFileSync(CREDS_FILE, JSON.stringify(creds));

    // Register.
    const signupCtx = await browser.newContext();
    const signup = await signupCtx.newPage();
    const signupProblems = watch(signup, baseURL!);
    await signup.goto('/signup');
    await expect(signup.getByRole('heading', { name: 'Create Account' })).toBeVisible();
    await signup.getByLabel('Full Name').fill(creds.fullName);
    await signup.getByLabel('Org Name (Optional)').fill(creds.orgName);
    await signup.getByLabel('Project Name (Optional)').fill(creds.projectName);
    await signup.getByLabel('Work Email').fill(creds.email);
    await signup.getByLabel('Password', { exact: true }).fill(creds.password);
    await signup.getByLabel('Confirm').fill(creds.password);
    await signup.getByRole('button', { name: 'Create Account' }).click();
    // Registration offers optional 2FA enrollment before entering the app.
    await signup.getByRole('button', { name: 'Skip for now' }).click();
    await expect(signup).not.toHaveURL(/\/(signup|login)/);
    await settle(signupProblems);
    expect(signupProblems.errors, 'problems during signup').toEqual([]);
    await signupCtx.close();

    // Log in from a clean browser with the same credentials.
    const loginCtx = await browser.newContext();
    const page = await loginCtx.newPage();
    const problems = watch(page, baseURL!);
    await page.goto('/login');
    await page.getByLabel('Work Email').fill(creds.email);
    await page.getByLabel('Password').fill(creds.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL(/\/overview/);
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
    // The project created at signup is the active one, so project-scoped pages
    // have something to show.
    await expect(page.getByRole('button', { name: `Project: ${creds.projectName}` })).toBeVisible();
    await settle(problems);
    expect(problems.errors, 'problems during login').toEqual([]);

    await loginCtx.storageState({ path: AUTH_FILE });
    await loginCtx.close();
});
