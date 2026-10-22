import { useState, useEffect, type ReactNode } from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';
import {
    Settings,
    LogOut,
    Menu,
    X,
    PanelLeftClose,
    PanelLeftOpen,
    ChevronsUpDown,
    ChevronDown,
    Plus,
    Sparkles,
    type LucideIcon,
} from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { getProjects, getWorkspaces } from '@/lib/api';
import DemoBadge from '@/components/demo/DemoBadge';
import AssistantDrawer from '@/components/assistant/AssistantDrawer';
import { usePermission } from '@/hooks/usePermission';
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
import { places, setupLinks, setupTone, toneClass, isIn, isGroup, activeHref, type SubLink, type Tone } from './nav';

function initials(name?: string | null, email?: string | null) {
    const src = (name || email || '?').trim();
    const parts = src.split(/[\s@.]+/).filter(Boolean);
    return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase();
}

// Hover/focus label for the icon rail; sits to the right of the icon.
function RailLabel({ children }: { children: string }) {
    return (
        <span className="pointer-events-none absolute left-full top-1/2 z-50 ml-3 -translate-y-1/2 whitespace-nowrap rounded-lg bg-ink px-2.5 py-1 text-xs font-medium text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
            {children}
        </span>
    );
}

function RailLink({ to, label, active, children }: { to: string; label: string; active: boolean; children: ReactNode }) {
    return (
        <Link
            to={to}
            aria-label={label}
            aria-current={active ? 'page' : undefined}
            data-active={active || undefined}
            className={cn(
                'group relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors',
                active ? 'bg-white text-ink shadow-[0_2px_8px_rgba(28,27,34,0.08)]' : 'text-ink/80 hover:bg-white/60'
            )}
        >
            {children}
            <RailLabel>{label}</RailLabel>
        </Link>
    );
}

function RailButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
    return (
        <button
            type="button"
            onClick={onClick}
            aria-label={label}
            className="group relative flex h-10 w-10 items-center justify-center rounded-xl text-ink-soft hover:bg-white/60 hover:text-ink"
        >
            {children}
            <RailLabel>{label}</RailLabel>
        </button>
    );
}

// A place's icon on its pastel tile (same colours as the user guide's groups).
function PlaceIcon({ icon: Icon, tone, size = 'md' }: { icon: LucideIcon; tone: Tone; size?: 'md' | 'lg' }) {
    return (
        <span
            aria-hidden="true"
            className={cn(
                'flex flex-none items-center justify-center',
                size === 'lg' ? 'h-8 w-8 rounded-[10px]' : 'h-[26px] w-[26px] rounded-lg',
                toneClass[tone],
            )}
        >
            <Icon className={size === 'lg' ? 'h-[17px] w-[17px]' : 'h-[15px] w-[15px]'} strokeWidth={2} />
        </span>
    );
}

const placeRow = 'flex min-h-[42px] w-full items-center gap-3 rounded-xl px-3 text-sm transition-colors';
const placeActive = 'bg-white font-semibold text-ink shadow-[0_2px_8px_rgba(28,27,34,0.08)]';
const placeIdle = 'font-medium text-ink/80 hover:bg-white/50';

