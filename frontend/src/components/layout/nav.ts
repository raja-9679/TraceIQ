import { matchPath } from 'react-router-dom';
import {
    BarChart3,
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
        // Requirements → cases → incidents is the trace chain a release is judged on.
        match: ['/quality', '/release-readiness', '/release-signoff', '/requirements', '/traceability', '/incidents'],
        children: [
            { name: 'Quality gate', href: '/quality' },
            { name: 'Release readiness', href: '/release-readiness' },
            { name: 'Release sign-off', href: '/release-signoff' },
            { name: 'Requirements', href: '/requirements' },
            { name: 'Traceability', href: '/traceability' },
            { name: 'Incidents', href: '/incidents' },
        ],
    },
    {
        // WP12: summaries for people who read results rather than write tests
        // (leadership, PM, CPO), plus delivery, reliability and coverage views.
        name: 'Reports', href: '/reports/summary', icon: BarChart3,
        match: ['/reports/*', '/journey-coverage', '/business-journeys', '/flag-coverage', '/benchmarks', '/costs'],
        children: [
            { name: 'Executive summary', href: '/reports/summary' },
            { name: 'Portfolio', href: '/reports/portfolio' },
            { name: 'Delivery (DORA)', href: '/reports/delivery' },
            { name: 'SLOs', href: '/reports/slo' },
            { name: 'Flake trends', href: '/reports/flake-trends' },
            { name: 'Dependencies', href: '/reports/dependencies' },
            { name: 'Journey coverage', href: '/journey-coverage' },
            { name: 'Business journeys', href: '/business-journeys' },
            { name: 'Flag coverage', href: '/flag-coverage' },
            { name: 'Benchmarks', href: '/benchmarks' },
            { name: 'Costs', href: '/costs' },
            { name: 'PoC report', href: '/reports/poc' },
        ],
    },
    {
        name: 'Tests', href: '/suites', icon: FlaskConical,
        // Analytics lives here: it reports per-project test effectiveness, not a release.
        match: ['/suites', '/suites/*', '/import-playwright', '/test-packs', '/environments', '/app-builds', '/schedules', '/analytics', '/frontend-quality'],
        children: [
            { name: 'Suites', href: '/suites' },
            { name: 'Schedules', href: '/schedules' },
            { name: 'Environments', href: '/environments' },
            { name: 'App builds', href: '/app-builds' },
            { name: 'Frontend quality', href: '/frontend-quality' },
            { name: 'Import Playwright', href: '/import-playwright' },
            { name: 'Test packs', href: '/test-packs' },
            { name: 'Analytics', href: '/analytics' },
        ],
    },
    {
        name: 'Runs', href: '/runs', icon: Play,
        match: ['/runs', '/runs/*'],
        children: [],
    },
    {
        // On-call (paging channels + maintenance windows) is where monitor alerts escalate.
        name: 'Watch', href: '/monitors', icon: Radio,
        match: ['/monitors', '/on-call'],
        children: [
            { name: 'Monitors', href: '/monitors' },
            { name: 'On-call', href: '/on-call' },
        ],
    },
    {
        name: 'Security', href: '/security', icon: ShieldCheck,
        // Estate (domain/TLS/header sweeps) and Script inventory (third-party
        // scripts on payment pages) are posture checks, so they live here too.
        // WP13: compliance (controls, evidence packs, audit log) and the public
        // trust center are the security/compliance owner's tools too.
        match: ['/security', '/access-matrix', '/access-reviews', '/estate', '/script-inventory',
            '/compliance/*', '/audit-log', '/trust-center'],
        children: [
            { name: 'Scans & findings', href: '/security' },
            { name: 'Access matrix', href: '/access-matrix' },
            { name: 'Access reviews', href: '/access-reviews' },
            { name: 'Estate', href: '/estate' },
            { name: 'Script inventory', href: '/script-inventory' },
            { name: 'Controls', href: '/compliance/controls' },
            { name: 'Evidence packs', href: '/compliance/evidence-packs' },
            { name: 'Audit log', href: '/audit-log' },
            { name: 'Trust center', href: '/trust-center' },
        ],
    },
];

export const setupLinks: SubLink[] = [
    { name: 'My settings', href: '/settings' },
    { name: 'Project settings', href: '/project-settings' },
    { name: 'Workspace', href: '/workspace' },
    { name: 'Users', href: '/users' },
    // Same check as backend/app/api/admin.py:get_current_tenant_admin.
    { name: 'Tenant admin', href: '/admin/users', permission: 'tenant:manage_settings' },
    // Backend guard is instance admin (stricter); the frontend has no flag for
    // that, so this hides it from non-admins and the page explains a 403.
    { name: 'Worker fleet', href: '/admin/workers', permission: 'tenant:manage_settings' },
    // Capacity planning; any signed-in user (pure calculator, no data).
    { name: 'Sizing calculator', href: '/sizing' },
    // Server-wide configuration; same instance-admin story as Worker fleet.
    { name: 'Instance settings', href: '/instance-settings', permission: 'tenant:manage_settings' },
    { name: 'API keys', href: '/api-keys' },
    { name: 'Webhooks', href: '/webhooks' },
    { name: 'Issue trackers', href: '/issue-trackers' },
    { name: 'AI usage', href: '/ai-usage' },
    // WP14b: workspace-admin pages; they explain a 403 to everyone else.
    { name: 'Policies', href: '/policies' },
    { name: 'Agents', href: '/agents' },
    { name: 'Billing', href: '/billing' },
];

export const isIn = (pathname: string, patterns: string[]) =>
    patterns.some((p) => matchPath({ path: p, end: true }, pathname));

/** A group opens a submenu; anything else (Runs) is a direct link. */
export const isGroup = (place: Place) => place.children.length > 1;
