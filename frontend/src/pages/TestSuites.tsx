import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, triggerRun, exportTestSuite, importTestSuite, getProjects, getWorkspaces } from '@/lib/api';
import { cn, formatDate } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Plus, Play, FolderOpen, Download, Upload, Search, ChevronRight, LayoutGrid, List,
} from 'lucide-react';
import { useState, useMemo, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import DemoWorkspaceButton from '@/components/demo/DemoWorkspaceButton';
import { usePermission } from "@/hooks/usePermission";
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetTitle,
} from "@/components/ui/sheet";

const MODE_LOOK: Record<string, { tone: Tone; label: string }> = {
    continuous: { tone: 'lavender', label: 'Continuous' },
    separate: { tone: 'pink', label: 'Separate' },
    parallel: { tone: 'mint', label: 'Parallel' },
};

function ExecModePill({ mode }: { mode: string }) {
    const look = MODE_LOOK[mode] ?? { tone: 'neutral' as Tone, label: mode };
    return <Pill tone={look.tone}>{look.label}</Pill>;
}

function SuiteChip() {
    return (
        <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[11px] bg-pastel-lavender text-pastel-ink-lavender" aria-hidden="true">
            <FolderOpen className="h-[17px] w-[17px]" strokeWidth={2.2} />
        </span>
    );
}

type SuiteRef = { id: number; name: string };

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

