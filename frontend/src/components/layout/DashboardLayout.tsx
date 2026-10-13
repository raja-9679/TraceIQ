import { useState, useEffect } from 'react';
import { Outlet, Link, useLocation, matchPath } from 'react-router-dom';
import {
    Inbox,
    Flag,
    FlaskConical,
    Play,
    Radio,
    ShieldCheck,
    Settings,
    LogOut,
    Menu,
    X,
    ChevronsUpDown,
    ChevronDown,
    Plus,
    type LucideIcon,
} from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { getProjects } from '@/lib/api';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';

type SubLink = { name: string; href: string };
type Place = {
    name: string;
    href: string;
    icon: LucideIcon;
    // Route patterns that count as "inside" this place, so the sidebar keeps
    // the right place highlighted on detail pages.
    match: string[];
    children: SubLink[];
};

// Redesign A: the old flat menu (one item per feature) regrouped by the
// question you came with. Every existing page keeps a home.
const places: Place[] = [
    {
        name: 'Today', href: '/', icon: Inbox,
        match: ['/', '/triage', '/proposals', '/flaky-tests', '/visual-review'],
        children: [
            { name: 'Inbox', href: '/' },
            { name: 'Failure triage', href: '/triage' },
            { name: 'Proposals', href: '/proposals' },
            { name: 'Flaky tests', href: '/flaky-tests' },
            { name: 'Visual review', href: '/visual-review' },
        ],
    },
    {
        name: 'Releases', href: '/quality', icon: Flag,
        match: ['/quality', '/traceability', '/analytics'],
        children: [
            { name: 'Quality gate', href: '/quality' },
            { name: 'Traceability', href: '/traceability' },
            { name: 'Analytics', href: '/analytics' },
        ],
    },
    {
        name: 'Tests', href: '/suites', icon: FlaskConical,
        match: ['/suites', '/suites/*', '/import-playwright', '/environments', '/app-builds', '/schedules'],
        children: [
            { name: 'Suites', href: '/suites' },
            { name: 'Schedules', href: '/schedules' },
            { name: 'Environments', href: '/environments' },
            { name: 'App builds', href: '/app-builds' },
            { name: 'Import Playwright', href: '/import-playwright' },
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

const setupLinks: SubLink[] = [
    { name: 'Settings', href: '/settings' },
    { name: 'Workspace', href: '/workspace' },
    { name: 'Users', href: '/users' },
    { name: 'Tenant admin', href: '/admin/users' },
    { name: 'API keys', href: '/api-keys' },
    { name: 'Webhooks', href: '/webhooks' },
    { name: 'Issue trackers', href: '/issue-trackers' },
    { name: 'AI usage', href: '/ai-usage' },
    { name: 'Billing', href: '/billing' },
];

const isIn = (pathname: string, patterns: string[]) =>
    patterns.some((p) => matchPath({ path: p, end: true }, pathname));

function initials(name?: string | null, email?: string | null) {
    const src = (name || email || '?').trim();
    const parts = src.split(/[\s@.]+/).filter(Boolean);
    return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase();
}

export default function DashboardLayout() {
    const { user, logout } = useAuth();
    const [mobileOpen, setMobileOpen] = useState(false);
    const location = useLocation();
    const inSetup = isIn(location.pathname, setupLinks.map((l) => l.href));
    const [setupToggled, setSetupOpen] = useState(false);
    const setupOpen = setupToggled || inSetup;

    // Close the mobile drawer on navigation (adjusting state during render,
    // not in an effect, so it doesn't cost an extra paint).
    const [lastPath, setLastPath] = useState(location.pathname);
    if (lastPath !== location.pathname) {
        setLastPath(location.pathname);
        setMobileOpen(false);
    }

    const [selectedProjectId, setSelectedProjectId] = useState<number | null>(() => {
        const saved = localStorage.getItem('activeProjectId');
        return saved ? parseInt(saved) : null;
    });

    const { data: projects } = useQuery({
        queryKey: ['projects'],
        queryFn: () => getProjects()
    });

    const activeProject = projects?.find(p => p.id === selectedProjectId);

    useEffect(() => {
        if (projects && projects.length > 0) {
            // Check if current selectedProjectId is still valid
            if (selectedProjectId && !projects.find(p => p.id === selectedProjectId)) {
                setSelectedProjectId(projects[0].id);
                localStorage.setItem('activeProjectId', projects[0].id.toString());
                window.dispatchEvent(new Event('projectChanged'));
            } else if (!selectedProjectId) {
                setSelectedProjectId(projects[0].id);
                localStorage.setItem('activeProjectId', projects[0].id.toString());
                window.dispatchEvent(new Event('projectChanged'));
            } else {
                localStorage.setItem('activeProjectId', selectedProjectId.toString());
            }
        }
    }, [selectedProjectId, projects]);

    const handleProjectSelect = (id: number) => {
        setSelectedProjectId(id);
        // Refresh page or trigger state update in children
        window.dispatchEvent(new Event('projectChanged'));
    };

    const sidebar = (
        <div className="flex h-full flex-col gap-6 overflow-y-auto px-4 py-5">
            <div className="flex items-center justify-between">
                <Link to="/" className="flex min-h-11 items-center gap-2.5 px-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-ink">
                        <svg width="18" height="13" viewBox="0 0 28 20" fill="none" aria-hidden="true">
                            <path d="M1 10h6l3-8 4 16 3-8h10" stroke="#FFFFFF" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </span>
                    <span className="text-lg font-bold tracking-tight text-ink">TraceIQ</span>
                </Link>
                <button
                    type="button"
                    onClick={() => setMobileOpen(false)}
                    className="flex h-10 w-10 items-center justify-center rounded-full text-ink-soft hover:bg-white/60 lg:hidden"
                    aria-label="Close menu"
                >
                    <X className="h-5 w-5" />
                </button>
            </div>

            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        type="button"
                        className="flex min-h-12 items-center gap-2.5 rounded-2xl border border-white/90 bg-white/55 px-3 text-left text-sm text-ink transition-colors hover:bg-white/75"
                    >
                        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-xs font-bold">
                            {initials(activeProject?.name)}
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate font-semibold">{activeProject?.name || 'Select project'}</span>
                            <span className="text-xs text-ink-soft">{projects?.length ?? 0} projects</span>
                        </span>
                        <ChevronsUpDown className="h-4 w-4 text-ink-soft" />
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56">
                    <DropdownMenuLabel>Switch project</DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {projects?.map((p) => (
                        <DropdownMenuItem
                            key={p.id}
                            onClick={() => handleProjectSelect(p.id)}
                            className={p.id === selectedProjectId ? "bg-accent" : ""}
                        >
                            <span className="truncate">{p.name}</span>
                        </DropdownMenuItem>
                    ))}
                    {(!projects || projects.length === 0) && (
                        <DropdownMenuItem disabled>
                            No projects found
                        </DropdownMenuItem>
                    )}
                    <DropdownMenuSeparator />
                    <Link to="/workspace">
                        <DropdownMenuItem>
                            <Plus className="h-4 w-4 mr-2" />
                            Manage projects
                        </DropdownMenuItem>
                    </Link>
                </DropdownMenuContent>
            </DropdownMenu>

            <nav aria-label="Main" className="flex flex-col gap-0.5">
                {places.map((place) => {
                    const active = isIn(location.pathname, place.match);
                    return (
                        <div key={place.name} className="flex flex-col">
                            <Link
                                to={place.href}
                                aria-current={active ? 'page' : undefined}
                                className={cn(
                                    'flex min-h-[42px] items-center gap-3 rounded-xl px-3 text-sm transition-colors',
                                    active
                                        ? 'bg-white font-semibold text-ink shadow-[0_2px_8px_rgba(28,27,34,0.08)]'
                                        : 'font-medium text-ink/80 hover:bg-white/50'
                                )}
                            >
                                <place.icon className="h-[18px] w-[18px]" strokeWidth={1.9} />
                                <span className="flex-1">{place.name}</span>
                            </Link>
                            {active && place.children.length > 1 && (
                                <div className="ml-[22px] mt-1 mb-1 flex flex-col gap-0.5 border-l border-ink/10 pl-3">
                                    {place.children.map((c) => {
                                        const on = location.pathname === c.href;
                                        return (
                                            <Link
                                                key={c.href}
                                                to={c.href}
                                                aria-current={on ? 'page' : undefined}
                                                className={cn(
                                                    'flex min-h-9 items-center rounded-lg px-2.5 text-[13px] transition-colors',
                                                    on ? 'bg-white/70 font-semibold text-ink' : 'text-ink-soft hover:bg-white/50 hover:text-ink'
                                                )}
                                            >
                                                {c.name}
                                            </Link>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    );
                })}
            </nav>

            <div className="mt-auto flex flex-col gap-0.5">
                <button
                    type="button"
                    onClick={() => setSetupOpen(!setupOpen)}
                    aria-expanded={setupOpen}
                    className={cn(
                        'flex min-h-10 items-center gap-3 rounded-xl px-3 text-sm transition-colors',
                        inSetup ? 'font-semibold text-ink' : 'text-ink/80 hover:bg-white/50'
                    )}
                >
                    <Settings className="h-[18px] w-[18px]" strokeWidth={1.9} />
                    <span className="flex-1 text-left">Setup</span>
                    <ChevronDown className={cn('h-4 w-4 transition-transform', setupOpen && 'rotate-180')} />
                </button>
                {setupOpen && (
                    <div className="ml-[22px] mb-1 flex flex-col gap-0.5 border-l border-ink/10 pl-3">
                        {setupLinks.map((l) => {
                            const on = location.pathname === l.href;
                            return (
                                <Link
                                    key={l.href}
                                    to={l.href}
                                    aria-current={on ? 'page' : undefined}
                                    className={cn(
                                        'flex min-h-9 items-center rounded-lg px-2.5 text-[13px] transition-colors',
                                        on ? 'bg-white/70 font-semibold text-ink' : 'text-ink-soft hover:bg-white/50 hover:text-ink'
                                    )}
                                >
                                    {l.name}
                                </Link>
                            );
                        })}
                    </div>
                )}

                <div className="mt-2 flex items-center gap-2.5 rounded-xl px-3 py-2.5">
                    <span className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-ink text-xs font-bold text-white">
                        {initials(user?.full_name, user?.email)}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col text-[13px]">
                        <span className="truncate font-semibold text-ink">{user?.full_name || 'User'}</span>
                        <span className="truncate text-ink-soft">{user?.email}</span>
                    </span>
                    <button
                        type="button"
                        onClick={logout}
                        className="flex h-9 w-9 flex-none items-center justify-center rounded-full text-ink-soft hover:bg-white/60 hover:text-ink"
                        aria-label="Log out"
                        title="Log out"
                    >
                        <LogOut className="h-4 w-4" />
                    </button>
                </div>
            </div>
        </div>
    );

    return (
        <div className="min-h-screen bg-background">
            {/* Desktop sidebar */}
            <aside
                aria-label="Workspace"
                className="bg-tiq-gradient fixed inset-y-0 left-0 z-40 hidden w-64 border-r border-white/80 lg:block"
            >
                {sidebar}
            </aside>

            {/* Mobile drawer */}
            {mobileOpen && (
                <>
                    <div
                        className="fixed inset-0 z-40 bg-ink/30 backdrop-blur-sm lg:hidden"
                        onClick={() => setMobileOpen(false)}
                    />
                    <aside
                        aria-label="Workspace"
                        className="fixed inset-y-0 left-0 z-50 w-72 bg-[#F6EEF0] lg:hidden"
                    >
                        <div className="bg-tiq-gradient h-full">{sidebar}</div>
                    </aside>
                </>
            )}

            <div className="lg:pl-64">
                {/* Mobile top bar; on desktop the sidebar carries everything */}
                <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur lg:hidden">
                    <button
                        type="button"
                        onClick={() => setMobileOpen(true)}
                        className="flex h-10 w-10 items-center justify-center rounded-full text-ink hover:bg-secondary"
                        aria-label="Open menu"
                    >
                        <Menu className="h-5 w-5" />
                    </button>
                    <span className="truncate text-sm font-semibold text-ink">{activeProject?.name || 'TraceIQ'}</span>
                </header>

                <main className="p-4 sm:p-6 lg:p-8">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}
