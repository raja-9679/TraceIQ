import { matchPath } from 'react-router-dom';
import {
    Inbox,
    Flag,
    FlaskConical,
    Play,
    Radio,
    ShieldCheck,
    type LucideIcon,
} from 'lucide-react';

// Sidebar information architecture. DashboardLayout renders it (expanded
// sidebar, collapsed icon rail, mobile drawer) and nav.test.tsx pins it, so
// this is the one place a destination is added, moved or renamed.

export type SubLink = {
    name: string;
    href: string;
    /** Hide unless usePermission().can(permission) — mirrors the backend guard. */
    permission?: string;
};

export type Place = {
    name: string;
    href: string;
    icon: LucideIcon;
    // Route patterns that count as "inside" this place, so the sidebar keeps
    // the right place highlighted on detail pages.
    match: string[];
    children: SubLink[];
};

// The old flat menu (one item per feature) regrouped by the question you came
// with. Every existing page keeps a home; routes are unchanged.
export const places: Place[] = [
    {
        // "/" redirects to /overview (the old Dashboard), so it belongs here too.
        name: 'Today', href: '/overview', icon: Inbox,
        match: ['/', '/overview', '/triage', '/proposals', '/flaky-tests', '/visual-review'],
        children: [
            { name: 'Overview', href: '/overview' },
            { name: 'Failure triage', href: '/triage' },
            { name: 'Proposals', href: '/proposals' },
            { name: 'Flaky tests', href: '/flaky-tests' },
            { name: 'Visual review', href: '/visual-review' },
        ],
    },
    {
        name: 'Releases', href: '/quality', icon: Flag,
        match: ['/quality', '/traceability'],
        children: [
            { name: 'Quality gate', href: '/quality' },
            { name: 'Traceability', href: '/traceability' },
        ],
    },
    {
        name: 'Tests', href: '/suites', icon: FlaskConical,
        // Analytics lives here: it reports per-project test effectiveness, not a release.
        match: ['/suites', '/suites/*', '/import-playwright', '/environments', '/app-builds', '/schedules', '/analytics'],
        children: [
            { name: 'Suites', href: '/suites' },
            { name: 'Schedules', href: '/schedules' },
            { name: 'Environments', href: '/environments' },
            { name: 'App builds', href: '/app-builds' },
            { name: 'Import Playwright', href: '/import-playwright' },
            { name: 'Analytics', href: '/analytics' },
        ],
    },
    {
        name: 'Runs', href: '/runs', icon: Play,
        match: ['/runs', '/runs/*'],
        children: [],
    },
    {
        name: 'Watch', href: '/monitors', icon: Radio,
        match: ['/monitors'],
        children: [],
    },
    {
        name: 'Security', href: '/security', icon: ShieldCheck,
        match: ['/security'],
        children: [],
    },
];

export const setupLinks: SubLink[] = [
    { name: 'Settings', href: '/settings' },
    { name: 'Workspace', href: '/workspace' },
    { name: 'Users', href: '/users' },
    // Same check as backend/app/api/admin.py:get_current_tenant_admin.
    { name: 'Tenant admin', href: '/admin/users', permission: 'tenant:manage_settings' },
    { name: 'API keys', href: '/api-keys' },
    { name: 'Webhooks', href: '/webhooks' },
    { name: 'Issue trackers', href: '/issue-trackers' },
    { name: 'AI usage', href: '/ai-usage' },
    { name: 'Billing', href: '/billing' },
];

export const isIn = (pathname: string, patterns: string[]) =>
    patterns.some((p) => matchPath({ path: p, end: true }, pathname));

/** A group opens a submenu; anything else (Runs, Watch, Security) is a direct link. */
export const isGroup = (place: Place) => place.children.length > 1;