export default function TestSuites() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const importRef = useRef<HTMLInputElement>(null);
    const [newSuiteName, setNewSuiteName] = useState('');
    const [newSuiteDesc, setNewSuiteDesc] = useState('');
    const [newExecutionMode, setNewExecutionMode] = useState<'continuous' | 'separate'>('continuous');
    const [isCreateSheetOpen, setIsCreateSheetOpen] = useState(false);

    const [searchTerm, setSearchTerm] = useState('');
    const [viewMode, setViewMode] = useState<'card' | 'list'>('list');

    // The sidebar owns project selection.
    const activeProjectId = useActiveProjectId();

    const { data: projects } = useQuery({
        queryKey: ['projects'],
        queryFn: () => getProjects()
    });

    const activeProject = projects?.find(p => p.id === activeProjectId);
    const { can } = usePermission();

    const { data: suites, isLoading } = useQuery({
        queryKey: ['suites', activeProjectId],
        queryFn: () => api.get('/suites', { params: { project_id: activeProjectId } })
            .then(res => res.data.filter((s: any) => !s.parent_id)),
        enabled: !!activeProjectId
    });

    const filteredSuites = useMemo(() => {
        if (!suites) return [];
        if (!searchTerm.trim()) return suites;

        const lowerSearch = searchTerm.toLowerCase();
        return suites.filter((suite: any) =>
            suite.name.toLowerCase().includes(lowerSearch) ||
            (suite.description && suite.description.toLowerCase().includes(lowerSearch))
        );
    }, [suites, searchTerm]);

    // Onboarding: seed a runnable "Sample Project" (Getting Started suite with
    // three demo cases) and switch the workspace to it.
    const { data: workspaces } = useQuery({
        queryKey: ['workspaces'],
        queryFn: () => getWorkspaces(),
    });
    const seedSampleMutation = useMutation({
        mutationFn: async (workspaceId: number) =>
            (await api.post('/onboarding/sample-project', { workspace_id: workspaceId })).data,
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['projects'] });
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            // useActiveProjectId re-reads on this event.
            localStorage.setItem('activeProjectId', String(data.project_id));
            window.dispatchEvent(new Event('projectChanged'));
            toast.success(data.created ? 'Sample project created' : 'Sample project already exists', {
                description: 'Open the Getting Started suite and hit Run Now to see real results.',
            });
        },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || 'Failed to create the sample project');
        },
    });
    const seedTargetWorkspaceId = activeProject?.workspace_id ?? (workspaces && workspaces[0]?.id);

    const createSuite = useMutation({
        mutationFn: (data: { name: string; description?: string; execution_mode: string; project_id: number }) =>
            api.post('/suites', data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            setNewSuiteName('');
            setNewSuiteDesc('');
            setIsCreateSheetOpen(false);
            toast.success('Suite created successfully', {
                description: 'Your new test suite is ready to be configured.',
            });
        },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || 'Failed to create suite');
        }
    });

    // Browser/device fan-out is resolved server-side (case run_matrix >
    // suite settings chain > user Settings defaults).
    const runMutation = useMutation({
        mutationFn: (id: number) => triggerRun(id),
        onSuccess: () => { navigate('/runs'); },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || "Failed to start run");
        }
    });

    const handleCreate = () => {
        if (newSuiteName.trim() && activeProjectId) {
            createSuite.mutate({
                name: newSuiteName,
                description: newSuiteDesc || undefined,
                execution_mode: newExecutionMode,
                project_id: activeProjectId
            });
        }
    };

    const handleExportSuite = async (id: number, name: string) => {
        try {
            const data = await exportTestSuite(id);
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${name.replace(/\s+/g, '_')}_suite.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Suite exported successfully');
        } catch {
            toast.error('Failed to export suite');
        }
    };

    const handleImportSuite = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;
        event.target.value = '';
        try {
            const content = await new Promise<string>((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = (e) => resolve(e.target?.result as string);
                reader.onerror = () => reject(new Error('Failed to read file'));
                reader.readAsText(file);
            });
            const data = JSON.parse(content);
            await importTestSuite(undefined, data, activeProjectId ?? undefined);
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            toast.success('Suite imported successfully');
        } catch (error: any) {
            const responseData = error?.response?.data;
            const detail = responseData?.detail ?? responseData ?? error?.message;
            toast.error('Import failed', { description: typeof detail === 'string' ? detail : 'Unknown error', duration: 8000 });
        }
    };

    const canUpdateProject = activeProjectId
        ? can("project:create_suite", { projectId: activeProjectId, workspaceId: activeProject?.workspace_id })
        : false;
    const canExecuteTest = activeProjectId
        ? can("project:execute_test", { projectId: activeProjectId, workspaceId: activeProject?.workspace_id })
        : false;

    const totalTestCases = suites?.reduce((sum: number, s: any) => sum + (s.total_test_cases || 0), 0) ?? 0;
    const totalModules = suites?.reduce((sum: number, s: any) => sum + (s.total_sub_modules || 0), 0) ?? 0;

    const subtitle = !activeProjectId
        ? 'Pick a project in the sidebar.'
        : suites
            ? `${plural(suites.length, 'suite')} · ${plural(totalTestCases, 'test')} · ${plural(totalModules, 'module')}${activeProject?.name ? ` in ${activeProject.name}` : ''}`
            : 'Suites group your tests and their shared settings.';

    const importButton = (
        <Button variant="outline" disabled={!canUpdateProject} onClick={() => importRef.current?.click()}>
            <Upload className="h-4 w-4" /> Import
        </Button>
    );

    const runButton = (suite: SuiteRef) => (
        <Button
            size="icon"
            onClick={() => runMutation.mutate(suite.id)}
            disabled={runMutation.isPending || !canExecuteTest}
            title={!canExecuteTest ? "Permission required" : "Run this suite"}
            aria-label={`Run ${suite.name}`}
        >
            <Play className="h-4 w-4" fill="currentColor" />
        </Button>
    );

    const exportButton = (suite: SuiteRef) => (
        <Button
            variant="outline"
            size="icon"
            onClick={() => handleExportSuite(suite.id, suite.name)}
            title="Export suite"
            aria-label={`Export ${suite.name}`}
        >
            <Download className="h-4 w-4" />
        </Button>
    );

    const modeButton = (mode: 'card' | 'list', Icon: typeof List, label: string) => (
        <button
            type="button"
            aria-pressed={viewMode === mode}
            onClick={() => setViewMode(mode)}
            className={cn(
                'inline-flex h-9 items-center gap-1.5 rounded-full px-3.5 text-sm font-medium transition-colors',
                viewMode === mode ? 'bg-white text-ink shadow-sm' : 'text-ink-soft hover:text-ink',
            )}
        >
            <Icon className="h-4 w-4" aria-hidden="true" /> {label}
        </button>
    );

    const modeOption = (value: 'continuous' | 'separate', title: string, body: string) => (
        <label className={cn(
            'flex cursor-pointer items-start gap-3 rounded-2xl border bg-white p-4 transition-colors',
            newExecutionMode === value ? 'border-ink' : 'border-border hover:border-ink-muted',
        )}>
            <input
                type="radio"
                name="execution-mode"
                className="mt-0.5 h-[18px] w-[18px] accent-ink"
                checked={newExecutionMode === value}
                onChange={() => setNewExecutionMode(value)}
            />
            <span className="flex flex-col gap-0.5">
                <span className="text-sm font-semibold text-ink">{title}</span>
                <span className="text-sm text-ink-soft">{body}</span>
            </span>
        </label>
    );

    return (
        <div className="flex flex-col gap-5">
            <input ref={importRef} type="file" accept=".json" onChange={handleImportSuite} className="hidden" aria-hidden="true" tabIndex={-1} />

            <Sheet open={isCreateSheetOpen} onOpenChange={setIsCreateSheetOpen}>
                <SheetContent className="flex flex-col gap-0 border-l border-border bg-background p-0 sm:max-w-md">
                    <div className="flex flex-col gap-1.5 border-b border-border bg-white p-6 sm:p-7">
                        <SheetTitle className="text-[22px] font-bold tracking-[-0.02em] text-ink">New suite</SheetTitle>
                        <SheetDescription className="text-sm text-ink-soft">
                            Start a suite now; add tests and sub-modules to it later.
                        </SheetDescription>
                    </div>

                    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6 sm:p-7">
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="new-suite-name" className="text-[13px] font-semibold text-ink">Suite name</label>
                            <Input
                                id="new-suite-name"
                                type="text"
                                value={newSuiteName}
                                onChange={(e) => setNewSuiteName(e.target.value)}
                                placeholder="e.g., Core API Regression"
                                className="h-11 rounded-xl bg-white"
                                autoFocus
                            />
                        </div>

                        <fieldset className="flex flex-col gap-2">
                            <legend className="mb-1.5 text-[13px] font-semibold text-ink">How tests run</legend>
                            {modeOption('continuous', 'Continuous', 'The default. Today each test still runs on its own, as with Separate.')}
                            {modeOption('separate', 'Separate', 'Each test runs on its own, from a clean state.')}
                        </fieldset>

                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="new-suite-desc" className="text-[13px] font-semibold text-ink">
                                Description <span className="font-normal text-ink-soft">(optional)</span>
                            </label>
                            <textarea
                                id="new-suite-desc"
                                value={newSuiteDesc}
                                onChange={(e) => setNewSuiteDesc(e.target.value)}
                                placeholder="What is this suite for?"
                                rows={3}
                                className="w-full resize-none rounded-xl border border-input bg-white px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-ring"
                            />
                        </div>
                    </div>

                    <div className="flex gap-2.5 border-t border-border bg-white p-6">
                        <Button variant="outline" size="lg" className="flex-1 px-4" onClick={() => setIsCreateSheetOpen(false)}>Cancel</Button>
                        <Button
                            size="lg"
                            className="flex-1 px-4"
                            onClick={handleCreate}
                            disabled={!newSuiteName.trim() || createSuite.isPending}
                        >
                            {createSuite.isPending ? 'Creating…' : 'Create suite'}
                        </Button>
                    </div>
                </SheetContent>
            </Sheet>

            <PageHeader
                title="Tests"
                subtitle={subtitle}
                actions={activeProjectId ? (
                    <>
                        {importButton}
                        <Button onClick={() => setIsCreateSheetOpen(true)} disabled={!canUpdateProject}>
                            <Plus className="h-4 w-4" /> New suite
                        </Button>
                    </>
                ) : undefined}
                toolbar={activeProjectId ? (<>
                        <div className="relative w-full sm:w-64">
                            <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-soft" aria-hidden="true" />
                            <Input
                                aria-label="Search suites"
                                placeholder="Search suites"
                                className="h-10 rounded-full bg-white pl-10"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                            />
                        </div>
                        <div role="group" aria-label="Layout" className="flex rounded-full bg-[#EFEDE7] p-1">
                            {modeButton('card', LayoutGrid, 'Cards')}
                            {modeButton('list', List, 'List')}
                        </div>
                </>) : undefined}
            />

            {!activeProjectId ? (
                <EmptyState title="Pick a project">
                    Choose a project in the sidebar to see and manage its test suites.
                    {seedTargetWorkspaceId && (
                        <span className="mt-4 flex flex-wrap justify-center gap-2">
                            <Button
                                variant="outline"
                                onClick={() => seedSampleMutation.mutate(seedTargetWorkspaceId)}
                                disabled={seedSampleMutation.isPending}
                            >
                                {seedSampleMutation.isPending ? 'Seeding…' : 'New here? Create a sample project'}
                            </Button>
                            <DemoWorkspaceButton />
                        </span>
                    )}
                </EmptyState>
            ) : isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2, 3].map((i) => <div key={i} className="h-[76px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : suites?.length === 0 ? (
                <div className="flex flex-col items-center gap-4 rounded-3xl border border-border bg-white px-6 py-12 text-center">
                    <SuiteChip />
                    <div className="flex flex-col gap-1.5">
                        <p className="text-lg font-semibold text-ink">No suites yet</p>
                        <p className="max-w-[52ch] text-sm text-ink-soft">
                            {activeProject?.name ? <><span className="font-medium text-ink">{activeProject.name}</span> has no test suites yet. </> : null}
                            Create your first suite to start organising your tests.
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center justify-center gap-2.5">
                        <Button size="lg" onClick={() => setIsCreateSheetOpen(true)} disabled={!canUpdateProject}>
                            <Plus className="h-4 w-4" /> Create your first suite
                        </Button>
                        <Button size="lg" variant="outline" disabled={!canUpdateProject} onClick={() => importRef.current?.click()}>
                            <Upload className="h-4 w-4" /> Import JSON
                        </Button>
                        {seedTargetWorkspaceId && (
                            <Button
                                size="lg"
                                variant="ghost"
                                onClick={() => seedSampleMutation.mutate(seedTargetWorkspaceId)}
                                disabled={seedSampleMutation.isPending}
                                title="Seeds a Sample Project with three runnable demo tests (UI, API, accessibility)"
                            >
                                {seedSampleMutation.isPending ? 'Seeding…' : 'Try a sample project'}
                            </Button>
                        )}
                        <DemoWorkspaceButton size="lg" />
                    </div>
                </div>
            ) : (
                <>
                    {filteredSuites.length === 0 ? (
                        <EmptyState title="No matches">
                            No suites match “{searchTerm}”.
                            <span className="mt-3 flex justify-center">
                                <Button variant="outline" onClick={() => setSearchTerm('')}>Clear search</Button>
                            </span>
                        </EmptyState>
                    ) : viewMode === 'card' ? (
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                            {filteredSuites.map((suite: any) => (
                                <article key={suite.id} className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-5 transition-shadow hover:shadow-[0_12px_40px_rgba(28,27,34,0.06)]">
                                    <div className="flex items-start justify-between gap-3">
                                        <SuiteChip />
                                        <ExecModePill mode={suite.execution_mode} />
                                    </div>
                                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                                        <h2 className="truncate text-lg font-bold leading-tight tracking-[-0.01em] text-ink" title={suite.name}>
                                            <Link to={`/suites/${suite.id}`} className="hover:underline">{suite.name}</Link>
                                        </h2>
                                        <p className="line-clamp-2 min-h-[40px] text-sm leading-relaxed text-ink-soft">
                                            {suite.description || 'No description'}
                                        </p>
                                    </div>
                                    <p className="text-xs text-ink-soft">
                                        {plural(suite.total_test_cases || 0, 'test')} · {plural(suite.total_sub_modules || 0, 'module')} · created {formatDate(suite.created_at)}
                                    </p>
                                    <div className="flex items-center gap-2 border-t border-[#F0EEE8] pt-4">
                                        <Button asChild variant="outline" className="flex-1">
                                            <Link to={`/suites/${suite.id}`}>Open <ChevronRight className="h-4 w-4" /></Link>
                                        </Button>
                                        {exportButton(suite)}
                                        {runButton(suite)}
                                    </div>
                                </article>
                            ))}
                        </div>
                    ) : (
                        <ul className="overflow-hidden rounded-3xl border border-border bg-white">
                            {filteredSuites.map((suite: any) => (
                                <li key={suite.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[#F0EEE8] px-4 py-3.5 first:border-t-0 hover:bg-background/60 sm:px-5">
                                    <Link to={`/suites/${suite.id}`} className="flex min-w-0 flex-[1_1_260px] items-center gap-3 rounded-xl py-1">
                                        <SuiteChip />
                                        <span className="flex min-w-0 flex-col gap-0.5">
                                            <span className="truncate text-[15px] font-semibold text-ink">{suite.name}</span>
                                            <span className="truncate text-xs text-ink-soft" title={suite.description || undefined}>
                                                {suite.description || `Created ${formatDate(suite.created_at)}`}
                                            </span>
                                        </span>
                                    </Link>
                                    <ExecModePill mode={suite.execution_mode} />
                                    <span className="w-40 text-xs text-ink-soft">
                                        {plural(suite.total_test_cases || 0, 'test')} · {plural(suite.total_sub_modules || 0, 'module')}
                                    </span>
                                    <span className="hidden w-28 flex-none whitespace-nowrap text-xs text-ink-soft lg:inline" title={formatDate(suite.created_at)}>{new Date(suite.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</span>
                                    <div className="ml-auto flex gap-1.5">
                                        {runButton(suite)}
                                        {exportButton(suite)}
                                        <Button asChild variant="ghost" size="icon" title="Open suite">
                                            <Link to={`/suites/${suite.id}`} aria-label={`Open ${suite.name}`}><ChevronRight className="h-4 w-4" /></Link>
                                        </Button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </>
            )}
        </div>
    );
}
