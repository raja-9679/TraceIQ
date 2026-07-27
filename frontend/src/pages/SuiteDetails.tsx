import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useState, useEffect, useMemo } from 'react';
import { api, triggerRun, exportTestSuite, importTestSuite, getProjects, getAuditLog, getAppBuilds } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScheduleModal } from "@/components/ScheduleModal";
import { GenerateCaseDialog } from "@/components/GenerateCaseDialog";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow
} from '@/components/ui/table';
import {
    Play, Plus, FolderOpen, FileText, Settings as SettingsIcon, Trash2, Edit, ListTodo, Download, Upload, CalendarClock, ChevronRight, Loader2, ArrowLeft, Search, LayoutGrid, List, AlertCircle, Zap, LayoutTemplateIcon, Globe, FolderTree, History, Sparkles, Smartphone
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

const containerVariants: Variants = {
    hidden: { opacity: 0 },
    show: { opacity: 1, transition: { staggerChildren: 0.05 } }
};

const itemVariants: Variants = {
    hidden: { opacity: 0, y: 15 },
    show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 300, damping: 24 } }
};

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
    sample_count: number;
    recent_failures: number;
}

function FlakinessBadge({ flake }: { flake: FlakinessEntry }) {
    const scorePct = (flake.flake_score * 100).toFixed(0);
    return (
        <span
            title={`Flake score: ${scorePct}% over ${flake.sample_count} recent runs${flake.is_quarantined ? ' — QUARANTINED (skipped at dispatch)' : ' — not quarantined'}`}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide border shrink-0 cursor-help ${flake.is_quarantined
                ? 'bg-rose-50 text-rose-700 border-rose-200'
                : 'bg-amber-50 text-amber-700 border-amber-200'}`}
        >
            <AlertCircle size={10} />
            {flake.is_quarantined ? 'Quarantined' : `Flaky ${scorePct}%`}
        </span>
    );
}

function ExecModeBadge({ mode }: { mode: string }) {
    const isPurple = mode === 'separate';
    const isGreen = mode === 'parallel';

    let colors = 'bg-indigo-50 text-indigo-700 border-indigo-200';
    let iconColor = 'text-indigo-500';

    if (isPurple) {
        colors = 'bg-purple-50 text-purple-700 border-purple-200';
        iconColor = 'text-purple-500';
    } else if (isGreen) {
        colors = 'bg-emerald-50 text-emerald-700 border-emerald-200';
        iconColor = 'text-emerald-500';
    }

    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wide border transition-colors ${colors}`}>
            <Zap size={12} className={iconColor} />
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

    const runContext = (selectedAppBuildId !== 'none' || selectedEnvId !== 'default')
        ? {
            ...(selectedAppBuildId !== 'none' ? { app_build_id: Number(selectedAppBuildId) } : {}),
            ...(selectedEnvId !== 'default' ? { environment_id: Number(selectedEnvId) } : {}),
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

    const handleRunTestCase = (caseId: number) => {
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

    return (
        <div className="max-w-[1600px] mx-auto pt-4 px-4 sm:px-8 space-y-8 pb-32 font-sans">
            {/* ── Breadcrumb & Header ── */}
            <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between py-4 bg-slate-50/80 backdrop-blur-xl border-b border-slate-200/60 shadow-[0_4px_20px_-10px_rgba(0,0,0,0.05)] mb-8 -mx-4 sm:-mx-8 px-4 sm:px-8 sticky top-0 z-30">
                <div className="space-y-4 max-w-3xl">
                    <div className="flex items-center text-sm font-medium text-slate-500 mb-3">
                        <Link to="/suites" className="hover:text-indigo-600 transition-colors flex items-center gap-1.5"><FolderOpen className="w-4 h-4" /> {activeProject?.name}</Link>
                        {suite.parent_id && (
                            <>
                                <ChevronRight className="w-4 h-4 mx-1.5 text-slate-300" />
                                <Link to={`/suites/${suite.parent_id}`} className="hover:text-indigo-600 transition-colors truncate max-w-[150px]">{suite.parent?.name || 'Parent Module'}</Link>
                            </>
                        )}
                    </div>

                    <div className="flex items-center gap-4">
                        <div className="w-14 h-14 rounded-2xl bg-indigo-50 border border-indigo-100 flex items-center justify-center shrink-0 shadow-inner">
                            <FolderOpen className="h-7 w-7 text-indigo-600" />
                        </div>
                        <div>
                            <div className="flex items-center gap-3">
                                <h1 className="text-3xl sm:text-4xl font-extrabold text-slate-900 tracking-tight">{suite.name}</h1>
                                {can("project:manage", { projectId, workspaceId }) && (
                                    <button onClick={openRenameDialog} className="p-1.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors" title="Rename module">
                                        <Edit className="w-4 h-4" />
                                    </button>
                                )}
                            </div>
                            <p className="text-slate-500 mt-1.5 text-base max-w-2xl leading-relaxed">{suite.description}</p>
                        </div>
                    </div>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                    {can("project:manage", { projectId, workspaceId }) && (
                        <div className="relative">
                            <input type="file" accept=".json" onChange={handleImportSuite} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" title="Import Module" />
                            <Button variant="outline" className="rounded-xl border-slate-200 text-slate-700 bg-white hover:bg-slate-50 shadow-sm h-11 px-4 font-medium">
                                <Upload className="mr-2 h-4 w-4 text-slate-400" /> Import
                            </Button>
                        </div>
                    )}
                    <Button variant="outline" onClick={handleExportSuite} className="rounded-xl border-slate-200 text-slate-700 bg-white hover:bg-slate-50 shadow-sm h-11 px-4 font-medium">
                        <Download className="mr-2 h-4 w-4 text-slate-400" /> Export
                    </Button>
                    {can("project:execute_test", { projectId, workspaceId }) && (
                        <Button
                            onClick={() => { setScheduleTarget({ suiteId: Number(suiteId), name: suite.name }); setIsScheduleModalOpen(true); }}
                            className="rounded-xl bg-slate-900 hover:bg-slate-800 text-white shadow-md h-11 px-5 transition-all font-semibold"
                        >
                            <CalendarClock className="mr-2 h-4 w-4" /> Schedule
                        </Button>
                    )}
                    {can("project:execute_test", { projectId, workspaceId }) && (environments?.length ?? 0) > 0 && (
                        <Select value={selectedEnvId} onValueChange={setSelectedEnvId}>
                            <SelectTrigger className="w-[190px] h-11 rounded-xl bg-white border-slate-200 shadow-sm" title="Environment for runs from this page">
                                <div className="flex items-center gap-2 min-w-0">
                                    <Globe className="w-4 h-4 text-emerald-500 shrink-0" />
                                    <SelectValue placeholder="Environment" />
                                </div>
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
                    {can("project:execute_test", { projectId, workspaceId }) && (appBuilds?.length ?? 0) > 0 && (
                        <Select value={selectedAppBuildId} onValueChange={setSelectedAppBuildId}>
                            <SelectTrigger className="w-[210px] h-11 rounded-xl bg-white border-slate-200 shadow-sm" title="Pin a mobile app build to runs from this page">
                                <div className="flex items-center gap-2 min-w-0">
                                    <Smartphone className="w-4 h-4 text-indigo-500 shrink-0" />
                                    <SelectValue placeholder="App build" />
                                </div>
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
                    {can("project:execute_test", { projectId, workspaceId }) && (
                        <Button
                            onClick={() => runMutation.mutate(Number(suiteId))}
                            disabled={runMutation.isPending}
                            className="rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white shadow-md shadow-indigo-600/20 h-11 px-6 transition-all font-semibold text-base"
                        >
                            {runMutation.isPending ? <><Loader2 className="mr-2 h-5 w-5 animate-spin" /> Starting...</> : <><Play className="mr-2 h-5 w-5" fill="currentColor" /> Run Now</>}
                        </Button>
                    )}
                </div>
            </div>

            {/* ── Main Layout (Sidebar + Content) ── */}
            <div className="flex flex-col xl:flex-row gap-8 items-start">

                {/* ── Tabs Navigation Vertical Sidebar ── */}
                <div className="w-full xl:w-64 shrink-0 bg-white rounded-3xl border border-slate-200 shadow-sm p-3 xl:sticky xl:top-[180px]">
                    <div className="flex flex-row xl:flex-col gap-1.5 overflow-x-auto xl:overflow-visible pb-2 xl:pb-0 scrollbar-hide">
                        <button
                            onClick={() => setActiveTab('tests')}
                            className={`flex items-center gap-3 px-4 py-3 xl:py-3.5 rounded-2xl text-sm font-bold transition-all whitespace-nowrap ${activeTab === 'tests' ? 'bg-indigo-50 text-indigo-700 shadow-sm border border-indigo-100/50' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900 border border-transparent'}`}
                        >
                            <ListTodo className={`w-5 h-5 ${activeTab === 'tests' ? 'text-indigo-600' : 'text-slate-400'}`} /> Tests & Modules
                        </button>
                        <button
                            onClick={() => setActiveTab('settings')}
                            className={`flex items-center gap-3 px-4 py-3 xl:py-3.5 rounded-2xl text-sm font-bold transition-all whitespace-nowrap ${activeTab === 'settings' ? 'bg-indigo-50 text-indigo-700 shadow-sm border border-indigo-100/50' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900 border border-transparent'}`}
                        >
                            <SettingsIcon className={`w-5 h-5 ${activeTab === 'settings' ? 'text-indigo-600' : 'text-slate-400'}`} /> Module Settings
                        </button>
                        <button
                            onClick={() => setActiveTab('audit')}
                            className={`flex items-center gap-3 px-4 py-3 xl:py-3.5 rounded-2xl text-sm font-bold transition-all whitespace-nowrap ${activeTab === 'audit' ? 'bg-indigo-50 text-indigo-700 shadow-sm border border-indigo-100/50' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900 border border-transparent'}`}
                        >
                            <History className={`w-5 h-5 ${activeTab === 'audit' ? 'text-indigo-600' : 'text-slate-400'}`} /> Audit Log
                        </button>
                    </div>

                    {/* Quick Stats in sidebar */}
                    <div className="hidden xl:block mt-6 px-4 py-5 border-t border-slate-100 space-y-4">
                        <div className="flex justify-between items-center text-sm">
                            <span className="text-slate-500 font-medium">Sub-modules</span>
                            <span className="font-extrabold text-slate-800 bg-slate-100 px-2.5 py-0.5 rounded-md">{suite.total_sub_modules || 0}</span>
                        </div>
                        <div className="flex justify-between items-center text-sm">
                            <span className="text-slate-500 font-medium">Test Cases</span>
                            <span className="font-extrabold text-slate-800 bg-slate-100 px-2.5 py-0.5 rounded-md">{suite.total_test_cases || 0}</span>
                        </div>
                        <div className="pt-4 border-t border-slate-100">
                            <div className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Execution Mode</div>
                            <ExecModeBadge mode={suite.execution_mode} />
                        </div>
                    </div>
                </div>

                {/* ── Content Area ── */}
                <div className="flex-1 min-w-0 w-full">
                    <AnimatePresence mode="wait">
                        {activeTab === 'tests' ? (
                            <motion.div
                                key="tests"
                                variants={tabVariants}
                                initial="hidden"
                                animate="visible"
                                exit="exit"
                                className="space-y-8"
                            >
                                {/* Search and Layout Bar */}
                                <div className="flex flex-col sm:flex-row items-center justify-between gap-4 bg-white p-3 rounded-2xl border border-slate-200 shadow-sm mb-6">
                                    <div className="relative w-full sm:w-80">
                                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                                        <Input
                                            placeholder="Search items..."
                                            className="pl-9 bg-slate-50/50 border-slate-200 shadow-inner rounded-xl focus-visible:ring-indigo-500 h-10 w-full"
                                            value={searchTerm}
                                            onChange={(e) => setSearchTerm(e.target.value)}
                                        />
                                    </div>
                                    <div className="flex w-full sm:w-auto items-center justify-between sm:justify-end gap-4">
                                        <div className="flex bg-slate-50 rounded-xl border border-slate-200 p-1 shrink-0 h-10">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className={`px-3 py-1 h-full rounded-lg text-xs font-bold ${viewMode === 'card' ? 'bg-white text-slate-900 shadow-sm border border-slate-200/50' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'}`}
                                                onClick={() => setViewMode('card')}
                                            >
                                                <LayoutGrid className="w-3.5 h-3.5 mr-1.5" /> Cards
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className={`px-3 py-1 h-full rounded-lg text-xs font-bold ${viewMode === 'list' ? 'bg-white text-slate-900 shadow-sm border border-slate-200/50' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'}`}
                                                onClick={() => setViewMode('list')}
                                            >
                                                <List className="w-3.5 h-3.5 mr-1.5" /> List
                                            </Button>
                                        </div>
                                    </div>
                                </div>


                                {/* ── Sub-Modules section ── */}
                                {suite.sub_modules && suite.sub_modules.length > 0 && (
                                    <div className="space-y-4 mb-10">
                                        <div className="flex flex-wrap items-center justify-between gap-4 mb-2">
                                            <h2 className="text-xl font-extrabold text-slate-900 flex items-center gap-2.5">
                                                <div className="p-1.5 rounded-lg bg-indigo-50 text-indigo-500 border border-indigo-100">
                                                    <FolderOpen className="h-5 w-5" />
                                                </div>
                                                Sub-Modules
                                                <span className="text-xs font-bold text-slate-500 bg-white border border-slate-200 px-2.5 py-0.5 rounded-full shadow-sm">{filteredSubModules.length}</span>
                                            </h2>
                                            <div className="flex gap-2">
                                                <Button size="sm" onClick={() => runMutation.mutate(Number(suiteId))} disabled={runMutation.isPending} className="rounded-xl border-slate-200 bg-emerald-50 text-emerald-700 shadow-sm h-9 px-3 hover:bg-emerald-600 hover:text-white font-bold transition-all">
                                                    {runMutation.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" fill="currentColor" />}
                                                    Run All
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => setShowSubModuleDialog(true)} className="rounded-xl border-slate-200 text-slate-700 bg-white shadow-sm h-9 px-3 hover:bg-slate-50 font-medium">
                                                    <Plus className="mr-1.5 h-4 w-4" /> Add Sub-Module
                                                </Button>
                                            </div>
                                        </div>

                                        {filteredSubModules.length === 0 ? (
                                            <div className="text-center py-8 text-slate-400 text-sm italic bg-slate-50/50 rounded-2xl border border-dashed border-slate-200">No sub-modules found matching "{searchTerm}".</div>
                                        ) : viewMode === 'card' ? (
                                            <motion.div
                                                className="grid gap-4 sm:gap-5 md:grid-cols-2 lg:grid-cols-3"
                                                variants={containerVariants}
                                                initial="hidden"
                                                animate="show"
                                            >
                                                {filteredSubModules.map((sub: any) => (
                                                    <motion.div key={sub.id} variants={itemVariants} className="h-full">
                                                        <div
                                                            className="h-full group bg-white rounded-3xl border border-slate-200 shadow-sm hover:shadow-xl hover:border-indigo-200/60 hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden flex flex-col"
                                                            onClick={() => navigate(`/suites/${sub.id}`)}
                                                        >
                                                            <div className="h-1.5 w-full bg-slate-100 group-hover:bg-indigo-50 transition-colors" />
                                                            <div className="p-5 flex flex-col flex-1">
                                                                <div className="flex items-start justify-between mb-3 gap-2">
                                                                    <div className="p-2.5 bg-slate-50 rounded-xl group-hover:bg-indigo-50 transition-colors border border-slate-100 shrink-0">
                                                                        <FolderOpen className="h-5 w-5 text-slate-400 group-hover:text-indigo-500 transition-colors" />
                                                                    </div>
                                                                    <div className="mt-1 flex-shrink-0"><ExecModeBadge mode={sub.execution_mode} /></div>
                                                                </div>
                                                                <div className="space-y-1.5 flex-1 min-w-0">
                                                                    <h3 className="font-bold text-slate-900 text-lg pr-4 group-hover:text-indigo-600 transition-colors">{sub.name}</h3>
                                                                    <p className="text-sm text-slate-500 line-clamp-2 leading-relaxed">{sub.description}</p>
                                                                </div>
                                                                <div className="flex items-center justify-between mt-5 pt-4 border-t border-slate-100">
                                                                    <div className="flex items-center gap-3 text-xs font-bold text-slate-400">
                                                                        <span className="flex items-center gap-1.5"><FileText className="h-3.5 w-3.5 text-slate-300" /> {sub.total_test_cases || 0}</span>
                                                                        <span className="flex items-center gap-1.5"><FolderOpen className="h-3.5 w-3.5 text-slate-300" /> {sub.total_sub_modules || 0}</span>
                                                                    </div>
                                                                    <div className="flex items-center gap-2">
                                                                        <Button
                                                                            variant="ghost"
                                                                            size="icon"
                                                                            className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 border border-transparent hover:border-indigo-200 transition-all z-10"
                                                                            onClick={(e) => { e.stopPropagation(); setScheduleTarget({ suiteId: Number(sub.id), name: sub.name }); setIsScheduleModalOpen(true); }}
                                                                            title="Schedule Module"
                                                                        >
                                                                            <CalendarClock className="h-3.5 w-3.5" />
                                                                        </Button>
                                                                        {can("project:manage", { projectId, workspaceId }) && (
                                                                            <Button
                                                                                variant="ghost"
                                                                                size="icon"
                                                                                className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 hover:text-rose-600 hover:bg-rose-50 border border-transparent hover:border-rose-200 transition-all z-10"
                                                                                onClick={(e) => { e.stopPropagation(); setSuiteToDelete({ id: sub.id, name: sub.name }); setShowDeleteSuiteDialog(true); }}
                                                                                title="Delete Module"
                                                                            >
                                                                                <Trash2 className="h-3.5 w-3.5" />
                                                                            </Button>
                                                                        )}
                                                                        <Button
                                                                            variant="ghost"
                                                                            size="sm"
                                                                            className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-50 text-emerald-700 hover:bg-emerald-600 hover:text-white rounded-lg text-xs font-bold transition-all shadow-sm z-10"
                                                                            onClick={(e) => { e.stopPropagation(); runMutation.mutate(sub.id); }}
                                                                        >
                                                                            <Play className="h-3 w-3" fill="currentColor" /> Run
                                                                        </Button>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </motion.div>
                                                ))}
                                            </motion.div>
                                        ) : (
                                            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                                                <Table>
                                                    <TableHeader className="bg-slate-50/50">
                                                        <TableRow className="border-slate-100 hover:bg-transparent">
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10">Name</TableHead>
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10">Execution</TableHead>
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10">Tests</TableHead>
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10 w-[60px]"></TableHead>
                                                        </TableRow>
                                                    </TableHeader>
                                                    <TableBody>
                                                        {filteredSubModules.map((sub: any) => (
                                                            <TableRow key={sub.id} className="border-slate-100 hover:bg-indigo-50/30 cursor-pointer group transition-colors" onClick={() => navigate(`/suites/${sub.id}`)}>
                                                                <TableCell className="py-3">
                                                                    <div className="flex items-center gap-3">
                                                                        <FolderOpen className="h-4 w-4 text-indigo-400 group-hover:text-indigo-600" />
                                                                        <span className="font-bold text-slate-700 group-hover:text-indigo-700">{sub.name}</span>
                                                                    </div>
                                                                </TableCell>
                                                                <TableCell className="py-3"><ExecModeBadge mode={sub.execution_mode} /></TableCell>
                                                                <TableCell className="py-3">
                                                                    <span className="text-xs font-bold text-slate-500 bg-slate-100 px-2 py-1 rounded-md">{sub.total_test_cases || 0}</span>
                                                                </TableCell>
                                                                <TableCell className="py-3 text-right">
                                                                    <div className="flex justify-end items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                        <Button
                                                                            variant="ghost"
                                                                            size="icon"
                                                                            className="h-8 w-8 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg"
                                                                            onClick={(e) => { e.stopPropagation(); setScheduleTarget({ suiteId: Number(sub.id), name: sub.name }); setIsScheduleModalOpen(true); }}
                                                                            title="Schedule Module"
                                                                        >
                                                                            <CalendarClock className="h-3.5 w-3.5" />
                                                                        </Button>
                                                                        <Button
                                                                            variant="ghost"
                                                                            size="icon"
                                                                            className="h-8 w-8 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg"
                                                                            onClick={(e) => { e.stopPropagation(); runMutation.mutate(sub.id); }}
                                                                            title="Run Module"
                                                                            disabled={runMutation.isPending}
                                                                        >
                                                                            <Play className="h-3.5 w-3.5" fill="currentColor" />
                                                                        </Button>
                                                                        {can("project:manage", { projectId, workspaceId }) && (
                                                                            <Button
                                                                                variant="ghost"
                                                                                size="icon"
                                                                                className="h-8 w-8 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg"
                                                                                onClick={(e) => { e.stopPropagation(); setSuiteToDelete({ id: sub.id, name: sub.name }); setShowDeleteSuiteDialog(true); }}
                                                                                title="Delete Module"
                                                                            >
                                                                                <Trash2 className="h-3.5 w-3.5" />
                                                                            </Button>
                                                                        )}
                                                                        <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg" onClick={(e) => { e.stopPropagation(); navigate(`/suites/${sub.id}`); }} title="Open Module">
                                                                            <ChevronRight className="h-4 w-4" />
                                                                        </Button>
                                                                    </div>
                                                                </TableCell>
                                                            </TableRow>
                                                        ))}
                                                    </TableBody>
                                                </Table>
                                            </div>
                                        )}
                                    </div>
                                )}


                                {/* ── Test Cases section ── */}
                                {suite.total_sub_modules === 0 && (
                                    <div className={`space-y-4 ${suite.sub_modules?.length ? 'pt-8 border-t border-slate-200/60' : ''}`}>
                                        <div className="flex flex-wrap items-center justify-between gap-4 mb-2">
                                            <h2 className="text-xl font-extrabold text-slate-900 flex items-center gap-2.5">
                                                <div className="p-1.5 rounded-lg bg-emerald-50 text-emerald-500 border border-emerald-100">
                                                    <FileText className="h-5 w-5" />
                                                </div>
                                                Test Cases
                                                <span className="text-xs font-bold text-slate-500 bg-white border border-slate-200 px-2.5 py-0.5 rounded-full shadow-sm">{filteredTestCases.length}</span>
                                            </h2>

                                            <div className="flex gap-2">
                                                {can("test:create", { projectId, workspaceId }) && (
                                                    <div className="relative">
                                                        <input type="file" accept=".json" onChange={handleImportCase} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" title="Import Test Case" />
                                                        <Button variant="outline" size="sm" className="rounded-xl border-slate-200 text-slate-700 bg-white hover:bg-slate-50 shadow-sm h-9 px-3 font-medium">
                                                            <Upload className="mr-1.5 h-4 w-4 text-slate-400" /> Import
                                                        </Button>
                                                    </div>
                                                )}
                                                <Button size="sm" onClick={() => runMutation.mutate(Number(suiteId))} disabled={runMutation.isPending} className="rounded-xl border-slate-200 bg-emerald-50 text-emerald-700 shadow-sm h-9 px-3 hover:bg-emerald-600 hover:text-white font-bold transition-all">
                                                    {runMutation.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" fill="currentColor" />}
                                                    Run All
                                                </Button>
                                                {can("test:create", { projectId, workspaceId }) && (
                                                    <>
                                                        {aiGenerationEnabled && (
                                                            <Button size="sm" onClick={() => setShowGenerateDialog(true)} className="rounded-xl border-slate-200 text-indigo-700 bg-indigo-50 shadow-sm h-9 px-3 hover:bg-indigo-100 font-medium">
                                                                <Sparkles className="mr-1.5 h-4 w-4" /> Generate
                                                            </Button>
                                                        )}
                                                        <Button size="sm" onClick={() => navigate(`/suites/${suiteId}/builder`)} className="rounded-xl border-slate-200 text-slate-700 bg-white shadow-sm h-9 px-3 hover:bg-slate-50 font-medium">
                                                            <Plus className="mr-1.5 h-4 w-4" /> Add Case
                                                        </Button>
                                                    </>
                                                )}
                                            </div>
                                        </div>

                                        {!suite.test_cases || suite.test_cases.length === 0 ? (
                                            <div className="text-center py-20 px-4 bg-slate-50/50 rounded-3xl border border-dashed border-slate-200 flex flex-col items-center">
                                                <div className="w-16 h-16 bg-white rounded-2xl flex items-center justify-center mx-auto mb-5 shadow-sm border border-slate-100">
                                                    <FileText className="h-8 w-8 text-slate-300" />
                                                </div>
                                                <h3 className="text-lg font-extrabold text-slate-900 mb-2">No test cases yet</h3>
                                                <p className="text-slate-500 text-sm mb-6 max-w-sm">Get started by building your first test case or importing an existing one.</p>
                                                <Button onClick={() => navigate(`/suites/${suiteId}/builder`)} className="rounded-xl h-11 px-6 shadow-sm bg-indigo-600 hover:bg-indigo-700 text-white font-semibold">
                                                    <Plus className="mr-2 h-4 w-4" /> Add Test Case
                                                </Button>
                                            </div>
                                        ) : filteredTestCases.length === 0 ? (
                                            <div className="text-center py-8 text-slate-400 text-sm italic bg-slate-50/50 rounded-2xl border border-dashed border-slate-200">No test cases found matching "{searchTerm}".</div>
                                        ) : viewMode === 'card' ? (
                                            <motion.div
                                                className="grid gap-4 sm:gap-5 md:grid-cols-2 lg:grid-cols-3"
                                                variants={containerVariants}
                                                initial="hidden"
                                                animate="show"
                                            >
                                                {filteredTestCases.map((tc: any) => (
                                                    <motion.div key={tc.id} variants={itemVariants} className="h-full">
                                                        <div className="group bg-white rounded-3xl border border-slate-200 shadow-sm hover:shadow-xl hover:border-emerald-200/60 hover:-translate-y-1 transition-all duration-300 flex flex-col h-full overflow-hidden">
                                                            <div className="h-1.5 w-full bg-slate-100 group-hover:bg-emerald-400 transition-colors" />
                                                            <div className="p-5 flex-1 flex flex-col">
                                                                <div className="flex items-center gap-3 mb-4">
                                                                    <div className="p-2.5 rounded-xl bg-slate-50 text-slate-400 group-hover:bg-emerald-50 group-hover:text-emerald-500 transition-colors border border-slate-100 shrink-0">
                                                                        <FileText className="h-5 w-5" />
                                                                    </div>
                                                                    <h3 className="font-extrabold text-slate-900 text-base truncate group-hover:text-emerald-700 transition-colors">{tc.name}</h3>
                                                                    {flakeByCaseId.has(tc.id) && <FlakinessBadge flake={flakeByCaseId.get(tc.id)!} />}
                                                                </div>

                                                                {/* Summary of steps */}
                                                                {tc.steps && tc.steps.length > 0 && (
                                                                    <div className="flex-1 space-y-2 mb-4">
                                                                        {tc.steps.slice(0, 3).map((step: any, idx: number) => (
                                                                            <div key={idx} className="flex gap-2 text-[11px] leading-snug">
                                                                                <span className="font-bold text-slate-300 shrink-0 w-3">{idx + 1}.</span>
                                                                                <span className="text-slate-500 line-clamp-1">
                                                                                    {typeof step === 'string' ? step : (
                                                                                        <><span className="font-bold text-emerald-600/80">{step.type}</span> {step.selector ? `on ${step.selector}` : ''}</>
                                                                                    )}
                                                                                </span>
                                                                            </div>
                                                                        ))}
                                                                        {tc.steps.length > 3 && (
                                                                            <div className="text-[10px] font-bold text-slate-400 pt-1 pl-5">+{tc.steps.length - 3} more steps</div>
                                                                        )}
                                                                    </div>
                                                                )}

                                                                {/* Card Actions overlaying bottom */}
                                                                <div className="pt-4 mt-auto border-t border-slate-100 flex items-center justify-between">
                                                                    <div className="flex gap-1.5">
                                                                        <button onClick={() => { setScheduleTarget({ suiteId: Number(suiteId), caseId: tc.id, name: tc.name }); setIsScheduleModalOpen(true); }} className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 hover:z-10 transition-colors border hover:border-indigo-200" title="Schedule">
                                                                            <CalendarClock className="h-3.5 w-3.5" />
                                                                        </button>
                                                                        {can("test:create", { projectId, workspaceId }) && (
                                                                            <button onClick={() => navigate(`/suites/${suiteId}/cases/${tc.id}/edit`)} className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 hover:z-10 transition-colors border hover:border-indigo-200" title="Edit">
                                                                                <Edit className="h-3.5 w-3.5" />
                                                                            </button>
                                                                        )}
                                                                        <button onClick={() => handleExportCase(tc.id, tc.name)} className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 hover:z-10 transition-colors border hover:border-indigo-200" title="Export">
                                                                            <Download className="h-3.5 w-3.5" />
                                                                        </button>
                                                                        {can("test:create", { projectId, workspaceId }) && (
                                                                            <button onClick={() => { setTestCaseToDelete({ id: tc.id, name: tc.name }); setShowDeleteTestCaseDialog(true); }} className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 hover:text-rose-600 hover:bg-rose-50 hover:z-10 transition-colors border hover:border-rose-200" title="Delete">
                                                                                <Trash2 className="h-3.5 w-3.5" />
                                                                            </button>
                                                                        )}
                                                                    </div>
                                                                    <button onClick={() => handleRunTestCase(tc.id)} className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-50 text-emerald-700 hover:bg-emerald-600 hover:text-white rounded-lg text-xs font-bold transition-all shadow-sm">
                                                                        <Play className="h-3 w-3" fill="currentColor" /> Run
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </motion.div>
                                                ))}
                                            </motion.div>
                                        ) : (
                                            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                                                <Table>
                                                    <TableHeader className="bg-slate-50/50">
                                                        <TableRow className="border-slate-100 hover:bg-transparent">
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10 w-[300px]">Test Case</TableHead>
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10 w-[120px]">Steps</TableHead>
                                                            <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-10 text-right">Actions</TableHead>
                                                        </TableRow>
                                                    </TableHeader>
                                                    <TableBody>
                                                        {filteredTestCases.map((tc: any) => (
                                                            <TableRow key={tc.id} className="border-slate-100 hover:bg-slate-50 group transition-colors">
                                                                <TableCell className="py-3">
                                                                    <div className="flex items-center gap-3">
                                                                        <FileText className="h-4 w-4 text-emerald-500" />
                                                                        <span className="font-bold text-slate-700 group-hover:text-emerald-700 text-sm">{tc.name}</span>
                                                                        {flakeByCaseId.has(tc.id) && <FlakinessBadge flake={flakeByCaseId.get(tc.id)!} />}
                                                                    </div>
                                                                </TableCell>
                                                                <TableCell className="py-3">
                                                                    <span className="text-xs font-bold text-slate-500 bg-slate-100 px-2 py-1 rounded-md">{tc.steps?.length || 0}</span>
                                                                </TableCell>
                                                                <TableCell className="py-3 text-right">
                                                                    <div className="flex justify-end gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                        <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg" onClick={() => handleRunTestCase(tc.id)} title="Run">
                                                                            <Play className="h-3.5 w-3.5" fill="currentColor" />
                                                                        </Button>
                                                                        <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg" onClick={() => { setScheduleTarget({ suiteId: Number(suiteId), caseId: tc.id, name: tc.name }); setIsScheduleModalOpen(true); }} title="Schedule">
                                                                            <CalendarClock className="h-3.5 w-3.5" />
                                                                        </Button>
                                                                        {can("test:create", { projectId, workspaceId }) && (
                                                                            <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg" onClick={() => navigate(`/suites/${suiteId}/cases/${tc.id}/edit`)} title="Edit">
                                                                                <Edit className="h-3.5 w-3.5" />
                                                                            </Button>
                                                                        )}
                                                                        <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg" onClick={() => handleExportCase(tc.id, tc.name)} title="Export">
                                                                            <Download className="h-3.5 w-3.5" />
                                                                        </Button>
                                                                        {can("test:create", { projectId, workspaceId }) && (
                                                                            <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg" onClick={() => { setTestCaseToDelete({ id: tc.id, name: tc.name }); setShowDeleteTestCaseDialog(true); }} title="Delete">
                                                                                <Trash2 className="h-3.5 w-3.5" />
                                                                            </Button>
                                                                        )}
                                                                    </div>
                                                                </TableCell>
                                                            </TableRow>
                                                        ))}
                                                    </TableBody>
                                                </Table>
                                            </div>
                                        )}
                                    </div>
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
                                                    ? "Tests run sequentially in a single browser session. Slowest, but most stable for consecutive actions."
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
                </div>
            </div>

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