// Expanded sidebar group: the row toggles its sub-links; it is highlighted
// while the current page is inside it, open or not.
function NavGroup({ label, icon, active, open, onToggle, links, pathname }: {
    label: string;
    icon: ReactNode;
    active: boolean;
    open: boolean;
    onToggle: () => void;
    links: SubLink[];
    pathname: string;
}) {
    const id = `nav-group-${label.toLowerCase()}`;
    return (
        <div className="flex flex-col">
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={open}
                aria-controls={id}
                data-active={active || undefined}
                className={cn(placeRow, active ? placeActive : placeIdle)}
            >
                {icon}
                <span className="flex-1 text-left">{label}</span>
                <ChevronDown className={cn('h-4 w-4 text-ink-soft transition-transform', open && 'rotate-180')} />
            </button>
            {open && (
                <div id={id} className="ml-[22px] mt-1 mb-1 flex flex-col gap-0.5 border-l border-ink/10 pl-3">
                    {links.map((l) => {
                        const on = l.href === activeHref(pathname, links);
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
        </div>
    );
}

// Rail flyout items. asChild makes the menu item the link itself (one element
// carries role=menuitem and the href) instead of a div nested in an <a>.
function RailMenuLinks({ links, pathname }: { links: SubLink[]; pathname: string }) {
    return (
        <>
            {links.map((l) => (
                <DropdownMenuItem key={l.href} asChild className={l.href === activeHref(pathname, links) ? "bg-accent" : ""}>
                    <Link to={l.href} aria-current={l.href === activeHref(pathname, links) ? 'page' : undefined}>{l.name}</Link>
                </DropdownMenuItem>
            ))}
        </>
    );
}

export default function DashboardLayout() {
    const { user, logout } = useAuth();
    const [mobileOpen, setMobileOpen] = useState(false);
    // Desktop only: collapse the sidebar to an icon rail to give a page more
    // width. Remembered per browser; storage can be blocked.
    const [collapsed, setCollapsedState] = useState<boolean>(() => {
        try { return localStorage.getItem('traceiq.sidebarCollapsed') === '1'; } catch { return false; }
    });
    const setCollapsed = (v: boolean) => {
        setCollapsedState(v);
        try { localStorage.setItem('traceiq.sidebarCollapsed', v ? '1' : '0'); } catch { /* per-browser nicety only */ }
    };
    const location = useLocation();
    const { can } = usePermission();
    // Links the backend would refuse are not offered (it still enforces).
    const visibleSetup = setupLinks.filter((l) => !l.permission || can(l.permission));
    const inSetup = isIn(location.pathname, setupLinks.map((l) => l.href));
    // Expanded sidebar: a group is open when the user opened it, or — until
    // they toggle it — when the current page is inside it.
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const groupOpen = (name: string, active: boolean) => openGroups[name] ?? active;
    const toggleGroup = (name: string, active: boolean) =>
        setOpenGroups((g) => ({ ...g, [name]: !(g[name] ?? active) }));

    // Close the mobile drawer on navigation (adjusting state during render,
    // not in an effect, so it doesn't cost an extra paint).
    const [lastPath, setLastPath] = useState(location.pathname);
    if (lastPath !== location.pathname) {
        setLastPath(location.pathname);
        setMobileOpen(false);
    }

    // "Ask TraceIQ" drawer (WP14a). Opened from the sidebar, the icon rail and
    // the phone header.
    const [assistantOpen, setAssistantOpen] = useState(false);

    const [selectedProjectId, setSelectedProjectId] = useState<number | null>(() => {
        const saved = localStorage.getItem('activeProjectId');
        return saved ? parseInt(saved) : null;
    });

    const { data: projects } = useQuery({
        queryKey: ['projects'],
        queryFn: () => getProjects()
    });

    const activeProject = projects?.find(p => p.id === selectedProjectId);
    // Projects of a seeded demo workspace (WP13c) carry a DEMO badge wherever
    // they are listed, so generated data is never mistaken for real.
    const { data: workspaces } = useQuery({ queryKey: ['workspaces'], queryFn: () => getWorkspaces() });
    const demoWorkspaceIds = new Set((workspaces || []).filter((w) => w.is_demo).map((w) => w.id));
    const isDemoProject = (p?: { workspace_id: number }) => !!p && demoWorkspaceIds.has(p.workspace_id);

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
        // Persist before announcing: listeners read localStorage on the event,
        // and the effect above only writes it after the next render.
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };

    const projectMenu = (
        <>
            <DropdownMenuLabel>Switch project</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {projects?.map((p) => (
                <DropdownMenuItem
                    key={p.id}
                    onClick={() => handleProjectSelect(p.id)}
                    className={p.id === selectedProjectId ? "bg-accent" : ""}
                >
                    <span className="truncate">{p.name}</span>
                    {isDemoProject(p) && <DemoBadge className="ml-auto" />}
                </DropdownMenuItem>
            ))}
            {(!projects || projects.length === 0) && (
                <DropdownMenuItem disabled>
                    No projects found
                </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
                <Link to="/workspace">
                    <Plus className="h-4 w-4 mr-2" />
                    Manage projects
                </Link>
            </DropdownMenuItem>
        </>
    );

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
                <button
                    type="button"
                    onClick={() => setCollapsed(true)}
                    className="hidden h-10 w-10 items-center justify-center rounded-full text-ink-soft hover:bg-white/60 hover:text-ink lg:flex"
                    aria-label="Collapse sidebar"
                    title="Collapse sidebar"
                >
                    <PanelLeftClose className="h-5 w-5" />
                </button>
            </div>

            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        type="button"
                        // Same name as the collapsed rail's switcher; without it the
                        // button's name was its initials + name + count run together.
                        aria-label={`Project: ${activeProject?.name || 'Select project'}`}
                        className="flex min-h-12 items-center gap-2.5 rounded-2xl border border-white/90 bg-white/55 px-3 text-left text-sm text-ink transition-colors hover:bg-white/75"
                    >
                        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-xs font-bold">
                            {initials(activeProject?.name)}
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col">
                            <span className="flex min-w-0 items-center gap-1.5">
                                <span className="truncate font-semibold">{activeProject?.name || 'Select project'}</span>
                                {isDemoProject(activeProject) && <DemoBadge />}
                            </span>
                            <span className="text-xs text-ink-soft">{projects?.length ?? 0} projects</span>
                        </span>
                        <ChevronsUpDown className="h-4 w-4 text-ink-soft" />
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56">
                    {projectMenu}
                </DropdownMenuContent>
            </DropdownMenu>

            <button
                type="button"
                onClick={() => setAssistantOpen(true)}
                className="flex min-h-10 items-center justify-center gap-2 rounded-full bg-ink px-4 text-sm font-semibold text-white transition-colors hover:bg-ink/85"
            >
                <Sparkles className="h-4 w-4" aria-hidden="true" />
                Ask TraceIQ
            </button>

            <nav aria-label="Main" className="flex flex-col gap-0.5">
                {places.map((place) => {
                    const active = isIn(location.pathname, place.match);
                    if (!isGroup(place)) {
                        return (
                            <Link
                                key={place.name}
                                to={place.href}
                                aria-current={active ? 'page' : undefined}
                                data-active={active || undefined}
                                className={cn(placeRow, active ? placeActive : placeIdle)}
                            >
                                <PlaceIcon icon={place.icon} tone={place.tone} />
                                <span className="flex-1">{place.name}</span>
                            </Link>
                        );
                    }
                    const open = groupOpen(place.name, active);
                    return (
                        <NavGroup
                            key={place.name}
                            label={place.name}
                            icon={<PlaceIcon icon={place.icon} tone={place.tone} />}
                            active={active}
                            open={open}
                            onToggle={() => toggleGroup(place.name, active)}
                            links={place.children}
                            pathname={location.pathname}
                        />
                    );
                })}
            </nav>

            <div className="mt-auto flex flex-col gap-0.5">
                <NavGroup
                    label="Setup"
                    icon={<PlaceIcon icon={Settings} tone={setupTone} />}
                    active={inSetup}
                    open={groupOpen('Setup', inSetup)}
                    onToggle={() => toggleGroup('Setup', inSetup)}
                    links={visibleSetup}
                    pathname={location.pathname}
                />

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

    // Collapsed desktop sidebar: icons only, each with a label that slides
    // out on hover/focus so nothing is unlabelled.
    const rail = (
        <div className="flex h-full flex-col items-center gap-4 py-5">
            <Link to="/" aria-label="TraceIQ home" className="flex h-10 w-10 items-center justify-center">
                <span className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-ink">
                    <svg width="18" height="13" viewBox="0 0 28 20" fill="none" aria-hidden="true">
                        <path d="M1 10h6l3-8 4 16 3-8h10" stroke="#FFFFFF" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                </span>
            </Link>
            <RailButton label="Show sidebar" onClick={() => setCollapsed(false)}>
                <PanelLeftOpen className="h-5 w-5" />
            </RailButton>

            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        type="button"
                        aria-label={`Project: ${activeProject?.name || 'Select project'}`}
                        title={activeProject?.name || 'Select project'}
                        className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/90 bg-white/70 text-xs font-bold text-ink hover:bg-white"
                    >
                        {initials(activeProject?.name)}
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="right" align="start" sideOffset={10} className="w-56">
                    {projectMenu}
                </DropdownMenuContent>
            </DropdownMenu>

            <RailButton label="Ask TraceIQ" onClick={() => setAssistantOpen(true)}>
                <Sparkles className="h-[18px] w-[18px]" strokeWidth={1.9} />
            </RailButton>

            <nav aria-label="Main" className="flex flex-col items-center gap-1">
                {places.map((place) => {
                    const active = isIn(location.pathname, place.match);
                    if (!isGroup(place)) {
                        return (
                            <RailLink key={place.name} to={place.href} label={place.name} active={active}>
                                <PlaceIcon icon={place.icon} tone={place.tone} size="lg" />
                            </RailLink>
                        );
                    }
                    // Grouped places open their pages in a menu, so every page
                    // stays one click away without expanding the sidebar.
                    return (
                        <DropdownMenu key={place.name}>
                            <DropdownMenuTrigger asChild>
                                <button
                                    type="button"
                                    aria-label={place.name}
                                    data-active={active || undefined}
                                    className={cn(
                                        'group relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors',
                                        active ? 'bg-white text-ink shadow-[0_2px_8px_rgba(28,27,34,0.08)]' : 'text-ink/80 hover:bg-white/60'
                                    )}
                                >
                                    <PlaceIcon icon={place.icon} tone={place.tone} size="lg" />
                                    <RailLabel>{place.name}</RailLabel>
                                </button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent side="right" align="start" sideOffset={10} className="w-52">
                                <DropdownMenuLabel>{place.name}</DropdownMenuLabel>
                                <DropdownMenuSeparator />
                                <RailMenuLinks links={place.children} pathname={location.pathname} />
                            </DropdownMenuContent>
                        </DropdownMenu>
                    );
                })}
            </nav>

            <div className="mt-auto flex flex-col items-center gap-1">
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <button
                            type="button"
                            aria-label="Setup"
                            data-active={inSetup || undefined}
                            className={cn(
                                'group relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors',
                                inSetup ? 'bg-white text-ink shadow-[0_2px_8px_rgba(28,27,34,0.08)]' : 'text-ink/80 hover:bg-white/60'
                            )}
                        >
                            <PlaceIcon icon={Settings} tone={setupTone} size="lg" />
                            <RailLabel>Setup</RailLabel>
                        </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent side="right" align="end" sideOffset={10} className="w-52">
                        <DropdownMenuLabel>Setup</DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        <RailMenuLinks links={visibleSetup} pathname={location.pathname} />
                    </DropdownMenuContent>
                </DropdownMenu>
                <span
                    className="mt-2 flex h-8 w-8 items-center justify-center rounded-full bg-ink text-xs font-bold text-white"
                    title={user?.email || undefined}
                >
                    {initials(user?.full_name, user?.email)}
                </span>
                <RailButton label="Log out" onClick={logout}>
                    <LogOut className="h-4 w-4" />
                </RailButton>
            </div>
        </div>
    );

    return (
        <div className="min-h-screen bg-background">
            {/* Desktop sidebar */}
            <aside
                aria-label="Workspace"
                className={cn(
                    "bg-tiq-gradient fixed inset-y-0 left-0 z-40 hidden border-r border-ink/[0.06] lg:block print:!hidden",
                    collapsed ? "w-16" : "w-64"
                )}
            >
                {collapsed ? rail : sidebar}
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

            <div className={cn(collapsed ? "lg:pl-16" : "lg:pl-64", "print:!pl-0")}>
                {/* Top bar: phones only — on desktop the sidebar or its icon rail is always there. */}
                <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur lg:hidden print:hidden">
                    <button
                        type="button"
                        onClick={() => setMobileOpen(true)}
                        className="flex h-10 w-10 items-center justify-center rounded-full text-ink hover:bg-secondary lg:hidden"
                        aria-label="Open menu"
                    >
                        <Menu className="h-5 w-5" />
                    </button>
                    <span className="truncate text-sm font-semibold text-ink">{activeProject?.name || 'TraceIQ'}</span>
                    {isDemoProject(activeProject) && <DemoBadge />}
                    <button
                        type="button"
                        onClick={() => setAssistantOpen(true)}
                        className="ml-auto flex h-9 flex-none items-center gap-1.5 rounded-full bg-ink px-3.5 text-[13px] font-semibold text-white hover:bg-ink/85"
                    >
                        <Sparkles className="h-4 w-4" aria-hidden="true" />
                        Ask TraceIQ
                    </button>
                </header>

                <main className="p-4 sm:p-6 lg:p-8 print:p-0">
                    <Outlet />
                </main>
            </div>

            <AssistantDrawer
                open={assistantOpen}
                onOpenChange={setAssistantOpen}
                workspaceId={activeProject?.workspace_id ?? null}
                projectId={activeProject?.id ?? null}
                onSelectProject={handleProjectSelect}
            />
        </div>
    );
}
