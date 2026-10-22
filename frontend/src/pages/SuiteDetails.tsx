import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { CaseSplit } from '@/components/redesign/CaseSplit';
import { describeStep } from '@/components/redesign/steps';
import { useState, useEffect, useMemo, useRef } from 'react';
import { api, triggerRun, exportTestSuite, importTestSuite, getProjects, getAuditLog, getAppBuilds } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/redesign/primitives';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { ScheduleModal } from "@/components/ScheduleModal";
import { GenerateCaseDialog } from "@/components/GenerateCaseDialog";
import {
    Play, Plus, FolderOpen, FileText, Settings as SettingsIcon, Trash2, Edit, Download, Upload, CalendarClock, ChevronRight, Loader2, ArrowLeft, Search, LayoutGrid, List, AlertCircle, Zap, LayoutTemplateIcon, Globe, FolderTree, History, Sparkles, Smartphone, MoreHorizontal, ChevronDown
} from 'lucide-react';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from 'sonner';
import { usePermission } from "@/hooks/usePermission";
import { motion, AnimatePresence, Variants } from 'framer-motion';
import { RetryPolicyCard } from '@/components/suite-settings/RetryPolicyCard';
import { ConsoleErrorPolicyCard } from '@/components/evidence/ConsoleErrorPolicyCard';
import { SuiteHooksCard } from '@/components/suite-hooks/SuiteHooksCard';
import { ReviewIntervalCard } from '@/components/ownership/ReviewIntervalCard';
import { getTestReview, type CaseOwnership } from '@/api/caseOwnership';
import { personLabel, reviewSummary } from '@/components/ownership/ownershipHelpers';
import { ProtectedSuiteCard } from '@/components/suite-settings/ProtectedSuiteCard';
import { FrontendMatrixSettings, RunMatrixPicker } from '@/components/frontend-quality/MatrixFields';
import { EMPTY_MATRIX, MatrixChoice, matrixRunContext } from '@/components/frontend-quality/matrixHelpers';
import { manualTestingApi } from '@/api/manualTesting';

const tabVariants: Variants = {
    hidden: { opacity: 0, y: -5 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.2 } },
    exit: { opacity: 0, y: 5, transition: { duration: 0.1 } }
};

interface FlakinessEntry {
    test_case_id: number;
    name: string;
    flake_score: number;
    is_quarantined: boolean;
    // Older backends omit it; then fall back to the bare flag.
    quarantine_state?: 'none' | 'active' | 'expired';
    sample_count: number;
    recent_failures: number;
}

function FlakinessBadge({ flake }: { flake: FlakinessEntry }) {
    const scorePct = (flake.flake_score * 100).toFixed(0);
    const state = flake.quarantine_state ?? (flake.is_quarantined ? 'active' : 'none');
    const status = state === 'active' ? ' — QUARANTINED (skipped at dispatch)'
        : state === 'expired' ? ' — quarantine EXPIRED (running again; release or re-quarantine)'
        : ' — not quarantined';
    return (
        <span
            title={`Flake score: ${scorePct}% over ${flake.sample_count} recent runs${status}`}
            className={`inline-flex shrink-0 cursor-help items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${state === 'active'
                ? 'bg-pastel-peach text-pastel-ink-peach'
                : 'bg-pastel-butter text-pastel-ink-butter'}`}
        >
            {state === 'active' ? 'Quarantined' : state === 'expired' ? 'Quarantine expired' : `Flaky ${scorePct}%`}
        </span>
    );
}

const EXEC_TONE: Record<string, string> = {
    continuous: 'bg-pastel-lavender text-pastel-ink-lavender',
    separate: 'bg-pastel-pink text-pastel-ink-pink',
    parallel: 'bg-pastel-mint text-pastel-ink-mint',
};

