import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import appSource from '../../App.tsx?raw';
import { places, setupLinks, isIn, isGroup } from './nav';
import DashboardLayout from './DashboardLayout';

// ---- Navigation config -----------------------------------------------------

const names = (links: { name: string }[]) => links.map((l) => l.name);
const place = (name: string) => places.find((p) => p.name === name)!;

describe('sidebar information architecture', () => {
    it('has the six places in order (Setup renders after them)', () => {
        expect(names(places)).toEqual(['Today', 'Releases', 'Tests', 'Runs', 'Watch', 'Security']);
    });

    it('groups each former sidebar item under its place', () => {
        expect(names(place('Today').children)).toEqual(['Overview', 'Failure triage', 'Proposals', 'Flaky tests', 'Visual review']);
        expect(names(place('Releases').children)).toEqual(['Quality gate', 'Release readiness', 'Requirements', 'Traceability', 'Incidents']);
        expect(names(place('Security').children)).toEqual(['Scans & findings', 'Access matrix', 'Access reviews', 'Estate', 'Script inventory']);
        expect(names(place('Watch').children)).toEqual(['Monitors', 'On-call']);
        expect(names(place('Tests').children)).toEqual(['Suites', 'Schedules', 'Environments', 'App builds', 'Frontend quality', 'Import Playwright', 'Analytics']);
        expect(names(setupLinks)).toEqual([
            'Settings', 'Workspace', 'Users', 'Tenant admin', 'Worker fleet', 'API keys',
            'Webhooks', 'Issue trackers', 'AI usage', 'Billing',
        ]);
    });

    it('keeps Runs as a direct destination', () => {
        expect(isGroup(place('Runs'))).toBe(false);
        for (const n of ['Today', 'Releases', 'Tests', 'Watch', 'Security']) expect(isGroup(place(n))).toBe(true);
        expect([place('Runs').href, place('Watch').href, place('Security').href]).toEqual(['/runs', '/monitors', '/security']);
    });

    it('offers all 35 destinations exactly once', () => {
        const hrefs = [
            ...places.filter((p) => !isGroup(p)).map((p) => p.href),
            ...places.flatMap((p) => p.children.map((c) => c.href)),
            ...setupLinks.map((l) => l.href),
        ];
        expect(hrefs).toHaveLength(35);
        expect(new Set(hrefs).size).toBe(hrefs.length);
    });

    it('points every link at a route that exists in App.tsx', () => {
        const routes = new Set([...appSource.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => '/' + m[1].replace(/^\//, '')));
        const hrefs = [
            ...places.map((p) => p.href),
            ...places.flatMap((p) => p.children.map((c) => c.href)),
            ...setupLinks.map((l) => l.href),
        ];
        for (const h of hrefs) expect(routes, `no <Route> for ${h}`).toContain(h);
    });

    it('counts nested pages as inside their place', () => {
        expect(isIn('/suites/12', place('Tests').match)).toBe(true);
        expect(isIn('/schedules', place('Tests').match)).toBe(true);
        expect(isIn('/runs/7', place('Runs').match)).toBe(true);
        expect(isIn('/', place('Today').match)).toBe(true);
        expect(isIn('/analytics', place('Tests').match)).toBe(true);
        expect(isIn('/analytics', place('Releases').match)).toBe(false);
        expect(isIn('/runs', place('Tests').match)).toBe(false);
        expect(isIn('/access-matrix', place('Security').match)).toBe(true);
        expect(isIn('/incidents', place('Releases').match)).toBe(true);
        expect(isIn('/on-call', place('Watch').match)).toBe(true);
        expect(isIn('/estate', place('Security').match)).toBe(true);
        expect(isIn('/script-inventory', place('Security').match)).toBe(true);
        expect(isIn('/frontend-quality', place('Tests').match)).toBe(true);
    });
});

// ---- DashboardLayout behaviour ---------------------------------------------

let systemPermissions: string[] = [];

vi.mock('@/context/AuthContext', () => ({
    useAuth: () => ({ user: { id: 1, email: 'dev@example.com', full_name: 'Dev User' }, logout: vi.fn() }),
}));
vi.mock('@/lib/api', () => ({
    getProjects: async () => [{ id: 1, name: 'Checkout App' }],
    getMyPermissions: async () => ({ system: systemPermissions, workspace: {}, project: {} }),
}));

function LocationProbe() {
    return <div data-testid="location">{useLocation().pathname}</div>;
}

function renderAt(path: string, { collapsed = false } = {}) {
    localStorage.setItem('traceiq.sidebarCollapsed', collapsed ? '1' : '0');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={[path]}>
                <Routes>
                    <Route path="/" element={<DashboardLayout />}>
                        <Route path="*" element={<LocationProbe />} />
                    </Route>
                </Routes>
            </MemoryRouter>
        </QueryClientProvider>,
    );
}

const desktopNav = () => within(screen.getByRole('complementary', { name: 'Workspace' }));
const location = () => screen.getByTestId('location').textContent;

describe('expanded sidebar', () => {
    beforeEach(() => { systemPermissions = []; });

    it('renders the seven top-level entries in order', () => {
        renderAt('/runs');
        const nav = desktopNav();
        // Each child of the nav is a direct link or a group (whose first button is its row).
        const top = Array.from(nav.getByRole('navigation', { name: 'Main' }).children)
            .map((el) => (el.tagName === 'A' ? el : el.querySelector('button'))?.textContent);
        expect(top).toEqual(['Today', 'Releases', 'Tests', 'Runs', 'Watch', 'Security']);
        expect(nav.getByRole('button', { name: 'Setup' })).toBeTruthy();
    });

    it('opens the group holding the current page and highlights it', () => {
        renderAt('/schedules');
        const nav = desktopNav();
        const tests = nav.getByRole('button', { name: 'Tests' });
        expect(tests.getAttribute('aria-expanded')).toBe('true');
        expect(tests.hasAttribute('data-active')).toBe(true);
        expect(nav.getByRole('link', { name: 'Schedules' }).getAttribute('aria-current')).toBe('page');
        expect(nav.getByRole('button', { name: 'Releases' }).getAttribute('aria-expanded')).toBe('false');
    });

    it('toggles a group open and closed without navigating', async () => {
        const user = userEvent.setup();
        renderAt('/runs');
        const nav = desktopNav();
        await user.click(nav.getByRole('button', { name: 'Releases' }));
        expect(nav.getByRole('link', { name: 'Traceability' }).getAttribute('href')).toBe('/traceability');
        expect(location()).toBe('/runs');
        await user.click(nav.getByRole('button', { name: 'Releases' }));
        expect(nav.queryByRole('link', { name: 'Traceability' })).toBeNull();
    });

    it('lets the user close the group that holds the current page', async () => {
        const user = userEvent.setup();
        renderAt('/schedules');
        const nav = desktopNav();
        await user.click(nav.getByRole('button', { name: 'Tests' }));
        expect(nav.queryByRole('link', { name: 'Schedules' })).toBeNull();
        expect(nav.getByRole('button', { name: 'Tests' }).hasAttribute('data-active')).toBe(true);
    });

    it('navigates directly from Runs', async () => {
        const user = userEvent.setup();
        renderAt('/overview');
        await user.click(desktopNav().getByRole('link', { name: 'Runs' }));
        expect(location()).toBe('/runs');
        expect(desktopNav().getByRole('link', { name: 'Runs' }).hasAttribute('data-active')).toBe(true);
    });

    it('opens Watch on the On-call page', () => {
        renderAt('/on-call');
        const watch = desktopNav().getByRole('button', { name: 'Watch' });
        expect(watch.getAttribute('aria-expanded')).toBe('true');
        expect(desktopNav().getByRole('link', { name: 'On-call' }).getAttribute('aria-current')).toBe('page');
    });

    it('hides Tenant admin and Worker fleet without tenant:manage_settings', async () => {
        const user = userEvent.setup();
        renderAt('/runs');
        await user.click(desktopNav().getByRole('button', { name: 'Setup' }));
        expect(desktopNav().getByRole('link', { name: 'Billing' })).toBeTruthy();
        expect(desktopNav().queryByRole('link', { name: 'Tenant admin' })).toBeNull();
        expect(desktopNav().queryByRole('link', { name: 'Worker fleet' })).toBeNull();
    });

    it('shows Tenant admin with tenant:manage_settings', async () => {
        systemPermissions = ['tenant:manage_settings'];
        const user = userEvent.setup();
        renderAt('/runs');
        await user.click(desktopNav().getByRole('button', { name: 'Setup' }));
        expect(await desktopNav().findByRole('link', { name: 'Tenant admin' })).toBeTruthy();
        expect(desktopNav().getByRole('link', { name: 'Worker fleet' }).getAttribute('href')).toBe('/admin/workers');
    });
});

describe('collapsed rail', () => {
    beforeEach(() => { systemPermissions = []; });

    it('shows icons only, each with an accessible name', () => {
        renderAt('/runs', { collapsed: true });
        const nav = desktopNav();
        for (const n of ['Today', 'Releases', 'Tests', 'Watch', 'Security', 'Setup']) expect(nav.getByRole('button', { name: n })).toBeTruthy();
        expect(nav.getByRole('link', { name: 'Runs' })).toBeTruthy();
        expect(nav.getByRole('link', { name: 'Runs' }).hasAttribute('data-active')).toBe(true);
        expect(nav.queryByRole('link', { name: 'Schedules' })).toBeNull();
    });

    it('opens a group flyout and navigates on click', async () => {
        const user = userEvent.setup();
        renderAt('/runs', { collapsed: true });
        await user.click(desktopNav().getByRole('button', { name: 'Tests' }));
        const menu = await screen.findByRole('menu');
        expect(within(menu).getAllByRole('menuitem').map((i) => i.textContent))
            .toEqual(['Suites', 'Schedules', 'Environments', 'App builds', 'Frontend quality', 'Import Playwright', 'Analytics']);
        await user.click(within(menu).getByRole('menuitem', { name: 'Schedules' }));
        expect(location()).toBe('/schedules');
        expect(desktopNav().getByRole('button', { name: 'Tests' }).hasAttribute('data-active')).toBe(true);
    });

    it('works from the keyboard: Enter opens, Enter navigates, Escape closes', async () => {
        const user = userEvent.setup();
        renderAt('/runs', { collapsed: true });
        desktopNav().getByRole('button', { name: 'Releases' }).focus();
        await user.keyboard('{Enter}');
        expect(await screen.findByRole('menu')).toBeTruthy();
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();

        desktopNav().getByRole('button', { name: 'Releases' }).focus();
        await user.keyboard('{Enter}');
        await screen.findByRole('menu');
        await user.keyboard('{Enter}'); // first item: Quality gate
        expect(location()).toBe('/quality');
    });

    it('applies the same permission filter to the Setup flyout', async () => {
        const user = userEvent.setup();
        renderAt('/runs', { collapsed: true });
        await user.click(desktopNav().getByRole('button', { name: 'Setup' }));
        const menu = await screen.findByRole('menu');
        expect(within(menu).getAllByRole('menuitem')).toHaveLength(8);
        expect(within(menu).queryByRole('menuitem', { name: 'Tenant admin' })).toBeNull();
    });
});
