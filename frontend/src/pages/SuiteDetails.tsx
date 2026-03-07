import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { api, getSettings, updateTestSuite } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { usePermission } from "@/hooks/usePermission";
import { Checkbox } from "@/components/ui/checkbox";
import { Plus, Play, Trash2, Edit, FileText, FolderOpen, Search, Loader2, ChevronDown, ChevronRight, AlertCircle, Download, Upload, X, Zap, FolderTree, Settings as SettingsIcon, LayoutTemplate as LayoutTemplateIcon, RefreshCw, Globe } from 'lucide-react';
import {
    DropdownMenu,
    DropdownMenuCheckboxItem,
    DropdownMenuContent,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { triggerRun, exportTestCase, importTestCase, exportTestSuite, importTestSuite, getAuditLog } from '@/lib/api';
import { motion, AnimatePresence } from 'framer-motion';

const containerVariants = {
    hidden: { opacity: 0 },
    show: {
        opacity: 1,
        transition: {
            staggerChildren: 0.1
        }
    }
};

const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0 }
};

const tabVariants = {
    hidden: { opacity: 0, y: 10 },
    visible: { opacity: 1, y: 0 },
    exit: { opacity: 0, y: -10 }
};

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

    // We need project ID for permissions. Assuming suiteId implies project context.
    const { data: suiteDataForPerms } = useQuery({
        queryKey: ['suite-perms', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then(res => res.data),
        enabled: !!suiteId,
    });
    const { can } = usePermission();
    const projectId = suiteDataForPerms?.project_id;

    const { data: project } = useQuery({
        queryKey: ['project', projectId],
        queryFn: async () => {
            if (!projectId) return null;
            const res = await api.get(`/projects/${projectId}`);
            return res.data;
        },
        enabled: !!projectId
    });
    const workspaceId = project?.workspace_id;

    const [showSubModuleDialog, setShowSubModuleDialog] = useState(false);
    const [newModuleName, setNewModuleName] = useState('');
    const [newModuleDesc, setNewModuleDesc] = useState('');
    const [showRenameDialog, setShowRenameDialog] = useState(false);
    const [renameName, setRenameName] = useState('');
    const [renameDesc, setRenameDesc] = useState('');
    const [activeTab, setActiveTab] = useState<'tests' | 'settings' | 'audit'>('tests');
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);
    const [deleteConfirmName, setDeleteConfirmName] = useState('');
    const [showDeleteTestCaseDialog, setShowDeleteTestCaseDialog] = useState(false);
    const [testCaseToDelete, setTestCaseToDelete] = useState<{ id: number; name: string } | null>(null);
    const [headerKey, setHeaderKey] = useState('');
    const [headerVal, setHeaderVal] = useState('');
    const [paramKey, setParamKey] = useState('');
    const [paramVal, setParamVal] = useState('');
    const [moduleError, setModuleError] = useState<string | null>(null);
    const [successMessage, setSuccessMessage] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const [selectedBrowsers, setSelectedBrowsers] = useState<string[]>(['chromium']);
    const [selectedDevices, setSelectedDevices] = useState<string[]>(['Desktop']);
    const location = useLocation();

    // Load user settings to check if multi-browser is enabled
    const { data: userSettings } = useQuery({
        queryKey: ['settings'],
        queryFn: getSettings,
    });

    // Initialize selections from settings when loaded
    useEffect(() => {
        if (userSettings) {
            if (userSettings.multi_browser_enabled) {
                setSelectedBrowsers(userSettings.selected_browsers);
            } else {
                setSelectedBrowsers([userSettings.default_browser]);
            }

            if (userSettings.multi_device_enabled) {
                setSelectedDevices(userSettings.selected_devices);
            } else {
                setSelectedDevices([userSettings.default_device]);
            }
        }
    }, [userSettings]);

    const { data: auditLogs, isLoading: isAuditLogLoading } = useQuery({
        queryKey: ['audit', suiteId],
        queryFn: () => getAuditLog('suite', Number(suiteId)),
        enabled: !!suiteId && activeTab === 'audit'
    });

    // Reset state when suite changes
    useEffect(() => {
        setSearchTerm('');
        setActiveTab('tests');
    }, [suiteId]);

    useEffect(() => {
        if (location.state?.message) {
            setSuccessMessage(location.state.message);
            // Clear state to prevent message from showing again on refresh
            window.history.replaceState({}, document.title);
            const timer = setTimeout(() => setSuccessMessage(null), 3000);
            return () => clearTimeout(timer);
        }
    }, [location]);

    const { data: suite, isLoading } = useQuery({
        queryKey: ['suite', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then(res => res.data),
        enabled: !!suiteId,
    });

    const createSubModule = useMutation({
        mutationFn: (data: { name: string; description?: string; parent_id: number }) =>
            api.post(`/suites`, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            setNewModuleName('');
            setNewModuleDesc('');
            setShowSubModuleDialog(false);
            setModuleError(null);
            toast.success('Sub-module created successfully');
        },
        onError: (error: any) => {
            const msg = error.response?.data?.detail || "Failed to create sub-module";
            setModuleError(msg);
            toast.error(msg);
        }
    });

    const handleCreateSubModule = () => {
        if (newModuleName.trim()) {
            createSubModule.mutate({
                name: newModuleName,
                description: newModuleDesc || undefined,
                parent_id: Number(suiteId),
            });
        }
    };

    const deleteSuite = useMutation({
        mutationFn: () => api.delete(`/suites/${suiteId}`),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite'] });
            const parentId = suite?.parent_id;
            const targetPath = parentId ? `/suites/${parentId}` : '/suites';

            setShowDeleteDialog(false);
            navigate(targetPath, {
                state: { message: "Module deleted successfully" }
            });
            toast.success('Module deleted successfully');
        },
        onError: (_error: any) => {
            toast.error('Failed to delete module');
        }
    });

    const deleteTestCase = useMutation({
        mutationFn: (id: number) => api.delete(`/cases/${id}`),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            setShowDeleteTestCaseDialog(false);
            setTestCaseToDelete(null);
            toast.success('Test case deleted successfully');
        },
        onError: (_error: any) => {
            toast.error('Failed to delete test case');
        }
    });

    const updateSettings = useMutation({
        mutationFn: (data: any) => api.put(`/suites/${suiteId}`, data),
        onSuccess: (_data, variables) => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            if (variables.successMessage) {
                toast.success(variables.successMessage);
            } else {
                toast.success('Settings updated successfully');
            }
        },
        onError: (_error: any) => {
            toast.error('Failed to update settings');
        }
    });

    const renameSuite = useMutation({
        mutationFn: (data: { name: string; description?: string }) =>
            updateTestSuite(Number(suiteId), data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite'] });
            setShowRenameDialog(false);
            toast.success('Module updated successfully');
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to update module');
        }
    });

    const handleRenameSuite = () => {
        if (renameName.trim()) {
            renameSuite.mutate({
                name: renameName,
                description: renameDesc || undefined,
            });
        }
    };

    const handleDeleteSuite = () => {
        if (deleteConfirmName === suite.name) {
            deleteSuite.mutate();
        }
    };

    const handleUpdateSettings = (newSettings: any, inherit: boolean, successMessage?: string) => {
        const settings = newSettings || { headers: {}, params: {} };
        updateSettings.mutate({
            settings: settings,
            inherit_settings: inherit,
            successMessage
        });
    };

    const [isRunning, setIsRunning] = useState(false);

    const handleRunSuite = async () => {
        // Validate selections
        if (selectedBrowsers.length === 0) {
            setModuleError('Please select at least one browser');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }
        if (selectedDevices.length === 0) {
            setModuleError('Please select at least one device');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }

        try {
            setIsRunning(true);
            await triggerRun(Number(suiteId), undefined, selectedBrowsers, selectedDevices);
            toast.success('Test suite run started');
            navigate('/runs');
        } catch (error: any) {
            console.error("Failed to start run:", error);
            toast.error(error?.response?.data?.detail || 'Failed to start test run');
        } finally {
            setIsRunning(false);
        }
    };

    const handleRunTestCase = async (caseId: number) => {
        // Validate selections
        if (selectedBrowsers.length === 0) {
            setModuleError('Please select at least one browser');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }
        if (selectedDevices.length === 0) {
            setModuleError('Please select at least one device');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }

        try {
            await triggerRun(Number(suiteId), caseId, selectedBrowsers, selectedDevices);
            toast.success('Test case run started');
            navigate('/runs');
        } catch (error: any) {
            console.error("Failed to start run:", error);
            toast.error(error?.response?.data?.detail || 'Failed to start test run');
        }
    };

    const handleExportCase = async (caseId: number, caseName: string) => {
        try {
            const data = await exportTestCase(caseId);
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${caseName.replace(/\s+/g, '_')}_test_case.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Test case exported successfully');
        } catch (error) {
            toast.error('Failed to export test case');
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
            await importTestCase(Number(suiteId), data);
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success('Test case imported successfully');
        } catch (error: any) {
            const msg = error?.response?.data?.detail || error?.message || 'Failed to import test case';
            toast.error(msg);
        }
    };

    const handleExportSuite = async () => {
        try {
            const data = await exportTestSuite(Number(suiteId));
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${suite.name.replace(/\s+/g, '_')}_suite.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Module exported successfully');
        } catch (error) {
            toast.error('Failed to export module');
        }
    };

    const handleImportSuite = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;
        // Reset input immediately so the same file can be re-selected if needed
        event.target.value = '';

        try {
            const content = await new Promise<string>((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = (e) => resolve(e.target?.result as string);
                reader.onerror = () => reject(new Error('Failed to read file'));
                reader.readAsText(file);
            });
            const data = JSON.parse(content);
            await importTestSuite(Number(suiteId), data);
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success('Module imported successfully');
        } catch (error: any) {
            console.error('[ImportSuite] caught:', error?.response?.status, error?.response?.data);
            const responseData = error?.response?.data;
            const detail = responseData?.detail ?? responseData ?? error?.message;
            if (detail && typeof detail === 'string') {
                toast.error('Import failed', { description: detail, duration: 8000 });
            } else {
                toast.error('Failed to import module');
            }
        }
    };

    if (isLoading) return (
        <div className="space-y-8 animate-pulse font-sans max-w-[1600px] mx-auto pb-12">
            <div className="h-40 bg-slate-100 rounded-[2rem] border border-slate-200" />
            <div className="h-10 w-80 bg-slate-100 rounded-xl" />
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {[...Array(3)].map((_,i) => <div key={i} className="bg-slate-50 h-56 rounded-3xl border border-slate-200" />)}
            </div>
        </div>
    );
    if (!suite) return <div className="p-16 text-center text-slate-500 font-medium">Suite not found</div>;



    return (
        <div className="font-sans max-w-[1600px] mx-auto pb-16">
            {/* Success toast banner */}
            <AnimatePresence>
                {successMessage && (
                    <motion.div
                        initial={{ opacity: 0, y: -20, scale: 0.95 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.95 }}
                        className="fixed top-6 left-1/2 -translate-x-1/2 z-50 bg-emerald-50 border border-emerald-200 text-emerald-800 px-5 py-3 rounded-2xl flex items-center gap-3 shadow-lg"
                    >
                        <div className="h-2 w-2 bg-emerald-500 rounded-full animate-pulse" />
                        <span className="font-medium text-sm">{successMessage}</span>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* ── Master Header (Sticky & Frosted) ── */}
            <div className="sticky top-0 z-30 pt-4 pb-6 bg-slate-50/80 backdrop-blur-xl border-b border-slate-200/60 shadow-[0_4px_20px_-10px_rgba(0,0,0,0.05)] mb-8 -mx-4 sm:-mx-8 px-4 sm:px-8">
                <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
                    <div className="space-y-5 flex-1">
                        {/* Breadcrumbs */}
                        <div className="flex items-center text-sm text-slate-400 gap-2">
                            <button onClick={() => navigate('/suites')} className="hover:text-indigo-600 transition-colors font-semibold px-2 py-1 -ml-2 rounded-md hover:bg-slate-100">
                                Suites
                            </button>
                            {suite.parent && (
                                <>
                                    <ChevronRight className="h-3.5 w-3.5" strokeWidth={3} />
                                    <button onClick={() => navigate(`/suites/${suite.parent.id}`)} className="hover:text-indigo-600 transition-colors font-semibold px-2 py-1 rounded-md hover:bg-slate-100">
                                        {suite.parent.name}
                                    </button>
                                </>
                            )}
                            <ChevronRight className="h-3.5 w-3.5" strokeWidth={3} />
                            <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">{suite.name}</span>
                        </div>

                        {/* Title Row */}
                        <div className="flex items-center gap-4 flex-wrap">
                            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-slate-900">{suite.name}</h1>
                            
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => { setRenameName(suite.name); setRenameDesc(suite.description || ''); setShowRenameDialog(true); }}
                                className="w-9 h-9 mt-1 rounded-xl text-slate-400 hover:text-indigo-600 hover:bg-indigo-50"
                            >
                                <Edit className="h-4 w-4" />
                            </Button>
                            
                            <div className="mt-1">
                                <ExecModeBadge mode={suite.execution_mode} />
                            </div>
                        </div>

                        {/* Description */}
                        {suite.description && (
                            <p className="text-slate-500 max-w-3xl text-sm sm:text-base leading-relaxed bg-white/50 inline-block px-4 py-2.5 rounded-xl border border-slate-100/50">{suite.description}</p>
                        )}
                    </div>

                    {/* Meta Stats & Core Actions */}
                    <div className="flex flex-col sm:flex-row sm:items-center gap-4 shrink-0 bg-white p-2.5 rounded-2xl border border-slate-200 shadow-sm">
                        <div className="flex items-center gap-6 px-4 py-1.5">
                            <div className="flex flex-col">
                                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-0.5">Tests</span>
                                <span className="text-xl font-black text-slate-800 flex items-center gap-2"><FileText size={16} className="text-indigo-400" />{suite.total_test_cases || 0}</span>
                            </div>
                            <div className="w-px h-8 bg-slate-100" />
                            <div className="flex flex-col">
                                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-0.5">Modules</span>
                                <span className="text-xl font-black text-slate-800 flex items-center gap-2"><FolderOpen size={16} className="text-amber-400" />{suite.total_sub_modules}</span>
                            </div>
                        </div>
                        
                        <div className="w-full sm:w-px sm:h-10 bg-slate-100 hidden sm:block" />
                        
                        <div className="flex items-center gap-2 pl-2">
                            <Button
                                variant="outline"
                                onClick={handleExportSuite}
                                className="rounded-xl h-11 border-slate-200 text-slate-700 hover:bg-slate-50 transition-all hover:scale-105 active:scale-95"
                                title="Export Suite as JSON"
                                size="icon"
                            >
                                <Download className="h-4 w-4" />
                            </Button>

                            {can("project:delete", { projectId, workspaceId }) && (
                                <Button
                                    variant="outline"
                                    onClick={() => setShowDeleteDialog(true)}
                                    className="rounded-xl h-11 border-rose-200 bg-rose-50 text-rose-600 hover:bg-rose-100 hover:text-rose-700 transition-all hover:scale-105 active:scale-95"
                                    title="Delete Suite"
                                    size="icon"
                                >
                                    <Trash2 className="h-4 w-4 shrink-0" />
                                </Button>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Delete Confirmation Modal */}
            {showDeleteDialog && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <Card className="w-full max-w-md mx-4">
                        <CardHeader>
                            <CardTitle className="text-red-600">Delete Module?</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-sm text-gray-600">
                                This will permanently delete <strong>{suite.name}</strong> and all its sub-modules and test cases. This action cannot be undone.
                            </p>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Type <span className="font-bold">{suite.name}</span> to confirm:</label>
                                <input
                                    type="text"
                                    value={deleteConfirmName}
                                    onChange={(e) => setDeleteConfirmName(e.target.value)}
                                    className="w-full px-3 py-2 border border-input bg-background rounded-lg focus:ring-2 focus:ring-destructive outline-none"
                                    placeholder="Enter module name"
                                />
                            </div>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => { setShowDeleteDialog(false); setDeleteConfirmName(''); }}>Cancel</Button>
                                <Button
                                    variant="destructive"
                                    disabled={deleteConfirmName !== suite.name || deleteSuite.isPending}
                                    onClick={handleDeleteSuite}
                                >
                                    {deleteSuite.isPending ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Deleting...
                                        </>
                                    ) : (
                                        'Delete Everything'
                                    )}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Delete Test Case Confirmation Modal */}
            {showDeleteTestCaseDialog && testCaseToDelete && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <Card className="w-full max-w-md mx-4">
                        <CardHeader>
                            <CardTitle className="text-red-600">Delete Test Case?</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-sm text-gray-600">
                                Are you sure you want to delete <strong>{testCaseToDelete.name}</strong>? This action cannot be undone.
                            </p>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => { setShowDeleteTestCaseDialog(false); setTestCaseToDelete(null); }}>Cancel</Button>
                                <Button
                                    variant="destructive"
                                    disabled={deleteTestCase.isPending}
                                    onClick={() => deleteTestCase.mutate(testCaseToDelete.id)}
                                >
                                    {deleteTestCase.isPending ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Deleting...
                                        </>
                                    ) : (
                                        'Delete'
                                    )}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* ── Tab bar ── */}
            <div className="flex px-4 sm:px-0 border-b border-slate-200 mt-2">
                {(['tests', 'settings', 'audit'] as const).map((tab) => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={`px-6 py-4 text-sm font-bold border-b-2 transition-all capitalize tracking-wide ${
                            activeTab === tab
                                ? 'border-primary text-primary bg-primary/5'
                                : 'border-transparent text-slate-400 hover:text-slate-700 hover:border-slate-300 hover:bg-slate-50'
                        }`}
                    >
                        {tab === 'tests' ? 'Tests & Modules' : tab === 'settings' ? 'Settings' : 'Audit Log'}
                    </button>
                ))}
            </div>

            <div className="px-4 sm:px-0 mt-8">
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
                            {/* Controls Bar */}
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                                <div className="relative flex-1 max-w-sm">
                                    <Search className="absolute left-3.5 top-1/2 transform -translate-y-1/2 h-4 w-4 text-slate-400" />
                                    <input
                                        type="text"
                                        placeholder="Search modules or test cases..."
                                        value={searchTerm}
                                        onChange={(e) => setSearchTerm(e.target.value)}
                                        className="w-full pl-10 pr-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
                                    />
                                </div>
                                <div className="flex flex-wrap items-center gap-2">
                                    {suite.total_sub_modules === 0 && (
                                        <div className="flex items-center gap-2 pr-4 sm:border-r border-slate-100">
                                            {can("test:create", { projectId, workspaceId }) && (
                                                <Button onClick={() => navigate(`/suites/${suiteId}/builder`)} className="rounded-xl shadow-sm h-10 px-4 whitespace-nowrap">
                                                    <Plus className="mr-2 h-4 w-4" /> New Test Case
                                                </Button>
                                            )}
                                            {can("test:create", { projectId, workspaceId }) && (
                                                <div className="relative hidden sm:block">
                                                    <input
                                                        type="file"
                                                        accept=".json"
                                                        onChange={handleImportCase}
                                                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                                        title="Import Test Case"
                                                    />
                                                    <Button variant="outline" className="rounded-xl h-10 border-slate-200 text-slate-700 bg-white hover:bg-slate-50">
                                                        <Upload className="mr-2 h-4 w-4" /> Import Case
                                                    </Button>
                                                </div>
                                            )}
                                        </div >
                                    )}
                                    {
                                        suite.total_test_cases === 0 && (
                                            <div className="flex items-center gap-2 pr-4 sm:border-r border-slate-100">
                                                {can("project:manage", { projectId, workspaceId }) && ( // Creating sub-module is structure update
                                                    <Button variant="outline" onClick={() => setShowSubModuleDialog(true)} className="rounded-xl h-10 border-slate-200 text-slate-700 bg-white hover:bg-slate-50 shadow-sm whitespace-nowrap">
                                                        <FolderOpen className="mr-2 h-4 w-4" /> New Module
                                                    </Button>
                                                )}
                                                {can("project:manage", { projectId, workspaceId }) && (
                                                    <div className="relative hidden sm:block">
                                                        <input
                                                            type="file"
                                                            accept=".json"
                                                            onChange={handleImportSuite}
                                                            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                                            title="Import Module"
                                                        />
                                                        <Button variant="outline" className="rounded-xl h-10 border-slate-200 text-slate-700 bg-white hover:bg-slate-50">
                                                            <Upload className="mr-2 h-4 w-4" /> Import Module
                                                        </Button>
                                                    </div>
                                                )}
                                            </div>
                                        )
                                    }

                                    {/* Browser Selector - Only show if multi-browser is ENABLED */}
                                    {
                                        userSettings && userSettings.multi_browser_enabled && (
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild>
                                                    <Button variant="outline" className="w-[140px] justify-between rounded-xl h-10 border-slate-200 text-slate-700 bg-white hover:bg-slate-50">
                                                        {selectedBrowsers.length > 0
                                                            ? (selectedBrowsers.length === 1 ? selectedBrowsers[0] : `${selectedBrowsers.length} Browsers`)
                                                            : "Select Browser"}
                                                        <ChevronDown className="ml-2 h-4 w-4 opacity-50" />
                                                    </Button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent className="w-56 rounded-xl">
                                                    <DropdownMenuLabel>Browsers</DropdownMenuLabel>
                                                    <DropdownMenuSeparator />
                                                    {['chromium', 'firefox', 'webkit']
                                                        .filter(b => userSettings.selected_browsers.includes(b))
                                                        .map((browser) => (
                                                            <DropdownMenuCheckboxItem
                                                                key={browser}
                                                                checked={selectedBrowsers.includes(browser)}
                                                                onSelect={(e) => e.preventDefault()}
                                                                onCheckedChange={(checked) => {
                                                                    if (checked) {
                                                                        setSelectedBrowsers([...selectedBrowsers, browser]);
                                                                    } else {
                                                                        setSelectedBrowsers(selectedBrowsers.filter((b) => b !== browser));
                                                                    }
                                                                }}
                                                            >
                                                                {browser.charAt(0).toUpperCase() + browser.slice(1)}
                                                            </DropdownMenuCheckboxItem>
                                                        ))}
                                                </DropdownMenuContent>
                                            </DropdownMenu>
                                        )
                                    }

                                    {/* Device Selector - Only show if multi-device is ENABLED */}
                                    {
                                        userSettings && userSettings.multi_device_enabled && (
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild>
                                                    <Button variant="outline" className="w-[140px] justify-between rounded-xl h-10 border-slate-200 text-slate-700 bg-white hover:bg-slate-50">
                                                        {selectedDevices.length > 0
                                                            ? (selectedDevices.length === 1 ? selectedDevices[0] : `${selectedDevices.length} Devices`)
                                                            : "Select Device"}
                                                        <ChevronDown className="ml-2 h-4 w-4 opacity-50" />
                                                    </Button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent className="w-56 rounded-xl">
                                                    <DropdownMenuLabel>Devices</DropdownMenuLabel>
                                                    <DropdownMenuSeparator />
                                                    {['Desktop', 'Mobile (Generic)', 'iPhone 13', 'Pixel 5']
                                                        .filter(d => userSettings.selected_devices.includes(d))
                                                        .map((device) => (
                                                            <DropdownMenuCheckboxItem
                                                                key={device}
                                                                checked={selectedDevices.includes(device)}
                                                                onSelect={(e) => e.preventDefault()}
                                                                onCheckedChange={(checked) => {
                                                                    if (checked) {
                                                                        setSelectedDevices([...selectedDevices, device]);
                                                                    } else {
                                                                        setSelectedDevices(selectedDevices.filter((d) => d !== device));
                                                                    }
                                                                }}
                                                            >
                                                                {device}
                                                            </DropdownMenuCheckboxItem>
                                                        ))}
                                                </DropdownMenuContent>
                                            </DropdownMenu>
                                        )
                                    }

                                    <Button 
                                        onClick={handleRunSuite} 
                                        disabled={isRunning || !can("project:execute_test", { projectId, workspaceId })} 
                                        title={!can("project:execute_test", { projectId, workspaceId }) ? "Permission required" : ""}
                                        className="rounded-xl shadow-md h-10 px-5 whitespace-nowrap transition-transform active:scale-95"
                                    >
                                        {isRunning ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Starting...
                                            </>
                                        ) : (
                                            <>
                                                <Play className="mr-2 h-4 w-4 shrink-0" /> Run Suite
                                            </>
                                        )}
                                    </Button>
                                </div >
                            </div >



                        {/* ── Sub-Modules section ── */}
                        {suite.sub_modules && suite.sub_modules.length > 0 && (
                            <div className="space-y-4">
                                <div className="flex flex-wrap items-center justify-between gap-4">
                                    <h2 className="text-xl font-extrabold text-slate-900 flex items-center gap-2.5">
                                        <div className="p-1.5 rounded-lg bg-indigo-50 text-indigo-500 border border-indigo-100">
                                            <FolderOpen className="h-5 w-5" />
                                        </div>
                                        Sub-Modules
                                        <span className="text-xs font-bold text-slate-500 bg-white border border-slate-200 px-2.5 py-0.5 rounded-full shadow-sm">{suite.sub_modules.length}</span>
                                    </h2>
                                    <div className="flex items-center gap-2">
                                        {can("project:manage", { projectId, workspaceId }) && (
                                            <Button variant="outline" size="sm" onClick={() => setShowSubModuleDialog(true)} className="rounded-xl border-slate-200 text-slate-700 bg-white shadow-sm h-9 px-3 hover:bg-slate-50">
                                                <Plus className="mr-1.5 h-4 w-4" /> Add Module
                                            </Button>
                                        )}
                                        {can("project:manage", { projectId, workspaceId }) && (
                                            <div className="relative">
                                                <input type="file" accept=".json" onChange={handleImportSuite} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" title="Import Module" />
                                                <Button variant="outline" size="sm" className="rounded-xl border-slate-200 text-slate-700 bg-white shadow-sm h-9 px-3 hover:bg-slate-50">
                                                    <Upload className="mr-1.5 h-4 w-4" /> Import
                                                </Button>
                                            </div>
                                        )}
                                    </div>
                                </div>
                                <motion.div
                                    key={suiteId}
                                    className="grid gap-4 sm:gap-5 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
                                    variants={containerVariants}
                                    initial="hidden"
                                    animate="show"
                                >
                                    <AnimatePresence mode='popLayout'>
                                        {suite.sub_modules
                                            .filter((sub: any) =>
                                                sub.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                                                (sub.description && sub.description.toLowerCase().includes(searchTerm.toLowerCase()))
                                            )
                                            .map((sub: any) => (
                                                <motion.div
                                                    key={sub.id}
                                                    layout
                                                    variants={itemVariants}
                                                    initial="hidden"
                                                    animate="show"
                                                    exit={{ opacity: 0, scale: 0.9 }}
                                                    className="h-full"
                                                >
                                                    <div
                                                        className="h-full group bg-white rounded-3xl border border-slate-200 shadow-sm hover:shadow-xl hover:border-indigo-200/60 hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden flex flex-col"
                                                        onClick={() => navigate(`/suites/${sub.id}`)}
                                                    >
                                                        {/* Status strip replacing exec-mode bar */}
                                                        <div className="h-1.5 w-full bg-slate-100 group-hover:bg-indigo-50 transition-colors" />
                                                        
                                                        <div className="p-5 flex flex-col flex-1">
                                                            <div className="flex items-start justify-between mb-3 gap-2">
                                                                <div className="p-2.5 bg-slate-50 rounded-xl group-hover:bg-indigo-50 transition-colors border border-slate-100 shrink-0">
                                                                    <FolderOpen className="h-5 w-5 text-slate-400 group-hover:text-indigo-500 transition-colors" />
                                                                </div>
                                                                <div className="mt-1 flex-shrink-0">
                                                                    <ExecModeBadge mode={sub.execution_mode} />
                                                                </div>
                                                            </div>
                                                            
                                                            <div className="space-y-1.5 flex-1 min-w-0">
                                                                <h3 className="font-bold text-slate-900 truncate text-lg pr-4 group-hover:text-indigo-600 transition-colors">{sub.name}</h3>
                                                                {sub.description && (
                                                                    <p className="text-sm text-slate-500 line-clamp-2 leading-relaxed">{sub.description}</p>
                                                                )}
                                                            </div>
                                                            
                                                            <div className="flex items-center justify-between mt-5 pt-4 border-t border-slate-100">
                                                                <div className="flex items-center gap-4 text-xs font-bold text-slate-400">
                                                                    <span className="flex items-center gap-1.5">
                                                                        <FileText className="h-3.5 w-3.5 text-slate-300" /> {sub.total_test_cases || 0}
                                                                    </span>
                                                                    <span className="flex items-center gap-1.5">
                                                                        <FolderTree className="h-3.5 w-3.5 text-slate-300" /> {sub.total_sub_modules || 0}
                                                                    </span>
                                                                </div>
                                                                <div className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center opacity-0 group-hover:opacity-100 group-hover:bg-indigo-50 transition-all transform translate-x-2 group-hover:translate-x-0">
                                                                    <ChevronRight className="h-4 w-4 text-indigo-500" strokeWidth={2.5} />
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </motion.div>
                                            ))}
                                    </AnimatePresence>
                                </motion.div>
                            </div>
                        )}

                        {/* ── Create Sub-Module inline card ── */}
                        <AnimatePresence>
                            {showSubModuleDialog && (
                                <motion.div
                                    initial={{ opacity: 0, y: -10, scale: 0.98 }}
                                    animate={{ opacity: 1, y: 0, scale: 1 }}
                                    exit={{ opacity: 0, y: -10, scale: 0.98 }}
                                    transition={{ type: 'spring', stiffness: 200, damping: 22 }}
                                    className="bg-white rounded-3xl border border-primary/20 shadow-lg p-6"
                                >
                                    <div className="flex items-center justify-between mb-5">
                                        <h2 className="text-base font-bold text-gray-900 flex items-center gap-2">
                                            <div className="w-7 h-7 rounded-xl bg-primary/10 flex items-center justify-center">
                                                <Plus size={14} className="text-primary" />
                                            </div>
                                            New Sub-Module
                                        </h2>
                                        <button onClick={() => setShowSubModuleDialog(false)} className="w-7 h-7 rounded-full flex items-center justify-center hover:bg-gray-100 text-gray-400 transition-colors">
                                            <X size={14} />
                                        </button>
                                    </div>
                                    {moduleError && (
                                        <div className="bg-rose-50 border border-rose-200 text-rose-700 px-3 py-2 rounded-xl text-sm mb-4">{moduleError}</div>
                                    )}
                                    <div className="grid gap-3 md:grid-cols-2">
                                        <div className="md:col-span-2">
                                            <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Module Name *</label>
                                            <input
                                                type="text"
                                                value={newModuleName}
                                                onChange={(e) => setNewModuleName(e.target.value)}
                                                placeholder="e.g., Auth Module, Payment Flow"
                                                className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary bg-gray-50 transition-all"
                                                autoFocus
                                            />
                                        </div>
                                        <div className="md:col-span-2">
                                            <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Description <span className="normal-case font-normal">(optional)</span></label>
                                            <textarea
                                                value={newModuleDesc}
                                                onChange={(e) => setNewModuleDesc(e.target.value)}
                                                placeholder="Brief description…"
                                                rows={2}
                                                className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary bg-gray-50 resize-none transition-all"
                                            />
                                        </div>
                                    </div>
                                    <div className="flex gap-3 mt-4 justify-end">
                                        <Button variant="outline" onClick={() => setShowSubModuleDialog(false)} className="rounded-xl">Cancel</Button>
                                        <Button onClick={handleCreateSubModule} disabled={!newModuleName.trim() || createSubModule.isPending} className="rounded-xl">
                                            {createSubModule.isPending ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Creating…</> : 'Create Module'}
                                        </Button>
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {/* ── Rename Module Modal ── */}
                        <AnimatePresence>
                            {showRenameDialog && (
                                <motion.div
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    exit={{ opacity: 0 }}
                                    className="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4"
                                    onClick={() => setShowRenameDialog(false)}
                                >
                                    <motion.div
                                        initial={{ scale: 0.95, opacity: 0, y: 10 }}
                                        animate={{ scale: 1, opacity: 1, y: 0 }}
                                        exit={{ scale: 0.95, opacity: 0, y: 10 }}
                                        className="bg-white rounded-3xl p-6 max-w-md w-full shadow-2xl border border-gray-100"
                                        onClick={(e) => e.stopPropagation()}
                                    >
                                        <h3 className="text-lg font-bold text-gray-900 mb-5">Edit Module</h3>
                                        <div className="space-y-4">
                                            <div>
                                                <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Module Name *</label>
                                                <input
                                                    type="text"
                                                    value={renameName}
                                                    onChange={(e) => setRenameName(e.target.value)}
                                                    className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary bg-gray-50 transition-all"
                                                    autoFocus
                                                />
                                            </div>
                                            <div>
                                                <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Description</label>
                                                <textarea
                                                    value={renameDesc}
                                                    onChange={(e) => setRenameDesc(e.target.value)}
                                                    rows={3}
                                                    className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary bg-gray-50 resize-none transition-all"
                                                />
                                            </div>
                                        </div>
                                        <div className="flex gap-3 justify-end mt-5">
                                            <button onClick={() => setShowRenameDialog(false)} className="px-5 py-2.5 rounded-xl font-medium text-gray-600 hover:bg-gray-100 transition-colors">Cancel</button>
                                            <button
                                                onClick={handleRenameSuite}
                                                disabled={!renameName.trim() || renameSuite.isPending}
                                                className="px-5 py-2.5 bg-primary text-white rounded-xl font-medium hover:bg-primary/90 transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                                            >
                                                {renameSuite.isPending ? <><Loader2 className="h-4 w-4 animate-spin" />Saving…</> : 'Save Changes'}
                                            </button>
                                        </div>
                                    </motion.div>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {/* ── Test Cases section ── */}
                        {suite.total_sub_modules === 0 && (
                            <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
                                <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50 flex flex-wrap items-center justify-between gap-4">
                                    <h2 className="text-xl font-extrabold text-slate-900 flex items-center gap-2.5">
                                        <div className="p-1.5 rounded-lg bg-indigo-50 text-indigo-500 border border-indigo-100">
                                            <FileText className="h-5 w-5" />
                                        </div>
                                        Test Cases
                                        <span className="text-xs font-bold text-slate-500 bg-white border border-slate-200 px-2.5 py-0.5 rounded-full shadow-sm">{suite.test_cases?.length || 0}</span>
                                    </h2>
                                </div>
                                {suite.test_cases && suite.test_cases.length > 0 ? (
                                    <motion.div
                                        className="divide-y divide-slate-100"
                                        variants={containerVariants}
                                        initial="hidden"
                                        animate="show"
                                    >
                                        <AnimatePresence mode='popLayout'>
                                            {suite.test_cases
                                                .filter((tc: any) => tc.name.toLowerCase().includes(searchTerm.toLowerCase()))
                                                .map((testCase: any) => (
                                                    <motion.div
                                                        key={testCase.id}
                                                        layout
                                                        variants={itemVariants}
                                                        initial="hidden"
                                                        animate="show"
                                                        exit={{ opacity: 0, x: -20 }}
                                                        className="group px-6 py-4 hover:bg-slate-50 transition-colors"
                                                    >
                                                        <div className="flex items-start justify-between gap-4">
                                                            <div className="flex-1 space-y-2 min-w-0">
                                                                <div className="flex items-center gap-3">
                                                                    <div className="p-2 rounded-xl bg-slate-100 text-slate-400 group-hover:bg-indigo-50 group-hover:text-indigo-500 transition-colors shrink-0">
                                                                        <FileText className="h-4 w-4" />
                                                                    </div>
                                                                    <h3 className="font-bold text-slate-900 text-base truncate group-hover:text-indigo-600 transition-colors">{testCase.name}</h3>
                                                                </div>
                                                                {testCase.steps && testCase.steps.length > 0 && (
                                                                    <div className="ml-10">
                                                                        <div className="flex items-center gap-2 mb-1.5 opacity-0 group-hover:opacity-100 transition-opacity h-0 overflow-hidden group-hover:h-auto">
                                                                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Steps ({testCase.steps.length})</p>
                                                                            <div className="h-px bg-slate-200 flex-1" />
                                                                        </div>
                                                                        <ol className="list-none space-y-1.5">
                                                                            {/* Only show first 3 steps by default, expand on hover could be an enhancement later */}
                                                                            {testCase.steps.slice(0, 3).map((step: any, idx: number) => (
                                                                                <li key={idx} className="flex gap-2 text-xs">
                                                                                    <span className="font-bold text-slate-300 select-none w-4 text-right shrink-0">{idx + 1}.</span>
                                                                                    {typeof step === 'string' ? (
                                                                                        <span className="text-slate-600">{step}</span>
                                                                                    ) : (
                                                                                        <span className="text-slate-600">
                                                                                            <span className="font-bold text-indigo-500 bg-indigo-50 px-1 py-0.5 rounded">{step.type}</span>
                                                                                            {step.selector && <span className="text-slate-400"> on <code className="bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200 font-mono text-[10px]">{step.selector}</code></span>}
                                                                                            {step.value && <span className="text-slate-700 italic"> "{step.value}"</span>}
                                                                                        </span>
                                                                                    )}
                                                                                </li>
                                                                            ))}
                                                                            {testCase.steps.length > 3 && (
                                                                                <li className="text-xs text-slate-400 font-medium pl-6 pt-1">
                                                                                    + {testCase.steps.length - 3} more steps
                                                                                </li>
                                                                            )}
                                                                        </ol>
                                                                    </div>
                                                                )}
                                                            </div>
                                                            <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-all transform translate-x-4 group-hover:translate-x-0 shrink-0">
                                                                <button onClick={() => handleRunTestCase(testCase.id)} className="w-9 h-9 rounded-xl flex items-center justify-center text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 transition-all border border-transparent hover:border-emerald-100 shadow-sm" title="Run this test case">
                                                                    <Play className="h-4 w-4" fill="currentColor" />
                                                                </button>
                                                                {can("test:create", { projectId, workspaceId }) && (
                                                                    <button onClick={() => navigate(`/suites/${suiteId}/cases/${testCase.id}/edit`)} className="w-9 h-9 rounded-xl flex items-center justify-center text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all border border-transparent hover:border-indigo-100 shadow-sm" title="Edit">
                                                                        <Edit className="h-4 w-4" />
                                                                    </button>
                                                                )}
                                                                <button onClick={() => handleExportCase(testCase.id, testCase.name)} className="w-9 h-9 rounded-xl flex items-center justify-center text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all border border-transparent hover:border-indigo-100 shadow-sm" title="Export">
                                                                    <Download className="h-4 w-4" />
                                                                </button>
                                                                {can("test:create", { projectId, workspaceId }) && (
                                                                    <button onClick={() => { setTestCaseToDelete({ id: testCase.id, name: testCase.name }); setShowDeleteTestCaseDialog(true); }} className="w-9 h-9 rounded-xl flex items-center justify-center text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-all border border-transparent hover:border-rose-100 shadow-sm" title="Delete">
                                                                        <Trash2 className="h-4 w-4" />
                                                                    </button>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </motion.div>
                                                ))}
                                        </AnimatePresence>
                                    </motion.div>
                                ) : (
                                    <div className="text-center py-20 px-4 flex flex-col items-center">
                                        <div className="w-16 h-16 bg-slate-50 rounded-2xl flex items-center justify-center mx-auto mb-5 border border-slate-100 shadow-sm">
                                            <FileText className="h-8 w-8 text-slate-300" />
                                        </div>
                                        <h3 className="text-lg font-extrabold text-slate-900 mb-2">No test cases yet</h3>
                                        <p className="text-slate-500 text-sm mb-8 max-w-sm">Get started by adding your first test case to this suite or importing an existing one.</p>
                                        {suite.total_sub_modules === 0 && (
                                            <div className="flex flex-wrap items-center gap-3 justify-center">
                                                {can("test:create", { projectId, workspaceId }) && (
                                                    <Button onClick={() => navigate(`/suites/${suiteId}/builder`)} className="rounded-xl h-11 px-6 shadow-sm">
                                                        <Plus className="mr-2 h-4 w-4" /> Add Test Case
                                                    </Button>
                                                )}
                                                {can("test:create", { projectId, workspaceId }) && (
                                                    <div className="relative">
                                                        <input type="file" accept=".json" onChange={handleImportCase} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" title="Import Test Case" />
                                                        <Button variant="outline" className="rounded-xl h-11 px-6 border-slate-200 text-slate-700 hover:bg-slate-50 bg-white">
                                                            <Upload className="mr-2 h-4 w-4" /> Import Case
                                                        </Button>
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                    </motion.div >
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
                ) : activeTab === 'audit' ? (
                    <motion.div
                        key="audit"
                        variants={tabVariants}
                        initial="hidden"
                        animate="visible"
                        exit="exit"
                        className="max-w-4xl space-y-6 pb-12"
                    >
                        {/* ── Audit Log Header ── */}
                        <div className="flex items-center gap-3 pb-2 border-b border-slate-100 mb-6">
                            <div className="p-2 rounded-xl bg-slate-100 text-slate-500">
                                <FileText className="h-5 w-5" />
                            </div>
                            <div>
                                <h2 className="text-xl font-extrabold text-slate-900">Audit Log</h2>
                                <p className="text-sm text-slate-500">History of changes for this module.</p>
                            </div>
                        </div>

                        {/* ── Audit Logs List ── */}
                        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
                            {isAuditLogLoading ? (
                                <div className="flex flex-col items-center justify-center p-12 text-slate-400">
                                    <Loader2 className="h-8 w-8 animate-spin mb-4 text-indigo-500" />
                                    <p className="font-medium">Loading audit history...</p>
                                </div>
                            ) : !auditLogs || auditLogs.length === 0 ? (
                                <div className="text-center py-16 px-6">
                                    <div className="mx-auto w-16 h-16 rounded-full bg-slate-50 flex items-center justify-center mb-4 border border-slate-100 shadow-inner">
                                        <FileText className="h-6 w-6 text-slate-300" />
                                    </div>
                                    <h3 className="text-lg font-bold text-slate-900 mb-2">No activity recorded</h3>
                                    <p className="text-slate-500 max-w-sm mx-auto">There are no audit logs available for this module yet.</p>
                                </div>
                            ) : (
                                <div className="divide-y divide-slate-100">
                                    {auditLogs.map((log: any) => (
                                        <div key={log.id} className="p-6 hover:bg-slate-50/50 transition-colors flex gap-5 group items-start">
                                            <div className="shrink-0 mt-0.5 relative">
                                                <div className="w-10 h-10 rounded-full bg-white border border-slate-200 shadow-sm flex items-center justify-center group-hover:border-indigo-200 group-hover:text-indigo-600 transition-colors z-10 relative">
                                                    <span className="text-xs font-bold text-slate-600 group-hover:text-indigo-600">
                                                        {log.user?.full_name ? log.user.full_name.substring(0, 2).toUpperCase() : 'SYS'}
                                                    </span>
                                                </div>
                                                {/* Connecting line */}
                                                <div className="absolute top-10 bottom-[-1.5rem] left-1/2 -ml-[1px] w-[2px] bg-slate-100 group-last:hidden" />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center justify-between gap-4 mb-2">
                                                    <p className="text-sm text-slate-900 font-medium flex items-center gap-2">
                                                        <span className={`px-2 py-1 rounded-md text-xs font-bold uppercase
                                                            ${log.action === 'create' ? 'bg-emerald-100 text-emerald-700 border border-emerald-200/50' :
                                                                log.action === 'update' ? 'bg-indigo-100 text-indigo-700 border border-indigo-200/50' :
                                                                log.action === 'delete' ? 'bg-rose-100 text-rose-700 border border-rose-200/50' :
                                                                'bg-slate-100 text-slate-700 border border-slate-200/50'
                                                            }`
                                                        }>
                                                            {log.action}
                                                        </span>
                                                        <span className="text-slate-500">by</span> <span className="font-semibold">{log.user?.full_name || 'System'}</span>
                                                    </p>
                                                    <time className="text-xs text-slate-400 font-medium whitespace-nowrap bg-slate-50 px-2.5 py-1 rounded-lg border border-slate-100 flex items-center gap-1.5"><RefreshCw className="h-3 w-3" /> {formatDate(log.timestamp)}</time>
                                                </div>
                                                
                                                {log.changes && Object.keys(log.changes).length > 0 && (
                                                    <div className="mt-3 text-sm">
                                                        {log.action === 'update' ? (
                                                            <div className="border border-slate-200 rounded-xl overflow-hidden shadow-sm">
                                                                <table className="w-full text-left text-xs">
                                                                    <thead className="bg-slate-50 border-b border-slate-200">
                                                                        <tr>
                                                                            <th className="px-4 py-3 font-semibold text-slate-600 tracking-wide uppercase">Field</th>
                                                                            <th className="px-4 py-3 font-semibold text-slate-600 tracking-wide uppercase">Old Value</th>
                                                                            <th className="px-4 py-3 font-semibold text-slate-600 tracking-wide uppercase">New Value</th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody className="divide-y divide-slate-100">
                                                                        {Object.entries(log.changes).map(([key, val]: [string, any]) => (
                                                                            <tr key={key} className="bg-white hover:bg-slate-50/50 transition-colors">
                                                                                <td className="px-4 py-3 font-mono text-slate-500 font-medium border-r border-slate-100">{key}</td>
                                                                                <td className="px-4 py-3 text-rose-600 bg-rose-50/30 font-mono break-all border-r border-slate-100 leading-relaxed">
                                                                                    {typeof val.old === 'object' ? JSON.stringify(val.old) : String(val.old ?? 'null')}
                                                                                </td>
                                                                                <td className="px-4 py-3 text-emerald-600 bg-emerald-50/30 font-mono break-all leading-relaxed">
                                                                                    {typeof val.new === 'object' ? JSON.stringify(val.new) : String(val.new ?? 'null')}
                                                                                </td>
                                                                            </tr>
                                                                        ))}
                                                                    </tbody>
                                                                </table>
                                                            </div>
                                                        ) : log.action === 'create' ? (
                                                            <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-4 text-emerald-800 text-xs shadow-sm">
                                                                <span className="font-bold flex items-center gap-2 mb-2"><Plus className="h-3 w-3" /> Created with initial values:</span>
                                                                <div className="font-mono opacity-90 leading-relaxed bg-emerald-100/50 p-2.5 rounded-lg border border-emerald-200/50">
                                                                    {Object.keys(log.changes).join(', ')}
                                                                </div>
                                                            </div>
                                                        ) : log.action === 'import' ? (
                                                            <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-4 text-indigo-800 text-xs shadow-sm">
                                                                <span className="font-bold flex items-center gap-2 mb-2"><Upload className="h-3 w-3" /> Imported data source:</span>
                                                                <div className="font-mono opacity-90 leading-relaxed bg-indigo-100/50 p-2.5 rounded-lg border border-indigo-200/50">
                                                                    Source: {log.changes.source || 'Unknown'}
                                                                </div>
                                                            </div>
                                                        ) : log.action === 'delete' ? (
                                                            <div className="bg-rose-50 border border-rose-100 rounded-xl p-4 text-rose-800 text-xs shadow-sm">
                                                                <span className="font-bold flex items-center gap-2 mb-2"><Trash2 className="h-3 w-3" /> Deleted entity:</span>
                                                                <div className="font-mono opacity-90 leading-relaxed bg-rose-100/50 p-2.5 rounded-lg border border-rose-200/50">
                                                                    ID: {log.entity_id}
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <div className="mt-2 text-xs font-mono bg-slate-900 text-emerald-400 p-4 rounded-xl border border-slate-800 overflow-x-auto shadow-inner">
                                                                <div className="mb-2 text-slate-400 font-bold uppercase tracking-wider flex items-center gap-2"><SettingsIcon className="h-3 w-3" /> Raw Data (Action: {log.action})</div>
                                                                <pre className="leading-relaxed">{JSON.stringify(log.changes, null, 2)}</pre>
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </motion.div>
                ) : null}
            </AnimatePresence>
        </div>
        </div>
    );
}