function ExecModeBadge({ mode }: { mode: string }) {
    return (
        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${EXEC_TONE[mode] ?? 'bg-secondary text-ink-soft'}`}>
            {mode}
        </span>
    );
}

export default function SuiteDetails() {
    const { suiteId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState('tests');
    const [searchTerm, setSearchTerm] = useState('');
    const [viewMode, setViewMode] = useState<'card' | 'list'>('list');
    const importSuiteRef = useRef<HTMLInputElement>(null);
    const importCaseRef = useRef<HTMLInputElement>(null);

    const [activeProjectId, setActiveProjectId] = useState<number | null>(() => {
        const saved = localStorage.getItem('activeProjectId');
        return saved ? parseInt(saved) : null;
    });

    useEffect(() => {
        const handleProjectChange = () => {
            const saved = localStorage.getItem('activeProjectId');
            setActiveProjectId(saved ? parseInt(saved) : null);
        };
        window.addEventListener('projectChanged', handleProjectChange);
        return () => window.removeEventListener('projectChanged', handleProjectChange);
    }, []);

    const { data: projects } = useQuery({
        queryKey: ['projects'],
        queryFn: () => getProjects()
    });

    const activeProject = projects?.find(p => p.id === activeProjectId);
    const { can } = usePermission();

    // Dialog state
    const [showSubModuleDialog, setShowSubModuleDialog] = useState(false);
    const [showGenerateDialog, setShowGenerateDialog] = useState(false);
    // Feature flag from Settings → General → AI Test Generation (off by default)
    const aiGenerationEnabled = localStorage.getItem('traceiq.ui.aiGeneration') === 'on';
    const [newModuleName, setNewModuleName] = useState('');
    const [newModuleDesc, setNewModuleDesc] = useState('');

    const [showDeleteSuiteDialog, setShowDeleteSuiteDialog] = useState(false);
    const [suiteToDelete, setSuiteToDelete] = useState<{ id: number, name: string } | null>(null);

    const [showDeleteTestCaseDialog, setShowDeleteTestCaseDialog] = useState(false);
    const [testCaseToDelete, setTestCaseToDelete] = useState<{ id: number, name: string } | null>(null);

    const [showRenameDialog, setShowRenameDialog] = useState(false);
    const [renameName, setRenameName] = useState('');
    const [renameDesc, setRenameDesc] = useState('');

    const [headerKey, setHeaderKey] = useState('');
    const [headerVal, setHeaderVal] = useState('');
    const [paramKey, setParamKey] = useState('');
    const [paramVal, setParamVal] = useState('');

    // Schedule Modal state
    const [isScheduleModalOpen, setIsScheduleModalOpen] = useState(false);
    const [scheduleTarget, setScheduleTarget] = useState<{ suiteId: number, caseId?: number, name: string } | null>(null);


    const { data: suite, isLoading, isError } = useQuery({
        queryKey: ['suite', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then(res => res.data),
        enabled: !!suiteId,
        retry: 1
    });

    const { data: auditLogs, isLoading: isAuditLogLoading } = useQuery({
        queryKey: ['audit', suiteId],
        queryFn: () => getAuditLog('suite', Number(suiteId)),
        enabled: !!suiteId && activeTab === 'audit'
    });

    // Flakiness (fetched once per project); mapped by test_case_id below.
    const flakinessProjectId = suite?.project_id ?? activeProjectId;
    const { data: flakinessData } = useQuery<FlakinessEntry[]>({
        queryKey: ['flakiness', flakinessProjectId],
        queryFn: () => api.get(`/analytics/projects/${flakinessProjectId}/flakiness`).then(res => res.data),
        enabled: !!flakinessProjectId,
        staleTime: 60000,
    });

    // Owner + review status per case (one project-wide query).
    const { data: reviewData } = useQuery({
        queryKey: ['test-review', flakinessProjectId, 'all', undefined],
        queryFn: () => getTestReview(flakinessProjectId!, 'all'),
        enabled: !!flakinessProjectId,
        staleTime: 60000,
    });
    const ownershipByCaseId = useMemo(() => {
        const map = new Map<number, CaseOwnership>();
        (reviewData?.items || []).forEach((c) => map.set(c.case_id, c));
        return map;
    }, [reviewData]);

    const flakeByCaseId = useMemo(() => {
        const map = new Map<number, FlakinessEntry>();
        (flakinessData || []).forEach((f) => {
            if (f.flake_score >= 0.15) map.set(f.test_case_id, f);
        });
        return map;
    }, [flakinessData]);

    const createSubModule = useMutation({
        mutationFn: (data: { name: string; description?: string; parent_id: number; project_id: number }) =>
            api.post('/suites', data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            queryClient.invalidateQueries({ queryKey: ['suites'] }); // update tree
            setNewModuleName('');
            setNewModuleDesc('');
            setShowSubModuleDialog(false);
            toast.success('Sub-module created successfully', {
                description: 'You can now add test cases or further sub-modules to it.'
            });
        },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || 'Failed to create sub-module');
        }
    });

    // Mobile app builds (Phase MOB): when the project has uploaded binaries,
    // a picker appears next to Run Now; the chosen build is pinned onto every
    // run triggered from this page (required for mobile_appium cases).
    const { data: appBuilds } = useQuery({
        queryKey: ['app-builds', suite?.project_id],
        queryFn: () => getAppBuilds(suite!.project_id),
        enabled: !!suite?.project_id,
        staleTime: 60000,
    });
    const [selectedAppBuildId, setSelectedAppBuildId] = useState<string>('none');

    // Environment picker: 'default' lets the dispatcher fall back to the
    // project's default ProjectEnvironment (or none configured).
    const { data: environments } = useQuery<{ id: number; name: string; is_default: boolean }[]>({
        queryKey: ['environments', suite?.project_id],
        queryFn: () => api.get(`/projects/${suite!.project_id}/environments`).then(res => res.data),
        enabled: !!suite?.project_id,
        staleTime: 60000,
    });
    const [selectedEnvId, setSelectedEnvId] = useState<string>('default');

    // Frontend matrix for runs started here (WP10); empty = the suite's settings.
    const [runMatrix, setRunMatrix] = useState<MatrixChoice>(EMPTY_MATRIX);
    const matrixContext = matrixRunContext(runMatrix);
    const runContext = (selectedAppBuildId !== 'none' || selectedEnvId !== 'default' || Object.keys(matrixContext).length)
        ? {
            ...(selectedAppBuildId !== 'none' ? { app_build_id: Number(selectedAppBuildId) } : {}),
            ...(selectedEnvId !== 'default' ? { environment_id: Number(selectedEnvId) } : {}),
            ...matrixContext,
        }
        : undefined;

    const runMutation = useMutation({
        mutationFn: (id: number) => triggerRun(id, undefined, undefined, undefined, runContext),
        onSuccess: () => { navigate('/runs'); },
        onError: (error: any) => { toast.error(error.response?.data?.detail || "Failed to start run"); }
    });

    const deleteSuiteMutation = useMutation({
        mutationFn: (id: number) => api.delete(`/suites/${id}`),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            if (suite?.parent_id) {
                navigate(`/suites/${suite.parent_id}`);
            } else {
                navigate('/suites');
            }
            toast.success('Module deleted successfully');
            setShowDeleteSuiteDialog(false);
            setSuiteToDelete(null);
        },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || "Failed to delete module");
        }
    });

    const deleteTestCaseMutation = useMutation({
        mutationFn: (id: number) => api.delete(`/cases/${id}`),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success('Test case deleted successfully');
            setShowDeleteTestCaseDialog(false);
            setTestCaseToDelete(null);
        },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || "Failed to delete test case");
        }
    });

    const renameSuite = useMutation({
        mutationFn: (data: { name: string; description?: string }) => api.patch(`/suites/${suiteId}`, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            setShowRenameDialog(false);
            toast.success('Module updated successfully');
        },
        onError: (error: any) => { toast.error('Failed to update module', { description: error.response?.data?.detail }); }
    });

    const updateSettings = useMutation({
        mutationFn: (data: any) => api.put(`/suites/${suiteId}`, data),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success(variables.successMessage || 'Settings updated successfully');
        },
        onError: (error: any) => { toast.error('Failed to update settings', { description: error.response?.data?.detail }); }
    });

    const runTestCaseMutation = useMutation({
        mutationFn: (caseId: number) => triggerRun(Number(suiteId), caseId, undefined, undefined, runContext),
        onSuccess: () => { navigate('/runs'); },
        onError: (error: any) => { toast.error(error.response?.data?.detail || "Failed to start run for this test case"); }
    });

    const handleCreateSubModule = () => {
        if (!newModuleName.trim() || !activeProjectId) return;
        createSubModule.mutate({
            name: newModuleName,
            description: newModuleDesc || undefined,
            parent_id: Number(suiteId),
            project_id: activeProjectId
        });
    };

    const handleExportSuite = async () => {
        try {
            const data = await exportTestSuite(Number(suiteId));
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${suite?.name?.replace(/\s+/g, '_')}_module.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Module exported successfully');
        } catch { toast.error('Failed to export module'); }
    };

    const handleExportCase = async (caseId: number, caseName: string) => {
        try {
            const res = await api.get(`/cases/${caseId}/export`);
            const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${caseName.replace(/\s+/g, '_')}_case.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Test Case exported successfully');
        } catch { toast.error('Failed to export test case'); }
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
            await importTestSuite(Number(suiteId), data, activeProjectId ?? undefined);
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success('Sub-module imported successfully');
        } catch (error: any) {
            const responseData = error?.response?.data;
            const detail = responseData?.detail ?? responseData ?? error?.message;
            toast.error('Import failed', { description: typeof detail === 'string' ? detail : 'Unknown error', duration: 8000 });
        }
    };

    const handleImportCase = async (event: React.ChangeEvent<HTMLInputElement>) => {
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
            await api.post(`/suites/${suiteId}/import_case`, data);
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success('Test Case imported successfully');
        } catch (error: any) {
            const responseData = error?.response?.data;
            const detail = responseData?.detail ?? responseData ?? error?.message;
            toast.error('Import failed', { description: typeof detail === 'string' ? detail : 'Unknown error', duration: 8000 });
        }
    };

    const handleRenameSuite = () => {
        if (!renameName.trim()) return;
        renameSuite.mutate({ name: renameName, description: renameDesc || undefined });
    };

    const openRenameDialog = () => {
        setRenameName(suite?.name || '');
        setRenameDesc(suite?.description || '');
        setShowRenameDialog(true);
    };

    const handleUpdateSettings = (newSettings: any, inherit: boolean, msg?: string) => {
        updateSettings.mutate({
            settings: newSettings,
            inherit_settings: inherit,
            successMessage: msg
        });
    };

    // Manual cases are run by a person, not dispatched: "Run" on one starts a
    // manual run of just that case and opens the tester screen.
    const startManualRun = useMutation({
        mutationFn: (caseIds?: number[]) => manualTestingApi.start({ suite_id: Number(suiteId), case_ids: caseIds }),
        onSuccess: (run) => { navigate(`/manual-runs/${run.id}`); },
        onError: (error: any) => { toast.error(error.response?.data?.detail || 'Could not start a manual run'); },
    });
    const isManual = (tc: any) => tc?.executor === 'manual';
    const editPath = (tc: any) => isManual(tc)
        ? `/suites/${suiteId}/manual/${tc.id}/edit` : `/suites/${suiteId}/cases/${tc.id}/edit`;

    const handleRunTestCase = (caseId: number) => {
        const tc = (suite?.test_cases || []).find((c: any) => c.id === caseId);
        if (isManual(tc)) { startManualRun.mutate([caseId]); return; }
        runTestCaseMutation.mutate(caseId);
    };

    if (isLoading) {
        return (
            <div className="flex h-[400px] items-center justify-center">
                <div className="flex flex-col items-center gap-4 text-slate-400">
                    <Loader2 className="h-8 w-8 animate-spin text-indigo-500" />
                    <p className="font-medium animate-pulse">Loading module details...</p>
                </div>
            </div>
        );
    }

    if (isError || !suite) {
        return (
            <div className="flex h-[400px] flex-col items-center justify-center p-8 text-center bg-white rounded-3xl border border-rose-100 shadow-sm mt-8 max-w-2xl mx-auto">
                <div className="w-16 h-16 bg-rose-50 rounded-2xl flex items-center justify-center mb-6">
                    <AlertCircle className="h-8 w-8 text-rose-500" />
                </div>
                <h3 className="text-xl font-bold text-slate-900 mb-2">Module Not Found</h3>
                <p className="text-slate-500 max-w-sm mb-6">The test module you're looking for doesn't exist or you don't have permission to view it.</p>
                <Button onClick={() => navigate('/suites')} className="rounded-xl px-6 h-11 bg-slate-900 text-white hover:bg-slate-800 shadow-sm">
                    <ArrowLeft className="mr-2 h-4 w-4" /> Back to Test Suites
                </Button>
            </div>
        );
    }

    const projectId = suite.project_id;
    const workspaceId = activeProject?.workspace_id;

    // Filter sub-modules
    const filteredSubModules = suite.sub_modules
        ? suite.sub_modules.filter((sub: any) =>
            sub.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
            (sub.description && sub.description.toLowerCase().includes(searchTerm.toLowerCase()))
        )
        : [];

    // Filter test cases
    const filteredTestCases = suite.test_cases
        ? suite.test_cases.filter((tc: any) => tc.name.toLowerCase().includes(searchTerm.toLowerCase()))
        : [];

    const tabs: { id: typeof activeTab; label: string; count?: number }[] = [
        { id: 'tests', label: 'Tests', count: (suite.total_sub_modules || 0) > 0 ? suite.total_sub_modules : suite.total_test_cases || 0 },
        { id: 'settings', label: 'Settings' },
        { id: 'audit', label: 'Audit log' },
    ];
    const showCases = (suite.total_sub_modules || 0) === 0;
    const canManage = can("project:manage", { projectId, workspaceId });
    const canCreateTest = can("test:create", { projectId, workspaceId });
    const canExecute = can("project:execute_test", { projectId, workspaceId });
    // POST/PUT/DELETE /schedules check project:create_suite, which only
    // project and workspace admins hold — editors would just get a 403.
    const canSchedule = can("project:create_suite", { projectId, workspaceId });

    // Row/card actions for a sub-module (schedule, run, delete, open).
    const SubModuleActions = ({ sub }: { sub: { id: number; name: string } }) => (
        <div className="flex flex-none items-center gap-1">
            {canSchedule && (
                <button type="button" aria-label={`Schedule ${sub.name}`} title="Schedule" onClick={() => { setScheduleTarget({ suiteId: Number(sub.id), name: sub.name }); setIsScheduleModalOpen(true); }}
                    className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-ink"><CalendarClock className="h-4 w-4" /></button>
            )}
            {canManage && (
                <button type="button" aria-label={`Delete ${sub.name}`} title="Delete" onClick={() => { setSuiteToDelete({ id: sub.id, name: sub.name }); setShowDeleteSuiteDialog(true); }}
                    className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-pastel-ink-peach"><Trash2 className="h-4 w-4" /></button>
            )}
            {canExecute && <Button size="sm" onClick={() => runMutation.mutate(sub.id)} disabled={runMutation.isPending}><Play className="h-3 w-3" fill="currentColor" /> Run</Button>}
            <Link to={`/suites/${sub.id}`} aria-label={`Open ${sub.name}`} className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-ink"><ChevronRight className="h-4 w-4" /></Link>
        </div>
    );


    return (
        <div className="flex flex-col gap-5 pb-16">
            <input ref={importSuiteRef} type="file" accept=".json" onChange={handleImportSuite} className="hidden" aria-hidden="true" tabIndex={-1} />
            <input ref={importCaseRef} type="file" accept=".json" onChange={handleImportCase} className="hidden" aria-hidden="true" tabIndex={-1} />

            <div className="rounded-3xl border border-border bg-white">
                <div className="px-5 pb-4 pt-5 sm:px-6">
                <PageHeader
                    crumbs={<>
                        <Link to="/suites" className="hover:text-ink">Tests</Link>
                        <span>/</span>
                        <span>{activeProject?.name}</span>
                        {suite.parent_id && (<>
                            <span>/</span>
                            <Link to={`/suites/${suite.parent_id}`} className="max-w-[180px] truncate hover:text-ink">{suite.parent?.name || 'Parent module'}</Link>
                        </>)}
                    </>}
                    title={<span className="inline-flex items-center gap-2">
                        {suite.name}
                        {canManage && (
                            <button type="button" onClick={openRenameDialog} aria-label="Rename this suite" title="Rename"
                                className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-ink">
                                <Edit className="h-4 w-4" />
                            </button>
                        )}
                    </span>}
                    badge={<ExecModeBadge mode={suite.execution_mode} />}
                    subtitle={suite.description || `${suite.total_test_cases || 0} tests · ${suite.total_sub_modules || 0} sub-modules`}
                    actions={
                        <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
                            {canExecute && <RunMatrixPicker value={runMatrix} onChange={setRunMatrix} />}
                            {canExecute && (environments?.length ?? 0) > 0 && (
                                <Select value={selectedEnvId} onValueChange={setSelectedEnvId}>
                                    <SelectTrigger className="h-10 w-auto max-w-[240px] gap-2 rounded-full border border-input bg-white px-3.5 text-sm text-ink shadow-none hover:bg-background focus:ring-0 focus-visible:ring-2 focus-visible:ring-ink/20 [&>span]:truncate" aria-label="Environment for runs from this page" title="Environment for runs from this page">
                                        <Globe className="h-4 w-4 shrink-0" /><SelectValue placeholder="Environment" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="default">Default environment</SelectItem>
                                        {(environments || []).map((env) => (
                                            <SelectItem key={env.id} value={env.id.toString()}>
                                                {env.name}{env.is_default ? ' (default)' : ''}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            )}
                            {canExecute && (appBuilds?.length ?? 0) > 0 && (
                                <Select value={selectedAppBuildId} onValueChange={setSelectedAppBuildId}>
                                    <SelectTrigger className="h-10 w-auto max-w-[240px] gap-2 rounded-full border border-input bg-white px-3.5 text-sm text-ink shadow-none hover:bg-background focus:ring-0 focus-visible:ring-2 focus-visible:ring-ink/20 [&>span]:truncate" aria-label="App build for runs from this page" title="Pin a mobile app build to runs from this page">
                                        <Smartphone className="h-4 w-4 shrink-0" /><SelectValue placeholder="App build" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="none">No app build (web)</SelectItem>
                                        {(appBuilds || []).map((b) => (
                                            <SelectItem key={b.id} value={b.id.toString()}>
                                                {b.app_name}{b.version_name ? ` ${b.version_name}` : ''} ({b.platform})
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            )}
                            {canExecute && (
                                <Button onClick={() => runMutation.mutate(Number(suiteId))} disabled={runMutation.isPending}>
                                    {runMutation.isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Starting…</> : <><Play className="h-4 w-4" fill="currentColor" /> Run suite</>}
                                </Button>
                            )}
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="outline" aria-label="More suite actions"><MoreHorizontal className="h-4 w-4" /> More</Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" className="w-56">
                                    {canSchedule && (
                                        <DropdownMenuItem onClick={() => { setScheduleTarget({ suiteId: Number(suiteId), name: suite.name }); setIsScheduleModalOpen(true); }}><CalendarClock className="mr-2 h-4 w-4" /> Schedule this suite</DropdownMenuItem>
                                    )}
                                    <DropdownMenuItem onClick={handleExportSuite}><Download className="mr-2 h-4 w-4" /> Export suite (JSON)</DropdownMenuItem>
                                    {canManage && (
                                        <DropdownMenuItem onClick={() => importSuiteRef.current?.click()}><Upload className="mr-2 h-4 w-4" /> Import into this suite</DropdownMenuItem>
                                    )}
                                </DropdownMenuContent>
                            </DropdownMenu>
                        </div>
                    }
                />
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-[#F0EEE8] px-3 py-2 sm:px-4">
                <div role="tablist" aria-label="Suite sections" className="-mb-2 flex gap-1 self-stretch">
                    {tabs.map((t) => (
                        <button key={t.id} type="button" role="tab" aria-selected={activeTab === t.id} onClick={() => setActiveTab(t.id)}
                            className={`flex min-h-12 items-center gap-1.5 px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ink/20 ${activeTab === t.id ? 'font-semibold text-ink shadow-[inset_0_-2px_0_#1C1B22]' : 'font-medium text-ink-soft hover:text-ink'}`}>
                            {t.label}{t.count !== undefined && <span className="font-mono text-xs text-ink-soft">{t.count}</span>}
                        </button>
                    ))}
                </div>
                {activeTab === 'tests' && (
                    <div className="ml-auto flex flex-wrap items-center gap-2">
                        <label className="flex h-10 w-full items-center gap-2 rounded-full border border-input bg-white px-3.5 sm:w-56">
                            <Search className="h-4 w-4 flex-none text-ink-soft" />
                            <span className="sr-only">Search this suite</span>
                            <input type="search" placeholder={showCases ? 'Search tests' : 'Search sub-modules'} value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)} className="min-w-0 flex-1 border-0 bg-transparent text-sm outline-none" />
                        </label>
                        <div role="group" aria-label="View" className="flex gap-0.5 rounded-full bg-[#EFEDE7] p-1">
                            {([['list', 'List view', List], ['card', 'Card view', LayoutGrid]] as const).map(([id, label, Icon]) => (
                                <button key={id} type="button" aria-pressed={viewMode === id} aria-label={label} title={label} onClick={() => setViewMode(id)}
                                    className={`flex min-h-9 items-center gap-1.5 rounded-full text-[13px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-ink/20 w-9 justify-center ${viewMode === id ? 'bg-white text-ink shadow-[0_1px_4px_rgba(28,27,34,0.1)]' : 'text-ink-soft'}`}>
                                    <Icon className="h-4 w-4" />
                                </button>
                            ))}
                        </div>
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button variant="outline" aria-label="Add or import"><Plus className="h-4 w-4" /> Add <ChevronDown className="h-3.5 w-3.5" /></Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-56">
                                <DropdownMenuItem onClick={() => setShowSubModuleDialog(true)}><FolderOpen className="mr-2 h-4 w-4" /> New sub-module</DropdownMenuItem>
                                {showCases && canCreateTest && (
                                    <DropdownMenuItem onClick={() => importCaseRef.current?.click()}><Upload className="mr-2 h-4 w-4" /> Import a test (JSON)</DropdownMenuItem>
                                )}
                                {showCases && canCreateTest && (
                                    <DropdownMenuItem onClick={() => navigate(`/suites/${suiteId}/manual/new`)}><FileText className="mr-2 h-4 w-4" /> New manual test</DropdownMenuItem>
                                )}
                                {canCreateTest && (
                                    <DropdownMenuItem onClick={() => startManualRun.mutate(undefined)}><Play className="mr-2 h-4 w-4" /> Start manual run</DropdownMenuItem>
                                )}
                                {showCases && canCreateTest && aiGenerationEnabled && (
                                    <DropdownMenuItem onClick={() => setShowGenerateDialog(true)}><Sparkles className="mr-2 h-4 w-4" /> Generate tests with AI</DropdownMenuItem>
                                )}
                            </DropdownMenuContent>
                        </DropdownMenu>
                        {showCases && canCreateTest && (
                            <Button onClick={() => navigate(`/suites/${suiteId}/builder`)}><Plus className="h-4 w-4" /> New test</Button>
                        )}
                    </div>
                )}
                </div>
            </div>

                    <AnimatePresence mode="wait">
                        {activeTab === 'tests' ? (
                            <motion.div
                                key="tests"
                                variants={tabVariants}
                                initial="hidden"
                                animate="visible"
                                exit="exit"
                                className="flex flex-col gap-6"
                            >
                                {/* ── Sub-modules ── */}
                                {suite.sub_modules && suite.sub_modules.length > 0 && (
                                    <section aria-label="Sub-modules" className="flex flex-col gap-2">
                                        <h2 className="px-1 text-xs font-semibold uppercase tracking-[0.06em] text-ink-soft">Sub-modules · {filteredSubModules.length}</h2>
                                        {filteredSubModules.length === 0 ? (
                                            <p className="rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-ink-soft">No sub-modules match “{searchTerm}”.</p>
                                        ) : viewMode === 'card' ? (
                                            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                                                {filteredSubModules.map((sub: any) => (
                                                    <div key={sub.id} className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-5">
                                                        <div className="flex items-start gap-3">
                                                            <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[11px] bg-pastel-lavender text-pastel-ink-lavender"><FolderOpen className="h-4 w-4" /></span>
                                                            <div className="min-w-0 flex-1">
                                                                <Link to={`/suites/${sub.id}`} className="block truncate text-base font-semibold text-ink hover:underline">{sub.name}</Link>
                                                                <p className="mt-0.5 line-clamp-2 text-sm text-ink-soft">{sub.description || `${sub.total_test_cases || 0} tests · ${sub.total_sub_modules || 0} sub-modules`}</p>
                                                            </div>
                                                            <ExecModeBadge mode={sub.execution_mode} />
                                                        </div>
                                                        <div className="mt-auto flex items-center gap-2 border-t border-[#F0EEE8] pt-3">
                                                            <span className="flex-1 text-xs text-ink-soft">{sub.total_test_cases || 0} tests · {sub.total_sub_modules || 0} sub-modules</span>
                                                            <SubModuleActions sub={sub} />
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        ) : (
                                            <ul className="overflow-hidden rounded-3xl border border-border bg-white">
                                                {filteredSubModules.map((sub: any) => (
                                                    <li key={sub.id} className="flex items-center gap-3 border-t border-[#F0EEE8] px-4 py-3 first:border-t-0">
                                                        <span className="flex h-8 w-8 flex-none items-center justify-center rounded-[10px] bg-pastel-lavender text-pastel-ink-lavender"><FolderOpen className="h-4 w-4" /></span>
                                                        <Link to={`/suites/${sub.id}`} className="min-w-0 flex-1">
                                                            <span className="block truncate text-sm font-semibold text-ink hover:underline">{sub.name}</span>
                                                            <span className="block truncate text-xs text-ink-soft">{sub.total_test_cases || 0} tests · {sub.total_sub_modules || 0} sub-modules{sub.description ? ` · ${sub.description}` : ''}</span>
                                                        </Link>
                                                        <ExecModeBadge mode={sub.execution_mode} />
                                                        <SubModuleActions sub={sub} />
                                                    </li>
                                                ))}
                                            </ul>
                                        )}
                                    </section>
                                )}

                                {/* ── Test cases ── */}
                                {showCases && (
                                    !suite.test_cases || suite.test_cases.length === 0 ? (
                                        <div className="flex flex-col items-center gap-3 rounded-3xl border border-border bg-white px-6 py-14 text-center">
                                            <p className="text-lg font-semibold text-ink">No tests in this suite yet</p>
                                            <p className="max-w-[46ch] text-sm text-ink-soft">Build one step by step, import a JSON export, or split this suite into sub-modules.</p>
                                            {canCreateTest && (
                                                <Button size="lg" onClick={() => navigate(`/suites/${suiteId}/builder`)}><Plus className="h-4 w-4" /> New test</Button>
                                            )}
                                        </div>
                                    ) : filteredTestCases.length === 0 ? (
                                        <p className="rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-ink-soft">No tests match “{searchTerm}”.</p>
                                    ) : viewMode === 'card' ? (
                                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                                            {filteredTestCases.map((tc: any) => (
                                                <div key={tc.id} className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-5">
                                                    <div className="flex items-start gap-3">
                                                        <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[11px] bg-secondary text-ink-soft"><FileText className="h-4 w-4" /></span>
                                                        <div className="min-w-0 flex-1">
                                                            <h3 className="text-base font-semibold leading-snug text-ink">{tc.name}</h3>
                                                            <p className="mt-0.5 text-xs text-ink-soft">{tc.steps?.length || 0} steps{isManual(tc) ? ' · manual' : ''}</p>
                                                            {ownershipByCaseId.has(tc.id) && (
                                                                <p className="mt-0.5 text-xs text-ink-soft">
                                                                    Owner: {personLabel(ownershipByCaseId.get(tc.id)!.owner)} · {reviewSummary(ownershipByCaseId.get(tc.id)!)}
                                                                </p>
                                                            )}
                                                        </div>
                                                        {flakeByCaseId.has(tc.id) && <FlakinessBadge flake={flakeByCaseId.get(tc.id)!} />}
                                                    </div>
                                                    {tc.steps && tc.steps.length > 0 && (
                                                        <ol className="flex flex-col gap-1.5 text-[13px] text-ink-soft">
                                                            {tc.steps.slice(0, 3).map((step: any, idx: number) => (
                                                                <li key={idx} className="flex gap-2"><span className="w-4 font-mono text-xs">{idx + 1}</span>
                                                                    <span className="line-clamp-1 text-ink">{describeStep(step).text}</span>
                                                                </li>
                                                            ))}
                                                            {tc.steps.length > 3 && <li className="pl-6 text-xs">+{tc.steps.length - 3} more</li>}
                                                        </ol>
                                                    )}
                                                    <div className="mt-auto flex items-center gap-1.5 border-t border-[#F0EEE8] pt-3">
                                                        {canSchedule && <button type="button" aria-label={`Schedule ${tc.name}`} title="Schedule" onClick={() => { setScheduleTarget({ suiteId: Number(suiteId), caseId: tc.id, name: tc.name }); setIsScheduleModalOpen(true); }} className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-ink"><CalendarClock className="h-4 w-4" /></button>}
                                                        {canCreateTest && <button type="button" aria-label={`Edit ${tc.name}`} title="Edit" onClick={() => navigate(editPath(tc))} className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-ink"><Edit className="h-4 w-4" /></button>}
                                                        <button type="button" aria-label={`Export ${tc.name}`} title="Export" onClick={() => handleExportCase(tc.id, tc.name)} className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-ink"><Download className="h-4 w-4" /></button>
                                                        {canCreateTest && <button type="button" aria-label={`Delete ${tc.name}`} title="Delete" onClick={() => { setTestCaseToDelete({ id: tc.id, name: tc.name }); setShowDeleteTestCaseDialog(true); }} className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:bg-secondary hover:text-pastel-ink-peach"><Trash2 className="h-4 w-4" /></button>}
                                                        {canExecute && <Button size="sm" className="ml-auto" onClick={() => handleRunTestCase(tc.id)}><Play className="h-3 w-3" fill="currentColor" /> Run</Button>}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <CaseSplit
                                            cases={filteredTestCases}
                                            badge={(tc) => flakeByCaseId.has(tc.id) ? <FlakinessBadge flake={flakeByCaseId.get(tc.id)!} /> : null}
                                            canEdit={canCreateTest}
                                            onRun={canExecute ? (tc) => handleRunTestCase(tc.id) : undefined}
                                            onSchedule={canSchedule ? (tc) => { setScheduleTarget({ suiteId: Number(suiteId), caseId: tc.id, name: tc.name }); setIsScheduleModalOpen(true); } : undefined}
                                            onEdit={(tc) => navigate(editPath(tc))}
                                            onExport={(tc) => handleExportCase(tc.id, tc.name)}
                                            onDelete={(tc) => { setTestCaseToDelete({ id: tc.id, name: tc.name }); setShowDeleteTestCaseDialog(true); }}
                                        />
                                    )
                                )}
                            </motion.div>
                        ) : activeTab === 'settings' ? (
                            <motion.div
                                key="settings"
                                variants={tabVariants}
                                initial="hidden"
                                animate="visible"
                                exit="exit"
                                className="max-w-4xl space-y-8 pb-12"
                            >
                                {/* ── Settings Header ── */}
                                <div className="flex items-center gap-3 pb-2 border-b border-slate-100 mb-6">
                                    <div className="p-2 rounded-xl bg-slate-100 text-slate-500">
                                        <SettingsIcon className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <h2 className="text-xl font-extrabold text-slate-900">Module Settings</h2>
                                        <p className="text-sm text-slate-500">Configure inheritance, execution modes, headers, and parameters.</p>
                                    </div>
                                </div>

                                {/* ── Protection (WP16c) ── */}
                                <ProtectedSuiteCard suiteId={Number(suiteId)} protectedSuite={!!suite.protected} />

                                {/* ── Inheritance Setting ── */}
                                {suite.parent_id && (
                                    <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 lg:p-8 flex flex-col md:flex-row md:items-center justify-between gap-6 hover:border-slate-300 transition-colors">
                                        <div className="space-y-1">
                                            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                                                <FolderTree className="h-4 w-4 text-indigo-500" />
                                                Inherit Settings from Parent
                                            </h3>
                                            <p className="text-sm text-slate-500 max-w-xl">
                                                Automatically use headers and parameters defined in parent modules. This simplifies configuration across nested modules.
                                            </p>
                                        </div>
                                        <Button
                                            variant={suite.inherit_settings ? "default" : "outline"}
                                            onClick={() => handleUpdateSettings(suite.settings, !suite.inherit_settings, "Inheritance settings updated")}
                                            className={`shrink-0 h-11 px-6 rounded-xl font-bold tracking-wide transition-all ${suite.inherit_settings ? 'bg-indigo-600 hover:bg-indigo-700 shadow-md shadow-indigo-600/20' : 'border-slate-200 text-slate-600 hover:bg-slate-50 group'}`}
                                        >
                                            {suite.inherit_settings ? (
                                                <><FolderTree className="mr-2 h-4 w-4" /> Inheritance On</>
                                            ) : (
                                                <><FolderTree className="mr-2 h-4 w-4 text-slate-400 group-hover:text-slate-600" /> Inheritance Off</>
                                            )}
                                        </Button>
                                    </div>
                                )}

                                {/* ── Execution Mode Setting ── */}
                                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
                                    <div className="p-6 lg:p-8 grid md:grid-cols-3 gap-6 items-start">
                                        <div className="md:col-span-2 space-y-1">
                                            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                                                <Zap className="h-4 w-4 text-indigo-500" />
                                                Execution Mode
                                            </h3>
                                            <p className="text-sm text-slate-500">
                                                {suite.execution_mode === 'continuous'
                                                    ? "The default mode. Each test is currently dispatched as its own job, from a clean browser, like Separate; a shared browser session is not implemented yet."
                                                    : suite.execution_mode === 'parallel'
                                                        ? "Tests run in parallel across multiple worker nodes. Fastest execution, optimized for resource usage."
                                                        : "Each test runs in a completely separate, clean browser session. Medium speed, highly isolated."}
                                            </p>
                                            {suite.sub_modules && suite.sub_modules.length > 0 && (
                                                <div className="mt-4 inline-flex items-center gap-2 bg-amber-50 text-amber-700 text-sm py-2 px-3 rounded-xl border border-amber-200/50">
                                                    <AlertCircle className="h-4 w-4 text-amber-500 shrink-0" />
                                                    <span className="font-medium">Locked to Separate mode because this module contains sub-modules.</span>
                                                </div>
                                            )}
                                        </div>
                                        <div className="md:col-span-1 md:justify-self-end w-full sm:w-auto">
                                            <Select
                                                value={suite.execution_mode}
                                                disabled={suite.sub_modules && suite.sub_modules.length > 0}
                                                onValueChange={(value) => updateSettings.mutate({
                                                    execution_mode: value,
                                                    successMessage: `Execution mode updated to ${value}`
                                                })}
                                            >
                                                <SelectTrigger className="w-full sm:w-[200px] h-11 rounded-xl border-slate-200 font-medium text-slate-700 bg-slate-50/50 focus:ring-indigo-500/20 data-[state=open]:border-indigo-500">
                                                    <SelectValue placeholder="Select mode" />
                                                </SelectTrigger>
                                                <SelectContent className="rounded-xl border-slate-200 shadow-xl overflow-hidden">
                                                    <SelectItem value="continuous" className="focus:bg-indigo-50 focus:text-indigo-700 font-medium cursor-pointer py-2.5">Continuous</SelectItem>
                                                    <SelectItem value="parallel" className="focus:bg-indigo-50 focus:text-indigo-700 font-medium cursor-pointer py-2.5">Parallel</SelectItem>
                                                    <SelectItem value="separate" className="focus:bg-indigo-50 focus:text-indigo-700 font-medium cursor-pointer py-2.5">Separate</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                </div>

                                {/* ── HAR Capture Setting ── */}
                                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
                                    <div className="p-6 lg:p-8 grid md:grid-cols-3 gap-6 items-start">
                                        <div className="md:col-span-2 space-y-1">
                                            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                                                <Globe className="h-4 w-4 text-indigo-500" />
                                                Network Archive (HAR)
                                            </h3>
                                            <p className="text-sm text-slate-500">
                                                Record a full HAR of every job in this suite and attach it to run results
                                                as a downloadable artifact. Adds some overhead and artifact size — enable
                                                when debugging network behavior. Inherited by sub-modules like other settings.
                                            </p>
                                        </div>
                                        <div className="md:col-span-1 md:justify-self-end">
                                            <label className="flex items-center gap-2.5 text-sm font-semibold text-slate-700 cursor-pointer select-none bg-slate-50/50 border border-slate-200 rounded-xl px-4 h-11">
                                                <input
                                                    type="checkbox"
                                                    className="rounded accent-indigo-600"
                                                    checked={!!suite.settings?.har_capture}
                                                    onChange={(e) => handleUpdateSettings(
                                                        { ...(suite.settings || {}), har_capture: e.target.checked },
                                                        suite.inherit_settings,
                                                        e.target.checked ? 'HAR capture enabled' : 'HAR capture disabled'
                                                    )}
                                                />
                                                Capture HAR
                                            </label>
                                        </div>
                                    </div>
                                </div>

                                {/* ── Retry failed tests ── */}
                                <RetryPolicyCard
                                    settings={suite.settings}
                                    effective={suite.effective_settings}
                                    inherit={suite.inherit_settings}
                                    executionMode={suite.execution_mode}
                                    onSave={(next, msg) => handleUpdateSettings(next, suite.inherit_settings, msg)}
                                />

                                {/* ── Fail on JavaScript errors ── */}
                                <ConsoleErrorPolicyCard
                                    key={suite.id}
                                    settings={suite.settings}
                                    effective={suite.effective_settings}
                                    inherit={suite.inherit_settings}
                                    onSave={(next, msg) => handleUpdateSettings(next, suite.inherit_settings, msg)}
                                />

                                {/* ── Setup & teardown hooks + data seed ── */}
                                <SuiteHooksCard
                                    key={`hooks-${suite.id}`}
                                    settings={suite.settings}
                                    effectiveSeed={suite.effective_settings?.data_seed}
                                    inherit={suite.inherit_settings}
                                    onSave={(next, msg) => handleUpdateSettings(next, suite.inherit_settings, msg)}
                                />

                                {/* ── Periodic test review (WP16b) ── */}
                                <ReviewIntervalCard
                                    key={`review-${suite.id}`}
                                    settings={suite.settings}
                                    effective={suite.effective_settings}
                                    inherit={suite.inherit_settings}
                                    onSave={(next, msg) => handleUpdateSettings(next, suite.inherit_settings, msg)}
                                />

                                {/* ── Execution Matrix (Browsers & Devices) ── */}
                                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
                                    <div className="p-6 lg:p-8 space-y-5">
                                        <div className="space-y-1">
                                            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                                                <Play className="h-4 w-4 text-indigo-500" />
                                                Execution Matrix
                                            </h3>
                                            <p className="text-sm text-slate-500">
                                                Browsers and devices every run of this module fans out to — one run per
                                                combination. Leave everything unchecked to inherit from the parent module
                                                (or, at the top level, your personal Settings defaults). Checking anything
                                                here replaces the inherited list for this module and its children.
                                                Test cases can override this individually in the builder.
                                            </p>
                                            {suite.inherit_settings && !(suite.settings?.browsers?.length) && (suite.effective_settings?.browsers?.length ?? 0) > 0 && (
                                                <p className="text-xs font-semibold text-indigo-600">
                                                    Inherited browsers: {suite.effective_settings.browsers.join(', ')}
                                                </p>
                                            )}
                                            {suite.inherit_settings && !(suite.settings?.devices?.length) && (suite.effective_settings?.devices?.length ?? 0) > 0 && (
                                                <p className="text-xs font-semibold text-indigo-600">
                                                    Inherited devices: {suite.effective_settings.devices.join(', ')}
                                                </p>
                                            )}
                                        </div>
                                        <div className="grid md:grid-cols-2 gap-6">
                                            <div className="space-y-2.5">
                                                <p className="text-xs font-bold uppercase tracking-wide text-slate-400">Browsers</p>
                                                {['chromium', 'firefox', 'webkit'].map((b) => (
                                                    <label key={b} className="flex items-center gap-2.5 text-sm font-semibold text-slate-700 cursor-pointer select-none bg-slate-50/50 border border-slate-200 rounded-xl px-4 h-11">
                                                        <input
                                                            type="checkbox"
                                                            className="rounded accent-indigo-600"
                                                            checked={(suite.settings?.browsers || []).includes(b)}
                                                            onChange={(e) => {
                                                                const cur: string[] = suite.settings?.browsers || [];
                                                                const next = e.target.checked ? [...cur, b] : cur.filter((x: string) => x !== b);
                                                                handleUpdateSettings(
                                                                    { ...(suite.settings || {}), browsers: next },
                                                                    suite.inherit_settings,
                                                                    next.length ? `Module runs on: ${next.join(', ')}` : 'Browser list cleared — inheriting again'
                                                                );
                                                            }}
                                                        />
                                                        <span className="capitalize">{b}</span>
                                                    </label>
                                                ))}
                                            </div>
                                            <div className="space-y-2.5">
                                                <p className="text-xs font-bold uppercase tracking-wide text-slate-400">Devices</p>
                                                {['Desktop', 'Mobile (Generic)', 'iPhone 13', 'Pixel 5'].map((d) => (
                                                    <label key={d} className="flex items-center gap-2.5 text-sm font-semibold text-slate-700 cursor-pointer select-none bg-slate-50/50 border border-slate-200 rounded-xl px-4 h-11">
                                                        <input
                                                            type="checkbox"
                                                            className="rounded accent-indigo-600"
                                                            checked={(suite.settings?.devices || []).includes(d)}
                                                            onChange={(e) => {
                                                                const cur: string[] = suite.settings?.devices || [];
                                                                const next = e.target.checked ? [...cur, d] : cur.filter((x: string) => x !== d);
                                                                handleUpdateSettings(
                                                                    { ...(suite.settings || {}), devices: next },
                                                                    suite.inherit_settings,
                                                                    next.length ? `Module devices: ${next.join(', ')}` : 'Device list cleared — inheriting again'
                                                                );
                                                            }}
                                                        />
                                                        {d}
                                                    </label>
                                                ))}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* ── Frontend matrix (viewports / locales / themes), WP10 ── */}
                                <FrontendMatrixSettings
                                    settings={suite.settings}
                                    effective={suite.effective_settings}
                                    onSave={(next, msg) => handleUpdateSettings(next, suite.inherit_settings, msg)}
                                />

                                {/* ── Headers & Params Configuration ── */}
                                <div className="grid lg:grid-cols-2 gap-6">
                                    {/* Custom Headers */}
                                    <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden flex flex-col h-full hover:border-slate-300 transition-colors">
                                        <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50">
                                            <h3 className="font-extrabold text-slate-900 flex items-center gap-2">
                                                <LayoutTemplateIcon className="h-4 w-4 text-indigo-500" />
                                                Custom Headers
                                            </h3>
                                        </div>
                                        <div className="p-6 flex-1 flex flex-col space-y-5">
                                            <div className="space-y-2.5 flex-1">
                                                {/* Inherited Headers (Read-only) */}
                                                {suite.inherit_settings && suite.effective_settings?.headers &&
                                                    Object.entries(suite.effective_settings.headers)
                                                        .filter(([key]) => !suite.settings?.headers?.[key]) // Only show if not overridden
                                                        .map(([key, value]: [string, any], idx) => (
                                                            <div key={`inherited-${idx}`} className="flex gap-2 opacity-60">
                                                                <input disabled value={key} className="flex-1 min-w-0 px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm font-mono text-slate-500 cursor-not-allowed shadow-inner" />
                                                                <input disabled value={value} className="flex-1 min-w-0 px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm italic text-slate-500 cursor-not-allowed shadow-inner" />
                                                                <div className="w-10 shrink-0" /> {/* Spacer for trash icon */}
                                                            </div>
                                                        ))}

                                                {/* Custom Headers */}
                                                {Object.entries(suite.settings?.headers || {}).map(([key, value]: [string, any], idx) => (
                                                    <div key={idx} className="flex gap-2 items-center group/item">
                                                        <input disabled value={key} className="flex-1 min-w-0 px-3.5 py-2.5 bg-white border border-slate-200 rounded-xl text-sm font-bold font-mono text-slate-700 shadow-sm" />
                                                        <input disabled value={value} className="flex-1 min-w-0 px-3.5 py-2.5 bg-white border border-slate-200 rounded-xl text-sm text-slate-600 shadow-sm" />
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="w-10 h-10 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 shrink-0 transition-all focus:opacity-100"
                                                            onClick={() => {
                                                                const newHeaders = { ...suite.settings.headers };
                                                                delete newHeaders[key];
                                                                handleUpdateSettings({ ...suite.settings, headers: newHeaders }, suite.inherit_settings, `Header '${key}' removed`);
                                                            }}>
                                                            <Trash2 className="h-4 w-4" />
                                                        </Button>
                                                    </div>
                                                ))}

                                                {(!suite.settings?.headers || Object.keys(suite.settings.headers).length === 0) && (!suite.inherit_settings || !suite.effective_settings?.headers || Object.keys(suite.effective_settings.headers).length === 0) && (
                                                    <div className="text-center py-6 text-sm text-slate-400 font-medium">No custom headers configured.</div>
                                                )}
                                            </div>

                                            <div className="pt-4 border-t border-slate-100 mt-auto">
                                                <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
                                                    <input
                                                        value={headerKey}
                                                        onChange={(e) => setHeaderKey(e.target.value)}
                                                        placeholder="Key (e.g. Authorization)"
                                                        className="flex-1 min-w-0 px-3.5 py-2.5 border border-slate-200 bg-white shadow-sm rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all placeholder:text-slate-400 font-medium"
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter' && headerKey && headerVal) {
                                                                const currentSettings = suite.settings || { headers: {}, params: {} };
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    headers: { ...(currentSettings.headers || {}), [headerKey]: headerVal }
                                                                }, suite.inherit_settings, `Header '${headerKey}' added`);
                                                                setHeaderKey('');
                                                                setHeaderVal('');
                                                            }
                                                        }}
                                                    />
                                                    <input
                                                        value={headerVal}
                                                        onChange={(e) => setHeaderVal(e.target.value)}
                                                        placeholder="Value"
                                                        className="flex-1 min-w-0 px-3.5 py-2.5 border border-slate-200 bg-white shadow-sm rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all placeholder:text-slate-400 font-medium"
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter' && headerKey && headerVal) {
                                                                const currentSettings = suite.settings || { headers: {}, params: {} };
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    headers: { ...(currentSettings.headers || {}), [headerKey]: headerVal }
                                                                }, suite.inherit_settings, `Header '${headerKey}' added`);
                                                                setHeaderKey('');
                                                                setHeaderVal('');
                                                            }
                                                        }}
                                                    />
                                                    <Button
                                                        className="rounded-xl px-5 h-[42px] shadow-sm font-bold tracking-wide shrink-0 bg-slate-900 hover:bg-slate-800 text-white border-transparent w-full sm:w-auto transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                                                        disabled={!headerKey || !headerVal}
                                                        onClick={() => {
                                                            if (headerKey && headerVal) {
                                                                const currentSettings = suite.settings || { headers: {}, params: {} };
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    headers: { ...(currentSettings.headers || {}), [headerKey]: headerVal }
                                                                }, suite.inherit_settings, `Header '${headerKey}' added`);
                                                                setHeaderKey('');
                                                                setHeaderVal('');
                                                            }
                                                        }}
                                                    >Add</Button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Custom Params */}
                                    <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden flex flex-col h-full hover:border-slate-300 transition-colors">
                                        <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50">
                                            <h3 className="font-extrabold text-slate-900 flex items-center gap-2">
                                                <LayoutTemplateIcon className="h-4 w-4 text-emerald-500" />
                                                Query Parameters
                                            </h3>
                                        </div>
                                        <div className="p-6 flex-1 flex flex-col space-y-5">
                                            <div className="space-y-2.5 flex-1">
                                                {/* Inherited Params (Read-only) */}
                                                {suite.inherit_settings && suite.effective_settings?.params &&
                                                    Object.entries(suite.effective_settings.params)
                                                        .filter(([key]) => !suite.settings?.params?.[key]) // Only show if not overridden
                                                        .map(([key, value]: [string, any], idx) => (
                                                            <div key={`inherited-param-${idx}`} className="flex gap-2 opacity-60">
                                                                <input disabled value={key} className="flex-1 min-w-0 px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm font-mono text-slate-500 cursor-not-allowed shadow-inner" />
                                                                <input disabled value={value} className="flex-1 min-w-0 px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm italic text-slate-500 cursor-not-allowed shadow-inner" />
                                                                <div className="w-10 shrink-0" /> {/* Spacer for trash icon */}
                                                            </div>
                                                        ))}

                                                {/* Custom Params */}
                                                {Object.entries(suite.settings?.params || {}).map(([key, value]: [string, any], idx) => (
                                                    <div key={idx} className="flex gap-2 items-center group/item">
                                                        <input disabled value={key} className="flex-1 min-w-0 px-3.5 py-2.5 bg-white border border-slate-200 rounded-xl text-sm font-bold font-mono text-slate-700 shadow-sm" />
                                                        <input disabled value={value} className="flex-1 min-w-0 px-3.5 py-2.5 bg-white border border-slate-200 rounded-xl text-sm text-slate-600 shadow-sm" />
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="w-10 h-10 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 shrink-0 transition-all focus:opacity-100"
                                                            onClick={() => {
                                                                const newParams = { ...suite.settings.params };
                                                                delete newParams[key];
                                                                handleUpdateSettings({ ...suite.settings, params: newParams }, suite.inherit_settings, `Parameter '${key}' removed`);
                                                            }}>
                                                            <Trash2 className="h-4 w-4" />
                                                        </Button>
                                                    </div>
                                                ))}

                                                {(!suite.settings?.params || Object.keys(suite.settings.params).length === 0) && (!suite.inherit_settings || !suite.effective_settings?.params || Object.keys(suite.effective_settings.params).length === 0) && (
                                                    <div className="text-center py-6 text-sm text-slate-400 font-medium">No custom parameters configured.</div>
                                                )}
                                            </div>

                                            <div className="pt-4 border-t border-slate-100 mt-auto">
                                                <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
                                                    <input
                                                        value={paramKey}
                                                        onChange={(e) => setParamKey(e.target.value)}
                                                        placeholder="Key (e.g. ?user_id=)"
                                                        className="flex-1 min-w-0 px-3.5 py-2.5 border border-slate-200 bg-white shadow-sm rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 transition-all placeholder:text-slate-400 font-medium"
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter' && paramKey && paramVal) {
                                                                const currentSettings = suite.settings || { headers: {}, params: {} };
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    params: { ...(currentSettings.params || {}), [paramKey]: paramVal }
                                                                }, suite.inherit_settings, `Parameter '${paramKey}' added`);
                                                                setParamKey('');
                                                                setParamVal('');
                                                            }
                                                        }}
                                                    />
                                                    <input
                                                        value={paramVal}
                                                        onChange={(e) => setParamVal(e.target.value)}
                                                        placeholder="Value"
                                                        className="flex-1 min-w-0 px-3.5 py-2.5 border border-slate-200 bg-white shadow-sm rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 transition-all placeholder:text-slate-400 font-medium"
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter' && paramKey && paramVal) {
                                                                const currentSettings = suite.settings || { headers: {}, params: {} };
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    params: { ...(currentSettings.params || {}), [paramKey]: paramVal }
                                                                }, suite.inherit_settings, `Parameter '${paramKey}' added`);
                                                                setParamKey('');
                                                                setParamVal('');
                                                            }
                                                        }}
                                                    />
                                                    <Button
                                                        className="rounded-xl px-5 h-[42px] shadow-sm font-bold tracking-wide shrink-0 bg-slate-900 hover:bg-slate-800 text-white border-transparent w-full sm:w-auto transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                                                        disabled={!paramKey || !paramVal}
                                                        onClick={() => {
                                                            if (paramKey && paramVal) {
                                                                const currentSettings = suite.settings || { headers: {}, params: {} };
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    params: { ...(currentSettings.params || {}), [paramKey]: paramVal }
                                                                }, suite.inherit_settings, `Parameter '${paramKey}' added`);
                                                                setParamKey('');
                                                                setParamVal('');
                                                            }
                                                        }}
                                                    >Add</Button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* ── Allowed Domains (Allowlist) ── */}
                                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
                                    <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50">
                                        <h3 className="font-extrabold text-slate-900 flex items-center gap-2">
                                            <FolderOpen className="h-4 w-4 text-indigo-500" />
                                            Allowed Domains (Allowlist)
                                        </h3>
                                        <p className="text-sm text-slate-500 mt-1 max-w-2xl">
                                            By default, custom headers and parameters are only sent to the exact domain of the initial test URL. Add trusted domains here to allow sending headers/params to them (e.g. your API domains, separate auth servers).
                                        </p>
                                    </div>
                                    <div className="p-6 flex flex-col space-y-5">
                                        <div className="space-y-3">
                                            {/* Inherited Allowed Domains */}
                                            {suite.inherit_settings && suite.effective_settings?.allowed_domains &&
                                                suite.effective_settings.allowed_domains
                                                    .filter((d: any) => {
                                                        const domainName = typeof d === 'string' ? d : d.domain;
                                                        const currentDomains = suite.settings?.allowed_domains || [];
                                                        return !currentDomains.some((cd: any) => (typeof cd === 'string' ? cd : cd.domain) === domainName);
                                                    })
                                                    .map((d: any, idx: number) => {
                                                        const domainName = typeof d === 'string' ? d : d.domain;
                                                        const allowHeaders = typeof d === 'string' ? true : d.headers !== false;
                                                        const allowParams = typeof d === 'string' ? false : d.params === true;
                                                        return (
                                                            <div key={`inherited-domain-${idx}`} className="flex flex-col sm:flex-row sm:items-center gap-3 opacity-60 bg-slate-50/50 p-3 rounded-xl border border-slate-100">
                                                                <input disabled value={domainName} className="flex-1 px-3 py-2 bg-transparent text-sm font-mono text-slate-600 outline-none" />
                                                                <div className="flex items-center gap-4 bg-white/50 px-4 py-2 rounded-lg border border-slate-200 pointer-events-none">
                                                                    <label className="flex items-center gap-2 text-sm font-medium text-slate-600">
                                                                        <Checkbox checked={allowHeaders} disabled className="data-[state=checked]:bg-slate-400 data-[state=checked]:border-slate-400" /> Headers
                                                                    </label>
                                                                    <div className="w-px h-4 bg-slate-300"></div>
                                                                    <label className="flex items-center gap-2 text-sm font-medium text-slate-600">
                                                                        <Checkbox checked={allowParams} disabled className="data-[state=checked]:bg-slate-400 data-[state=checked]:border-slate-400" /> Params
                                                                    </label>
                                                                </div>
                                                                <div className="w-10 sm:block hidden" />
                                                            </div>
                                                        );
                                                    })}

                                            {/* Custom Allowed Domains */}
                                            {(suite.settings?.allowed_domains || []).map((d: any, idx: number) => {
                                                const domainName = typeof d === 'string' ? d : d.domain;
                                                const allowHeaders = typeof d === 'string' ? true : d.headers !== false;
                                                const allowParams = typeof d === 'string' ? false : d.params === true;

                                                return (
                                                    <div key={idx} className="flex flex-col sm:flex-row sm:items-center gap-3 bg-white p-3 rounded-xl border border-slate-200 shadow-sm group/item">
                                                        <input disabled value={domainName} className="flex-1 px-3 py-2 bg-transparent font-bold text-sm text-slate-800 outline-none" />
                                                        <div className="flex items-center gap-4 bg-slate-50 px-4 py-2 rounded-lg border border-slate-200">
                                                            <label className="flex items-center gap-2 text-sm font-medium text-slate-700 cursor-pointer">
                                                                <Checkbox
                                                                    checked={allowHeaders}
                                                                    className="data-[state=checked]:bg-indigo-600 data-[state=checked]:border-indigo-600"
                                                                    onCheckedChange={(checked) => {
                                                                        const newDomains = [...suite.settings.allowed_domains];
                                                                        newDomains[idx] = {
                                                                            domain: domainName,
                                                                            headers: !!checked,
                                                                            params: allowParams
                                                                        };
                                                                        handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings, "Domain permissions updated");
                                                                    }}
                                                                /> Headers
                                                            </label>
                                                            <div className="w-px h-4 bg-slate-300"></div>
                                                            <label className="flex items-center gap-2 text-sm font-medium text-slate-700 cursor-pointer">
                                                                <Checkbox
                                                                    checked={allowParams}
                                                                    className="data-[state=checked]:bg-indigo-600 data-[state=checked]:border-indigo-600"
                                                                    onCheckedChange={(checked) => {
                                                                        const newDomains = [...suite.settings.allowed_domains];
                                                                        newDomains[idx] = {
                                                                            domain: domainName,
                                                                            headers: allowHeaders,
                                                                            params: !!checked
                                                                        };
                                                                        handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings, "Domain permissions updated");
                                                                    }}
                                                                /> Params
                                                            </label>
                                                        </div>
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="w-10 h-10 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 shrink-0 self-end sm:self-auto transition-all focus:opacity-100"
                                                            onClick={() => {
                                                                const newDomains = suite.settings.allowed_domains.filter((_: any, i: number) => i !== idx);
                                                                handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings, `Domain '${domainName}' removed`);
                                                            }}>
                                                            <Trash2 className="h-4 w-4" />
                                                        </Button>
                                                    </div>
                                                );
                                            })}

                                            {(!suite.settings?.allowed_domains || suite.settings.allowed_domains.length === 0) && (!suite.inherit_settings || !suite.effective_settings?.allowed_domains || suite.effective_settings.allowed_domains.length === 0) && (
                                                <div className="text-center py-6 text-sm text-slate-400 font-medium bg-slate-50/50 rounded-xl border border-dashed border-slate-200">No domains allowed yet.</div>
                                            )}
                                        </div>

                                        <div className="pt-4 border-t border-slate-100 mt-2">
                                            <div className="flex gap-3 items-center">
                                                <div className="relative flex-1">
                                                    <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
                                                        <FolderOpen className="h-4 w-4 text-slate-400" />
                                                    </div>
                                                    <input
                                                        id="new-domain-input"
                                                        placeholder="e.g. api.yourwebsite.com"
                                                        className="w-full pl-9 pr-4 py-2.5 border border-slate-200 bg-white shadow-sm rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all placeholder:text-slate-400 font-medium"
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter') {
                                                                const val = (e.target as HTMLInputElement).value;
                                                                if (val) {
                                                                    const currentSettings = suite.settings || {};
                                                                    const currentDomains = currentSettings.allowed_domains || [];
                                                                    const exists = currentDomains.some((d: any) => (typeof d === 'string' ? d : d.domain) === val);

                                                                    if (!exists) {
                                                                        handleUpdateSettings({
                                                                            ...currentSettings,
                                                                            allowed_domains: [...currentDomains, { domain: val, headers: true, params: false }]
                                                                        }, suite.inherit_settings, `Domain '${val}' added`);
                                                                        (e.target as HTMLInputElement).value = '';
                                                                    }
                                                                }
                                                            }
                                                        }}
                                                    />
                                                </div>
                                                <Button
                                                    className="rounded-xl px-5 shadow-sm font-bold tracking-wide flex items-center shrink-0 bg-indigo-600 hover:bg-indigo-700 text-white"
                                                    onClick={() => {
                                                        const input = document.getElementById('new-domain-input') as HTMLInputElement;
                                                        const val = input.value;
                                                        if (val) {
                                                            const currentSettings = suite.settings || {};
                                                            const currentDomains = currentSettings.allowed_domains || [];
                                                            const exists = currentDomains.some((d: any) => (typeof d === 'string' ? d : d.domain) === val);

                                                            if (!exists) {
                                                                handleUpdateSettings({
                                                                    ...currentSettings,
                                                                    allowed_domains: [...currentDomains, { domain: val, headers: true, params: false }]
                                                                }, suite.inherit_settings);
                                                                input.value = '';
                                                            }
                                                        }
                                                    }}
                                                ><Plus className="h-4 w-4 mr-1.5 -ml-1" /> Add Domain</Button>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* ── Domain-Specific Overrides ── */}
                                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors mt-6">
                                    <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50">
                                        <h3 className="font-extrabold text-slate-900 flex items-center gap-2">
                                            <Globe className="h-4 w-4 text-emerald-500" />
                                            Domain Specific Overrides
                                        </h3>
                                        <p className="text-sm text-slate-500 mt-1 max-w-2xl">
                                            Configure specific headers and parameters that will only be sent when requests match a particular domain.
                                        </p>
                                    </div>
                                    <div className="p-6 flex flex-col space-y-6">
                                        {Object.entries(suite.settings?.domain_settings || {}).map(([domain, dSettings]: [string, any], idx) => (
                                            <div key={idx} className="bg-slate-50 rounded-2xl border border-slate-200 p-5 space-y-5">
                                                <div className="flex items-center justify-between pb-3 border-b border-slate-200/60">
                                                    <h4 className="font-bold text-slate-800 flex items-center gap-2">
                                                        <Globe className="h-4 w-4 text-slate-400" />
                                                        {domain}
                                                    </h4>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="text-slate-400 hover:text-rose-600 hover:bg-rose-50 h-8 w-8 rounded-lg"
                                                        onClick={() => {
                                                            const newDSettings = { ...suite.settings.domain_settings };
                                                            delete newDSettings[domain];
                                                            handleUpdateSettings({ ...suite.settings, domain_settings: newDSettings }, suite.inherit_settings, `Domain settings for '${domain}' removed`);
                                                        }}>
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>

                                                {/* Domain Headers */}
                                                <div className="space-y-3">
                                                    <h5 className="text-xs font-bold text-slate-400 uppercase tracking-widest pl-1">Headers</h5>
                                                    {Object.entries(dSettings.headers || {}).map(([pKey, pVal]: [string, any], pIdx) => (
                                                        <div key={pIdx} className="flex items-center gap-2 group/dhdr">
                                                            <input disabled value={pKey} className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm font-mono text-slate-600 shadow-sm" />
                                                            <input disabled value={pVal} className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm text-slate-600 shadow-sm" />
                                                            <Button variant="ghost" size="icon" className="h-8 w-8 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-all shrink-0" onClick={() => {
                                                                const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                                const newHeaders = { ...currentDomainSettings.headers };
                                                                delete newHeaders[pKey];
                                                                handleUpdateSettings({
                                                                    ...suite.settings,
                                                                    domain_settings: {
                                                                        ...suite.settings.domain_settings,
                                                                        [domain]: { ...currentDomainSettings, headers: newHeaders }
                                                                    }
                                                                }, suite.inherit_settings);
                                                            }}>
                                                                <Trash2 className="h-3.5 w-3.5" />
                                                            </Button>
                                                        </div>
                                                    ))}
                                                    <div className="flex flex-col sm:flex-row gap-2 pt-1 relative">
                                                        <input placeholder="Key" className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20" id={`d-hdr-key-${idx}`} />
                                                        <input placeholder="Value" className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20" id={`d-hdr-val-${idx}`} />
                                                        <Button size="sm" className="bg-slate-900 hover:bg-slate-800 text-white rounded-lg shadow-sm whitespace-nowrap shrink-0 sm:w-auto w-full" onClick={() => {
                                                            const keyInput = document.getElementById(`d-hdr-key-${idx}`) as HTMLInputElement;
                                                            const valInput = document.getElementById(`d-hdr-val-${idx}`) as HTMLInputElement;
                                                            if (keyInput.value && valInput.value) {
                                                                const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                                const newHeaders = { ...currentDomainSettings.headers, [keyInput.value]: valInput.value };
                                                                handleUpdateSettings({
                                                                    ...suite.settings,
                                                                    domain_settings: {
                                                                        ...suite.settings.domain_settings,
                                                                        [domain]: { ...currentDomainSettings, headers: newHeaders }
                                                                    }
                                                                }, suite.inherit_settings);
                                                                keyInput.value = '';
                                                                valInput.value = '';
                                                            }
                                                        }}>Add Header</Button>
                                                    </div>
                                                </div>

                                                {/* Domain Params */}
                                                <div className="space-y-3 pt-4">
                                                    <h5 className="text-xs font-bold text-slate-400 uppercase tracking-widest pl-1">Query Params</h5>
                                                    {Object.entries(dSettings.params || {}).map(([pKey, pVal]: [string, any], pIdx) => (
                                                        <div key={pIdx} className="flex items-center gap-2 group/dparam">
                                                            <input disabled value={pKey} className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm font-mono text-slate-600 shadow-sm" />
                                                            <input disabled value={pVal} className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm text-slate-600 shadow-sm" />
                                                            <Button variant="ghost" size="icon" className="h-8 w-8 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-all shrink-0" onClick={() => {
                                                                const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                                const newParams = { ...currentDomainSettings.params };
                                                                delete newParams[pKey];
                                                                handleUpdateSettings({
                                                                    ...suite.settings,
                                                                    domain_settings: {
                                                                        ...suite.settings.domain_settings,
                                                                        [domain]: { ...currentDomainSettings, params: newParams }
                                                                    }
                                                                }, suite.inherit_settings);
                                                            }}>
                                                                <Trash2 className="h-3.5 w-3.5" />
                                                            </Button>
                                                        </div>
                                                    ))}
                                                    <div className="flex flex-col sm:flex-row gap-2 pt-1 relative">
                                                        <input placeholder="Key" className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20" id={`d-param-key-${idx}`} />
                                                        <input placeholder="Value" className="flex-1 min-w-0 px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/20" id={`d-param-val-${idx}`} />
                                                        <Button size="sm" className="bg-slate-900 hover:bg-slate-800 text-white rounded-lg shadow-sm whitespace-nowrap shrink-0 sm:w-auto w-full" onClick={() => {
                                                            const keyInput = document.getElementById(`d-param-key-${idx}`) as HTMLInputElement;
                                                            const valInput = document.getElementById(`d-param-val-${idx}`) as HTMLInputElement;
                                                            if (keyInput.value && valInput.value) {
                                                                const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                                const newParams = { ...currentDomainSettings.params, [keyInput.value]: valInput.value };
                                                                handleUpdateSettings({
                                                                    ...suite.settings,
                                                                    domain_settings: {
                                                                        ...suite.settings.domain_settings,
                                                                        [domain]: { ...currentDomainSettings, params: newParams }
                                                                    }
                                                                }, suite.inherit_settings);
                                                                keyInput.value = '';
                                                                valInput.value = '';
                                                            }
                                                        }}>Add Param</Button>
                                                    </div>
                                                </div>

                                            </div>
                                        ))}

                                        {(!suite.settings?.domain_settings || Object.keys(suite.settings.domain_settings).length === 0) && (
                                            <div className="text-center py-6 text-sm text-slate-400 font-medium bg-slate-50/50 rounded-xl border border-dashed border-slate-200">No domain-specific overrides added yet.</div>
                                        )}

                                        <div className="pt-4 border-t border-slate-100 flex flex-col sm:flex-row gap-3 sm:items-center">
                                            <div className="relative flex-1">
                                                <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
                                                    <Globe className="h-4 w-4 text-slate-400" />
                                                </div>
                                                <input
                                                    id="new-domain-setting-input"
                                                    placeholder="New Domain (e.g. analytics.example.com)"
                                                    className="w-full min-w-0 pl-9 pr-4 py-2.5 border border-slate-200 bg-white shadow-sm rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all placeholder:text-slate-400 font-medium"
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter') {
                                                            const val = (e.target as HTMLInputElement).value;
                                                            if (val) {
                                                                const currentSettings = suite.settings || {};
                                                                const currentDomainSettings = currentSettings.domain_settings || {};
                                                                if (!currentDomainSettings[val]) {
                                                                    handleUpdateSettings({
                                                                        ...currentSettings,
                                                                        domain_settings: {
                                                                            ...currentDomainSettings,
                                                                            [val]: { headers: {}, params: {} }
                                                                        }
                                                                    }, suite.inherit_settings);
                                                                    (e.target as HTMLInputElement).value = '';
                                                                }
                                                            }
                                                        }
                                                    }}
                                                />
                                            </div>
                                            <Button
                                                className="rounded-xl px-5 h-[42px] shadow-sm font-bold tracking-wide flex items-center shrink-0 w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 text-white"
                                                onClick={() => {
                                                    const input = document.getElementById('new-domain-setting-input') as HTMLInputElement;
                                                    const val = input.value;
                                                    if (val) {
                                                        const currentSettings = suite.settings || {};
                                                        const currentDomainSettings = currentSettings.domain_settings || {};
                                                        if (!currentDomainSettings[val]) {
                                                            handleUpdateSettings({
                                                                ...currentSettings,
                                                                domain_settings: {
                                                                    ...currentDomainSettings,
                                                                    [val]: { headers: {}, params: {} }
                                                                }
                                                            }, suite.inherit_settings);
                                                            input.value = '';
                                                        }
                                                    }
                                                }}
                                            ><Plus className="h-4 w-4 mr-1.5 -ml-1" /> Add Domain Override</Button>
                                        </div>
                                    </div>
                                </div>

                            </motion.div>
                        ) : null}

                        {activeTab === 'audit' ? (
                            <motion.div
                                key="audit"
                                variants={tabVariants}
                                initial="hidden"
                                animate="visible"
                                exit="exit"
                                className="space-y-6"
                            >
                                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 sm:p-8">
                                    <div className="flex items-center gap-3 mb-6">
                                        <div className="w-10 h-10 rounded-xl bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-100">
                                            <History className="h-5 w-5 text-indigo-600" />
                                        </div>
                                        <div>
                                            <h2 className="text-xl font-extrabold text-slate-900">Audit Log</h2>
                                            <p className="text-sm text-slate-500 mt-1 font-medium">History of all actions performed on this module.</p>
                                        </div>
                                    </div>

                                    <div className="space-y-4">
                                        {isAuditLogLoading ? (
                                            <div className="text-center py-8 text-slate-400 font-medium bg-slate-50/50 rounded-xl border border-dashed border-slate-200">Loading audit history...</div>
                                        ) : !auditLogs || auditLogs.length === 0 ? (
                                            <div className="text-center py-8 text-sm text-slate-400 font-medium bg-slate-50/50 rounded-xl border border-dashed border-slate-200">
                                                No audit history available.
                                            </div>
                                        ) : (
                                            auditLogs.map((log: any) => (
                                                <div key={log.id} className="flex flex-col p-5 border border-slate-100 rounded-2xl bg-white shadow-sm hover:shadow-md transition-all">
                                                    <div className="flex justify-between items-start mb-3">
                                                        <div className="flex items-center gap-2.5">
                                                            <span className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider ${log.action === 'create' ? 'bg-emerald-100 text-emerald-700' :
                                                                log.action === 'update' ? 'bg-indigo-100 text-indigo-700' :
                                                                    log.action === 'delete' ? 'bg-rose-100 text-rose-700' :
                                                                        'bg-slate-100 text-slate-700'
                                                                }`}>
                                                                {log.action}
                                                            </span>
                                                            <span className="font-bold text-sm text-slate-700">
                                                                by {log.user?.full_name || 'Unknown User'}
                                                            </span>
                                                        </div>
                                                        <span className="text-xs font-semibold text-slate-400">
                                                            {new Date(log.timestamp).toLocaleString()}
                                                        </span>
                                                    </div>
                                                    {log.changes && Object.keys(log.changes).length > 0 && (
                                                        <div className="mt-3 text-sm">
                                                            {log.action === 'update' ? (
                                                                <div className="border border-slate-100 rounded-xl overflow-hidden bg-white shadow-sm">
                                                                    <table className="w-full text-left text-xs">
                                                                        <thead className="bg-slate-50/80 border-b border-slate-100">
                                                                            <tr>
                                                                                <th className="px-4 py-2.5 font-bold text-slate-500 uppercase tracking-wider">Field</th>
                                                                                <th className="px-4 py-2.5 font-bold text-slate-500 uppercase tracking-wider">Old Value</th>
                                                                                <th className="px-4 py-2.5 font-bold text-slate-500 uppercase tracking-wider">New Value</th>
                                                                            </tr>
                                                                        </thead>
                                                                        <tbody className="divide-y divide-slate-50">
                                                                            {Object.entries(log.changes).map(([key, val]: [string, any]) => (
                                                                                <tr key={key} className="hover:bg-slate-50/50 transition-colors">
                                                                                    <td className="px-4 py-3 font-semibold text-slate-700">{key}</td>
                                                                                    <td className="px-4 py-3 text-rose-600 bg-rose-50/30 font-mono break-all text-[11px]">
                                                                                        {typeof val.old === 'object' ? JSON.stringify(val.old) : String(val.old ?? 'null')}
                                                                                    </td>
                                                                                    <td className="px-4 py-3 text-emerald-600 bg-emerald-50/30 font-mono break-all text-[11px]">
                                                                                        {typeof val.new === 'object' ? JSON.stringify(val.new) : String(val.new ?? 'null')}
                                                                                    </td>
                                                                                </tr>
                                                                            ))}
                                                                        </tbody>
                                                                    </table>
                                                                </div>
                                                            ) : log.action === 'create' ? (
                                                                <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-4 text-emerald-800 text-xs shadow-sm">
                                                                    <span className="font-bold">Created with initial values:</span>
                                                                    <div className="mt-1.5 font-mono opacity-80 leading-relaxed text-[11px]">
                                                                        {Object.keys(log.changes).join(', ')}
                                                                    </div>
                                                                </div>
                                                            ) : log.action === 'import' ? (
                                                                <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-4 text-indigo-800 text-xs shadow-sm">
                                                                    <span className="font-bold">Imported data source:</span>
                                                                    <div className="mt-1.5 font-mono opacity-80 text-[11px]">
                                                                        Source: {log.changes.source || 'Unknown'}
                                                                    </div>
                                                                </div>
                                                            ) : (
                                                                <div className="mt-2 text-[11px] font-mono bg-white p-3 rounded-xl border border-slate-100 overflow-x-auto shadow-sm text-slate-600">
                                                                    <pre>{JSON.stringify(log.changes, null, 2)}</pre>
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                            ))
                                        )}
                                    </div>
                                </div>
                            </motion.div>
                        ) : null}
                    </AnimatePresence>

            {/* KEEP EXISTING DIALOGS AND MODALS EXACTLY THE SAME... */}
            {/* ── Rename Dialog, SubModule Dialog, Delete Dialogs ── */}
            <AnimatePresence>
                {showSubModuleDialog && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-gray-900/50 flex items-center justify-center p-4 z-50">
                        <div className="bg-white rounded-2xl p-6 w-full max-w-sm">
                            <h3 className="text-xl font-bold mb-4">Create Sub-Module</h3>
                            <Input placeholder="Name" value={newModuleName} onChange={(e) => setNewModuleName(e.target.value)} className="mb-4" />
                            <Input placeholder="Description (Optional)" value={newModuleDesc} onChange={(e) => setNewModuleDesc(e.target.value)} className="mb-4" />
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => setShowSubModuleDialog(false)}>Cancel</Button>
                                <Button onClick={handleCreateSubModule} disabled={!newModuleName.trim() || createSubModule.isPending}>Create</Button>
                            </div>
                        </div>
                    </motion.div>
                )}
                {showRenameDialog && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-gray-900/50 flex items-center justify-center p-4 z-50">
                        <div className="bg-white rounded-2xl p-6 w-full max-w-sm">
                            <h3 className="text-xl font-bold mb-4">Rename Module</h3>
                            <Input placeholder="Name" value={renameName} onChange={(e) => setRenameName(e.target.value)} className="mb-4" />
                            <Input placeholder="Description" value={renameDesc} onChange={(e) => setRenameDesc(e.target.value)} className="mb-4" />
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => setShowRenameDialog(false)}>Cancel</Button>
                                <Button onClick={handleRenameSuite} disabled={!renameName.trim() || renameSuite.isPending}>Save</Button>
                            </div>
                        </div>
                    </motion.div>
                )}
                {showDeleteSuiteDialog && suiteToDelete && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-gray-900/50 flex items-center justify-center p-4 z-50">
                        <div className="bg-white rounded-2xl p-6 w-full max-w-md">
                            <h3 className="text-xl font-bold mb-4 text-red-600">Delete Module?</h3>
                            <p className="text-sm text-slate-600 mb-6">
                                This will permanently delete <strong>{suiteToDelete.name}</strong> and all its sub-modules and test cases. This action cannot be undone.
                            </p>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => { setShowDeleteSuiteDialog(false); setSuiteToDelete(null); }}>Cancel</Button>
                                <Button
                                    variant="destructive"
                                    disabled={deleteSuiteMutation.isPending}
                                    onClick={() => deleteSuiteMutation.mutate(suiteToDelete.id)}
                                >
                                    {deleteSuiteMutation.isPending ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Deleting...</> : 'Delete Everything'}
                                </Button>
                            </div>
                        </div>
                    </motion.div>
                )}
                {showDeleteTestCaseDialog && testCaseToDelete && (
                    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-gray-900/50 flex items-center justify-center p-4 z-50">
                        <div className="bg-white rounded-2xl p-6 w-full max-w-md">
                            <h3 className="text-xl font-bold mb-4 text-red-600">Delete Test Case?</h3>
                            <p className="text-sm text-slate-600 mb-6">
                                Are you sure you want to delete <strong>{testCaseToDelete.name}</strong>? This action cannot be undone.
                            </p>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => { setShowDeleteTestCaseDialog(false); setTestCaseToDelete(null); }}>Cancel</Button>
                                <Button
                                    variant="destructive"
                                    disabled={deleteTestCaseMutation.isPending}
                                    onClick={() => deleteTestCaseMutation.mutate(testCaseToDelete.id)}
                                >
                                    {deleteTestCaseMutation.isPending ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Deleting...</> : 'Delete Test Case'}
                                </Button>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            <ScheduleModal
                isOpen={isScheduleModalOpen}
                onClose={() => { setIsScheduleModalOpen(false); setScheduleTarget(null); }}
                projectId={Number(projectId)}
                testSuiteId={scheduleTarget?.suiteId}
                testCaseId={scheduleTarget?.caseId}
                targetName={`${scheduleTarget?.name}`}
            />

            <GenerateCaseDialog
                suiteId={Number(suiteId)}
                open={showGenerateDialog}
                onOpenChange={setShowGenerateDialog}
            />
        </div>
    );
}
