import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { Page } from '@playwright/test';

export const AUTH_FILE = '.auth/user.json';
export const CREDS_FILE = '.auth/credentials.json';

export type Credentials = {
    email: string;
    password: string;
    fullName: string;
    orgName: string;
    projectName: string;
};

/** A unique account per suite run, so the suite can be re-run against a stack that already has users. */
export function credentials(): Credentials {
    const tag = `${Date.now().toString(36)}${randomBytes(3).toString('hex')}`;
    return {
        email: `e2e-${tag}@example.com`,
        password: `E2e-${randomBytes(12).toString('hex')}`,
        fullName: 'E2E Runner',
        orgName: `E2E Org ${tag}`,
        projectName: `E2E Home ${tag}`,
    };
}

export function savedCredentials(): Credentials {
    return JSON.parse(readFileSync(CREDS_FILE, 'utf8'));
}

/**
 * Calls the API as the signed-in user (token from the page's localStorage).
 * Used only for fixtures the UI flow under test does not cover — the journey
 * creates its first case through the UI.
 */
export async function api<T = unknown>(page: Page, method: string, path: string, body?: unknown): Promise<T> {
    const token = await page.evaluate(() => localStorage.getItem('token'));
    if (!token) throw new Error('no session token in localStorage');
    const res = await page.request.fetch(`/api${path}`, {
        method,
        headers: { Authorization: `Bearer ${token}` },
        data: body,
    });
    if (!res.ok()) throw new Error(`${method} ${path} -> ${res.status()}: ${await res.text()}`);
    return (await res.json()) as T;
}
