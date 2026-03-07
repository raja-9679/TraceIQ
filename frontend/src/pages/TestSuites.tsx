import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, triggerRun, exportTestSuite, importTestSuite, getProjects } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
    Plus, Play, FolderOpen, FileText, Download, Upload,
    AlertCircle, Search, Layers, ChevronRight,
    Clock, Zap
} from 'lucide-react';
import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { usePermission } from "@/hooks/usePermission";
import { motion, Variants } from 'framer-motion';
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetTitle,
} from "@/components/ui/sheet";

const containerVariants: Variants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.05 } }
};

const itemVariants: Variants = {
    hidden: { opacity: 0, y: 20 },
    visible: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 300, damping: 24 } }
};

function ExecModeBadge({ mode }: { mode: string }) {
    const isPurple = mode === 'separate';
    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wide
            ${isPurple
                ? 'bg-purple-50 text-purple-700 border border-purple-200'
                : 'bg-indigo-50 text-indigo-700 border border-indigo-200'
        } transition-colors`}>
            <Zap size={12} className={isPurple ? 'text-purple-500' : 'text-indigo-500'} />
            {mode}
        </span>
    );
}

export default function TestSuites() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [newSuiteName, setNewSuiteName] = useState('');
    const [newSuiteDesc, setNewSuiteDesc] = useState('');
    const [newExecutionMode, setNewExecutionMode] = useState<'continuous' | 'separate'>('continuous');
    const [isCreateSheetOpen, setIsCreateSheetOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState('');

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

    const { data: suites, isLoading } = useQuery({
        queryKey: ['suites', activeProjectId],
        queryFn: () => api.get('/suites', { params: { project_id: activeProjectId } })
            .then(res => res.data.filter((s: any) => !s.parent_id)),
        enabled: !!activeProjectId
    });

    const filteredSuites = suites?.filter((suite: any) =>
        suite.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (suite.description && suite.description.toLowerCase().includes(searchTerm.toLowerCase()))
    );

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

    /* ───────── Loading skeleton ───────── */
    if (isLoading) {
        return (
            <div className="space-y-8 animate-pulse">
                <div className="h-40 bg-slate-100 rounded-3xl" />
                <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                    {[...Array(6)].map((_, i) => (
                        <div key={i} className="bg-slate-50 h-56 rounded-3xl border border-slate-200" />
                    ))}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-8 pb-12 font-sans">
            {/* ── Master Header & Hero Dashboard ── */}
            <div className="bg-white rounded-[2rem] border border-slate-200 shadow-sm overflow-hidden relative">
                <div className="absolute top-0 right-0 w-[40rem] h-[40rem] bg-indigo-50/50 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2 pointer-events-none" />
                
                <div className="p-8 relative z-10 flex flex-col lg:flex-row lg:items-center justify-between gap-8">
                    <div className="max-w-xl">
                        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-50 text-indigo-600 text-[11px] font-bold uppercase tracking-wider mb-4 border border-indigo-100/50">
                            <Layers size={13} strokeWidth={2.5} /> Test Suites
                        </div>
                        <h1 className="text-4xl font-extrabold text-slate-900 tracking-tight mb-3">
                            Project Hub
                        </h1>
                        <p className="text-slate-500 text-lg leading-relaxed">
                            Manage {activeProject?.name ? <span className="font-medium text-slate-700">"{activeProject.name}"</span> : "your project's"} testing suites, monitor coverage, and trigger executions.
                        </p>
                    </div>

                    <div className="flex flex-wrap gap-4 items-center bg-slate-50/50 p-2 rounded-2xl border border-slate-100 backdrop-blur-sm">
                        {/* Search */}
                        <div className="relative">
                            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
                            <input
                                type="text"
                                placeholder="Search suites..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-10 pr-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 w-56 sm:w-64 shadow-sm transition-all text-slate-700 placeholder:text-slate-400"
                            />
                        </div>

                        {/* Import */}
                        <div className="relative">
                            <input
                                type="file"
                                accept=".json"
                                onChange={handleImportSuite}
                                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                title="Import Suite"
                                disabled={!canUpdateProject}
                            />
                            <Button variant="outline" disabled={!canUpdateProject} className="rounded-xl border-slate-200 shadow-sm hover:bg-slate-50 h-11 px-4 text-slate-600 font-medium">
                                <Upload className="mr-2 h-4 w-4 text-slate-400" /> Import
                            </Button>
                        </div>

                        {/* Create */}
                        <Button
                            onClick={() => setIsCreateSheetOpen(true)}
                            disabled={!canUpdateProject}
                            className="rounded-xl shadow-md h-11 px-5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold transition-all hover:shadow-lg hover:-translate-y-0.5"
                        >
                            <Plus className="mr-2 h-4 w-4" strokeWidth={2.5} /> New Suite
                        </Button>
                    </div>
                </div>

                {/* Health Metrics Bar */}
                {suites && suites.length > 0 && (
                    <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-slate-100 border-t border-slate-100 bg-slate-50/30">
                        <div className="p-5 px-8">
                            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Total Suites</p>
                            <div className="flex items-baseline gap-2">
                                <span className="text-2xl font-bold text-slate-900">{suites.length}</span>
                            </div>
                        </div>
                        <div className="p-5 px-8">
                            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Total Tests</p>
                            <div className="flex items-baseline gap-2">
                                <span className="text-2xl font-bold text-slate-900">{totalTestCases}</span>
                            </div>
                        </div>
                        <div className="p-5 px-8">
                            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Sub-Modules</p>
                            <div className="flex items-baseline gap-2">
                                <span className="text-2xl font-bold text-slate-900">{totalModules}</span>
                            </div>
                        </div>
                        <div className="p-5 px-8">
                            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Status</p>
                            <div className="flex items-center gap-2 mt-1">
                                <span className="flex h-2.5 w-2.5 relative">
                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
                                </span>
                                <span className="text-sm font-medium text-emerald-600">Active</span>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* ── Slide-over Create Sheet ── */}
            <Sheet open={isCreateSheetOpen} onOpenChange={setIsCreateSheetOpen}>
                <SheetContent className="sm:max-w-md border-l-0 shadow-2xl p-0 flex flex-col bg-slate-50/50 backdrop-blur-xl">
                    <div className="p-6 sm:p-8 bg-white border-b border-slate-100">
                        <SheetTitle className="text-2xl font-bold text-slate-900 tracking-tight flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-100/50">
                                <Plus size={20} className="text-indigo-600" />
                            </div>
                            Create Suite
                        </SheetTitle>
                        <SheetDescription className="mt-2 text-slate-500">
                            Configure a new test suite foundation. You can add granular test cases and sub-modules later.
                        </SheetDescription>
                    </div>

                    <div className="p-6 sm:p-8 space-y-6 flex-1 overflow-y-auto">
                        <div className="space-y-2">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Suite Name</label>
                            <input
                                type="text"
                                value={newSuiteName}
                                onChange={(e) => setNewSuiteName(e.target.value)}
                                placeholder="e.g., Core API Regression"
                                className="w-full px-4 py-3 border border-slate-200 bg-white rounded-xl text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-medium placeholder:font-normal placeholder:text-slate-400 shadow-sm"
                                autoFocus
                            />
                        </div>

                        <div className="space-y-3">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Execution Strategy</label>
                            <div className="grid grid-cols-1 gap-3">
                                <label className={`relative flex items-start gap-4 p-4 rounded-xl border-2 cursor-pointer transition-all ${newExecutionMode === 'continuous' ? 'border-indigo-500 bg-indigo-50/50' : 'border-slate-200 bg-white hover:border-slate-300'}`}>
                                    <input type="radio" className="sr-only" checked={newExecutionMode === 'continuous'} onChange={() => setNewExecutionMode('continuous')} />
                                    <div className={`shrink-0 w-5 h-5 rounded-full border flex items-center justify-center mt-0.5 ${newExecutionMode === 'continuous' ? 'border-indigo-500' : 'border-slate-300'}`}>
                                        {newExecutionMode === 'continuous' && <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full" />}
                                    </div>
                                    <div className="flex-1">
                                        <p className={`font-semibold text-sm ${newExecutionMode === 'continuous' ? 'text-indigo-900' : 'text-slate-900'}`}>Continuous</p>
                                        <p className="text-sm text-slate-500 mt-1">Tests run sequentially sharing the same browser session.</p>
                                    </div>
                                </label>

                                <label className={`relative flex items-start gap-4 p-4 rounded-xl border-2 cursor-pointer transition-all ${newExecutionMode === 'separate' ? 'border-indigo-500 bg-indigo-50/50' : 'border-slate-200 bg-white hover:border-slate-300'}`}>
                                    <input type="radio" className="sr-only" checked={newExecutionMode === 'separate'} onChange={() => setNewExecutionMode('separate')} />
                                    <div className={`shrink-0 w-5 h-5 rounded-full border flex items-center justify-center mt-0.5 ${newExecutionMode === 'separate' ? 'border-indigo-500' : 'border-slate-300'}`}>
                                        {newExecutionMode === 'separate' && <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full" />}
                                    </div>
                                    <div className="flex-1">
                                        <p className={`font-semibold text-sm ${newExecutionMode === 'separate' ? 'text-indigo-900' : 'text-slate-900'}`}>Separate</p>
                                        <p className="text-sm text-slate-500 mt-1">Each test runs in complete isolation (clean state).</p>
                                    </div>
                                </label>
                            </div>
                        </div>

                        <div className="space-y-2">
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Description <span className="text-slate-400 font-normal normal-case">(Optional)</span></label>
                            <textarea
                                value={newSuiteDesc}
                                onChange={(e) => setNewSuiteDesc(e.target.value)}
                                placeholder="What is the purpose of this suite?"
                                rows={3}
                                className="w-full px-4 py-3 border border-slate-200 bg-white rounded-xl text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all placeholder:text-slate-400 resize-none shadow-sm"
                            />
                        </div>
                    </div>

                    <div className="p-6 bg-white border-t border-slate-100 flex gap-3">
                        <Button variant="outline" onClick={() => setIsCreateSheetOpen(false)} className="flex-1 h-12 rounded-xl text-slate-600 font-medium border-slate-200 hover:bg-slate-50">Cancel</Button>
                        <Button 
                            onClick={handleCreate} 
                            disabled={!newSuiteName.trim() || createSuite.isPending} 
                            className="flex-1 h-12 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold shadow-md transition-all disabled:opacity-50"
                        >
                            {createSuite.isPending ? 'Building...' : 'Create Suite'}
                        </Button>
                    </div>
                </SheetContent>
            </Sheet>

            {/* ── Bento-Box Suites Grid ── */}
            {filteredSuites && filteredSuites.length > 0 ? (
                <motion.div
                    className="grid gap-6 md:grid-cols-2 lg:grid-cols-3"
                    variants={containerVariants}
                    initial="hidden"
                    animate="visible"
                >
                    {filteredSuites.map((suite: any) => (
                        <motion.div
                            key={suite.id}
                            variants={itemVariants}
                            className="group bg-white rounded-3xl border border-slate-200 shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300 flex flex-col overflow-hidden relative"
                        >
                            {/* Accent Glow */}
                            <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-b from-indigo-50/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none" />

                            <div className="p-6 flex flex-col flex-1 relative z-10">
                                {/* Header */}
                                <div className="flex items-start justify-between gap-4 mb-4">
                                    <div className="w-12 h-12 rounded-2xl bg-slate-50 flex items-center justify-center shrink-0 border border-slate-100 group-hover:scale-105 group-hover:bg-white group-hover:border-indigo-100 group-hover:shadow-sm transition-all duration-300">
                                        <FolderOpen size={22} strokeWidth={1.5} className="text-indigo-600" />
                                    </div>
                                    <ExecModeBadge mode={suite.execution_mode} />
                                </div>

                                {/* Title & Desc */}
                                <h3 className="font-extrabold text-slate-900 text-lg sm:text-xl truncate leading-tight group-hover:text-indigo-600 transition-colors" title={suite.name}>
                                    {suite.name}
                                </h3>
                                
                                {suite.description ? (
                                    <p className="text-sm text-slate-500 mt-2 line-clamp-2 leading-relaxed flex-1">{suite.description}</p>
                                ) : (
                                    <p className="text-sm text-slate-400 italic mt-2 flex-1">No description provided.</p>
                                )}

                                {/* Progress / Size Indicator */}
                                <div className="mt-5 mb-4">
                                    <div className="flex justify-between text-xs font-semibold text-slate-600 mb-2">
                                        <span className="flex items-center gap-1.5"><FileText size={13} className="text-slate-400" /> {suite.total_test_cases || 0} Tests</span>
                                        <span className="flex items-center gap-1.5"><Layers size={13} className="text-slate-400" /> {suite.total_sub_modules || 0} Modules</span>
                                    </div>
                                    <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                                        {/* Fake visual bar showing ratio of tests to modules (visual flair) */}
                                        <div 
                                            className="h-full bg-slate-300 rounded-full group-hover:bg-indigo-500 transition-colors duration-500" 
                                            style={{ width: `${Math.min(100, Math.max(10, ((suite.total_test_cases || 0) * 10) / (suite.total_sub_modules || 1)))}%` }} 
                                        />
                                    </div>
                                </div>

                                {/* Footer details */}
                                <div className="flex items-center justify-between text-xs font-medium text-slate-400 mt-auto pt-4 border-t border-slate-100">
                                    <span className="flex items-center gap-1.5 font-mono bg-slate-50 px-2 py-0.5 rounded-md border border-slate-100">
                                        <Clock size={12} /> {formatDate(suite.created_at)}
                                    </span>
                                </div>

                                {/* Hover Actions Overlay */}
                                <div className="absolute inset-x-0 bottom-0 p-4 bg-white/90 backdrop-blur-sm border-t border-slate-100 translate-y-full group-hover:translate-y-0 transition-transform duration-300 flex items-center gap-2">
                                    <Link to={`/suites/${suite.id}`} className="flex-1">
                                        <Button variant="outline" className="w-full rounded-xl border-slate-200 text-slate-700 hover:text-indigo-600 hover:bg-indigo-50 hover:border-indigo-200 shadow-sm h-10">
                                            Open Suite <ChevronRight size={14} className="ml-1.5" />
                                        </Button>
                                    </Link>
                                    <Button
                                        variant="outline"
                                        size="icon"
                                        onClick={() => handleExportSuite(suite.id, suite.name)}
                                        className="rounded-xl border-slate-200 text-slate-500 hover:text-indigo-600 hover:bg-slate-50 h-10 w-10 shrink-0"
                                        title="Export suite"
                                    >
                                        <Download size={16} />
                                    </Button>
                                    <Button
                                        onClick={() => runMutation.mutate(suite.id)}
                                        disabled={runMutation.isPending || !canExecuteTest}
                                        title={!canExecuteTest ? "Permission required" : "Run this suite"}
                                        className="rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white h-10 w-10 shrink-0 p-0 shadow-md"
                                    >
                                        <Play size={15} className="ml-0.5" fill="currentColor" />
                                    </Button>
                                </div>
                            </div>
                        </motion.div>
                    ))}
                </motion.div>
            ) : (
                /* ── Empty State ── */
                <div className="bg-white rounded-[2rem] border border-slate-200 shadow-sm p-16 flex flex-col items-center justify-center min-h-[400px]">
                    {!activeProjectId ? (
                        <div className="text-center max-w-md">
                            <div className="w-20 h-20 rounded-full bg-amber-50 flex items-center justify-center mx-auto mb-5 border border-amber-100">
                                <AlertCircle size={32} className="text-amber-500" />
                            </div>
                            <h3 className="text-2xl font-bold text-slate-900 mb-2 tracking-tight">Project Required</h3>
                            <p className="text-slate-500 text-base leading-relaxed">
                                Please select a project from the top navigation to view or manage test suites.
                            </p>
                        </div>
                    ) : searchTerm ? (
                        <div className="text-center max-w-md">
                            <div className="w-20 h-20 rounded-full bg-slate-50 flex items-center justify-center mx-auto mb-5 border border-slate-100">
                                <Search size={32} className="text-slate-300" />
                            </div>
                            <h3 className="text-2xl font-bold text-slate-900 mb-2 tracking-tight">No Matches Found</h3>
                            <p className="text-slate-500 text-base leading-relaxed mb-6">
                                We couldn't find any suites matching "<span className="font-semibold text-slate-800">{searchTerm}</span>".
                            </p>
                            <Button variant="outline" onClick={() => setSearchTerm('')} className="rounded-xl border-slate-200 text-slate-600 font-medium">
                                Clear Search
                            </Button>
                        </div>
                    ) : (
                        <div className="text-center max-w-md">
                            <div className="w-24 h-24 rounded-full bg-indigo-50/50 flex flex-col items-center justify-center mx-auto mb-6 border border-indigo-100/50 shadow-inner">
                                <FolderOpen size={36} strokeWidth={1.5} className="text-indigo-400 mb-1" />
                            </div>
                            <h3 className="text-3xl font-extrabold text-slate-900 mb-3 tracking-tight">Empty Workspace</h3>
                            <p className="text-slate-500 text-base leading-relaxed mb-8">
                                Project <strong>{activeProject?.name}</strong> has no test suites yet. Create your first suite to begin organizing your automated tests.
                            </p>
                            <Button onClick={() => setIsCreateSheetOpen(true)} disabled={!canUpdateProject} size="lg" className="rounded-xl shadow-md bg-indigo-600 hover:bg-indigo-700 text-white font-semibold transform hover:-translate-y-0.5 transition-all">
                                <Plus className="mr-2 h-5 w-5" strokeWidth={2.5} /> Build Your First Suite
                            </Button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
