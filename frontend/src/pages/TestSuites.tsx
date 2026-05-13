import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, triggerRun, exportTestSuite, importTestSuite, getProjects } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { 
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow 
} from '@/components/ui/table';
import {
    Plus, Play, FolderOpen, FileText, Download, Upload,
    AlertCircle, Search, ChevronRight, LayoutGrid, List,
    Clock, Zap
} from 'lucide-react';
import { useState, useEffect, useMemo } from 'react';
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

export default function TestSuites() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [newSuiteName, setNewSuiteName] = useState('');
    const [newSuiteDesc, setNewSuiteDesc] = useState('');
    const [newExecutionMode, setNewExecutionMode] = useState<'continuous' | 'separate'>('continuous');
    const [isCreateSheetOpen, setIsCreateSheetOpen] = useState(false);
    
    // New Feature State
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
            <div className="space-y-8 animate-pulse p-4">
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
        <div className="space-y-8 pb-12 font-sans max-w-[1600px] mx-auto pt-4 px-4 sm:px-8">
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

            {!activeProjectId ? (
                <div className="p-16 text-center bg-white border border-slate-200 rounded-3xl shadow-sm max-w-2xl mx-auto mt-12">
                    <div className="w-20 h-20 bg-amber-50 rounded-full flex items-center justify-center mx-auto mb-6 border border-amber-100 shadow-[0_2px_10px_-4px_rgba(0,0,0,0.05)]">
                        <AlertCircle className="w-10 h-10 text-amber-500" />
                    </div>
                    <h3 className="text-2xl font-extrabold text-slate-900 mb-2">Project Required</h3>
                    <p className="text-slate-500">Please select a project from the top navigation to view or manage test suites.</p>
                </div>
            ) : suites?.length === 0 ? (
                <div className="p-16 text-center bg-white border border-slate-200 rounded-3xl shadow-sm max-w-2xl mx-auto mt-12">
                    <div className="w-24 h-24 rounded-full bg-indigo-50/50 flex flex-col items-center justify-center mx-auto mb-6 border border-indigo-100/50 shadow-inner">
                        <FolderOpen size={36} strokeWidth={1.5} className="text-indigo-400 mb-1" />
                    </div>
                    <h3 className="text-3xl font-extrabold text-slate-900 mb-3 tracking-tight">Empty Workspace</h3>
                    <p className="text-slate-500 text-base leading-relaxed mb-8">
                        Project <strong>{activeProject?.name}</strong> has no test suites yet. Create your first suite to begin organizing your automated tests.
                    </p>
                    <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                        <Button onClick={() => setIsCreateSheetOpen(true)} disabled={!canUpdateProject} size="lg" className="rounded-xl shadow-md bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-8 h-12">
                            <Plus className="mr-2 h-5 w-5" /> Build Your First Suite
                        </Button>
                        <div className="relative">
                            <input type="file" accept=".json" onChange={handleImportSuite} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" title="Import Suite" disabled={!canUpdateProject} />
                            <Button variant="outline" disabled={!canUpdateProject} className="rounded-xl border-slate-200 shadow-sm hover:bg-slate-50 text-slate-600 font-medium px-8 h-12">
                                <Upload className="mr-2 h-5 w-5 text-slate-400" /> Import JSON
                            </Button>
                        </div>
                    </div>
                </div>
            ) : (
                <>
                    {/* Header Setup (Unified search & toggle) */}
                    <div className="sticky top-0 z-30 py-4 bg-slate-50/80 backdrop-blur-xl border-b border-slate-200/60 shadow-[0_4px_20px_-10px_rgba(0,0,0,0.05)] mb-8 -mx-4 sm:-mx-8 px-4 sm:px-8">
                        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
                            <div className="space-y-4">
                                <div className="flex items-center text-sm text-slate-400 gap-2">
                                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Project Hub</span>
                                </div>
                                <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-slate-900">Test Suites</h1>
                                <p className="text-slate-500 max-w-2xl text-base leading-relaxed">
                                    Manage {activeProject?.name ? <span className="font-medium text-slate-700">"{activeProject.name}"</span> : "your project's"} testing suites, monitor coverage, and trigger executions.
                                </p>
                            </div>
                            
                            <div className="flex flex-col sm:flex-row items-center gap-4 flex-wrap">
                                {/* Search */}
                                <div className="relative w-full sm:w-64">
                                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                                    <Input 
                                        placeholder="Search suites..." 
                                        className="pl-9 bg-white border-slate-200 shadow-sm rounded-xl focus-visible:ring-indigo-500 h-11"
                                        value={searchTerm}
                                        onChange={(e) => setSearchTerm(e.target.value)}
                                    />
                                </div>

                                {/* Layout Toggle */}
                                <div className="flex bg-white rounded-xl border border-slate-200 shadow-sm p-1 shrink-0 h-11">
                                    <Button 
                                        variant="ghost" 
                                        size="sm" 
                                        className={`px-3 py-1.5 h-full rounded-lg ${viewMode === 'card' ? 'bg-slate-100 text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'}`}
                                        onClick={() => setViewMode('card')}
                                    >
                                        <LayoutGrid className="w-4 h-4 mr-2" /> Cards
                                    </Button>
                                    <Button 
                                        variant="ghost" 
                                        size="sm" 
                                        className={`px-3 py-1.5 h-full rounded-lg ${viewMode === 'list' ? 'bg-slate-100 text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'}`}
                                        onClick={() => setViewMode('list')}
                                    >
                                        <List className="w-4 h-4 mr-2" /> List
                                    </Button>
                                </div>
                                
                                {/* Actions */}
                                <div className="flex items-center gap-2">
                                    <div className="relative">
                                        <input type="file" accept=".json" onChange={handleImportSuite} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" title="Import Suite" disabled={!canUpdateProject} />
                                        <Button variant="outline" disabled={!canUpdateProject} className="rounded-xl border-slate-200 shadow-sm hover:bg-slate-50 text-slate-600 font-medium h-11 px-4">
                                            <Upload className="h-4 w-4 sm:mr-2 text-slate-400" /> <span className="hidden sm:inline">Import</span>
                                        </Button>
                                    </div>
                                    <Button onClick={() => setIsCreateSheetOpen(true)} disabled={!canUpdateProject} className="rounded-xl shadow-md h-11 px-5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold transition-all">
                                        <Plus className="mr-2 h-4 w-4" strokeWidth={2.5} /> New Suite
                                    </Button>
                                </div>
                            </div>
                        </div>

                        {/* Sticky Metrics Strip below header */}
                        <div className="flex items-center gap-6 mt-6 px-5 py-3 bg-white rounded-2xl border border-slate-200 shadow-sm lg:w-max">
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Suites</span>
                                <span className="text-lg font-black text-slate-800">{suites.length}</span>
                            </div>
                            <div className="w-px h-6 bg-slate-100" />
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Tests</span>
                                <span className="text-lg font-black text-slate-800 flex items-center gap-1.5"><FileText size={14} className="text-indigo-400" />{totalTestCases}</span>
                            </div>
                            <div className="w-px h-6 bg-slate-100" />
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Modules</span>
                                <span className="text-lg font-black text-slate-800 flex items-center gap-1.5"><FolderOpen size={14} className="text-amber-400" />{totalModules}</span>
                            </div>
                        </div>
                    </div>

                    {filteredSuites.length === 0 ? (
                        <div className="p-12 text-center bg-slate-50 border border-slate-200 border-dashed rounded-3xl mt-8 max-w-2xl mx-auto">
                           <Search className="w-8 h-8 text-slate-300 mx-auto mb-4" />
                           <h3 className="text-lg font-semibold text-slate-700 mb-1">No matches found</h3>
                           <p className="text-slate-500">No suites matched your search query "{searchTerm}".</p>
                           <Button variant="ghost" onClick={() => setSearchTerm('')} className="mt-4 text-indigo-600">Clear Search</Button>
                        </div>
                    ) : viewMode === 'card' ? (
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
                                    <div className="p-6 flex flex-col flex-1 relative z-10">
                                        {/* Header */}
                                        <div className="flex items-start justify-between gap-4 mb-4">
                                            <div className="w-12 h-12 rounded-2xl bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-100/50 group-hover:bg-white group-hover:shadow-sm transition-all duration-300">
                                                <FolderOpen size={22} strokeWidth={1.5} className="text-indigo-600" />
                                            </div>
                                            <ExecModeBadge mode={suite.execution_mode} />
                                        </div>

                                        {/* Title & Desc */}
                                        <h3 className="font-extrabold text-slate-900 text-lg sm:text-xl truncate leading-tight group-hover:text-indigo-600 transition-colors" title={suite.name}>
                                            {suite.name}
                                        </h3>
                                        
                                        <p className="text-sm text-slate-500 mt-2 line-clamp-2 leading-relaxed flex-1 min-h-[40px]">
                                            {suite.description || <span className="italic opacity-60">No description provided</span>}
                                        </p>

                                        {/* Progress / Size Indicator */}
                                        <div className="mt-5 mb-4">
                                            <div className="flex justify-between text-xs font-semibold text-slate-500 mb-2">
                                                <span className="flex items-center gap-1.5"><FileText size={14} className="text-slate-400 text-indigo-400" /> {suite.total_test_cases || 0} Tests</span>
                                                <span className="flex items-center gap-1.5"><FolderOpen size={14} className="text-slate-400 text-amber-500" /> {suite.total_sub_modules || 0} Modules</span>
                                            </div>
                                            <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                                                <div 
                                                    className="h-full bg-slate-300 rounded-full group-hover:bg-indigo-500 transition-colors duration-500" 
                                                    style={{ width: `${Math.min(100, Math.max(10, ((suite.total_test_cases || 0) * 10) / (suite.total_sub_modules || 1)))}%` }} 
                                                />
                                            </div>
                                        </div>
                                    </div>
                                    {/* Footer Details */}
                                    <div className="px-6 pb-2 pt-2 flex items-center justify-between">
                                        <span className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
                                            <Clock size={12} /> {formatDate(suite.created_at)}
                                        </span>
                                        <div className="flex items-center gap-1 text-[10px] font-bold text-slate-400 tracking-wider uppercase">
                                            Status <span className="w-2 h-2 rounded-full bg-emerald-500 ml-1"></span>
                                        </div>
                                    </div>

                                    {/* Permanent Actions Overlay */}
                                    <div className="p-4 bg-slate-50/50 border-t border-slate-100 flex items-center gap-2 mt-auto">
                                        <Link to={`/suites/${suite.id}`} className="flex-1">
                                            <Button variant="outline" className="w-full bg-white rounded-xl border-slate-200 text-slate-700 hover:text-indigo-600 hover:bg-indigo-50 hover:border-indigo-200 shadow-sm h-10">
                                                Open <ChevronRight size={14} className="ml-1.5" />
                                            </Button>
                                        </Link>
                                        <Button
                                            variant="outline"
                                            size="icon"
                                            onClick={() => handleExportSuite(suite.id, suite.name)}
                                            className="bg-white rounded-xl border-slate-200 text-slate-500 hover:text-indigo-600 hover:bg-slate-50 h-10 w-10 shrink-0"
                                            title="Export suite"
                                        >
                                            <Download size={16} />
                                        </Button>
                                        <Button
                                            onClick={() => runMutation.mutate(suite.id)}
                                            disabled={runMutation.isPending || !canExecuteTest}
                                            className="rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white h-10 w-10 shrink-0 p-0 shadow-md"
                                            title={!canExecuteTest ? "Permission required" : "Run this suite"}
                                        >
                                            <Play size={15} className="ml-0.5" fill="currentColor" />
                                        </Button>
                                    </div>
                                </motion.div>
                            ))}
                        </motion.div>
                    ) : (
                        <div className="bg-white border border-slate-200 rounded-3xl overflow-hidden shadow-sm">
                            <Table>
                                <TableHeader className="bg-slate-50/50">
                                    <TableRow className="border-slate-100 hover:bg-transparent">
                                        <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[300px]">Suite Name</TableHead>
                                        <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Execution</TableHead>
                                        <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Contents</TableHead>
                                        <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Created</TableHead>
                                        <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[180px]">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredSuites.map((suite: any) => (
                                        <TableRow key={suite.id} className="border-slate-100 hover:bg-slate-50/50 transition-colors group">
                                            <TableCell className="py-4">
                                                <Link to={`/suites/${suite.id}`} className="block">
                                                    <div className="flex items-start gap-3">
                                                        <div className="p-2 bg-indigo-50 rounded-lg shrink-0 mt-0.5 group-hover:bg-indigo-100 transition-colors">
                                                            <FolderOpen className="h-4 w-4 text-indigo-600" />
                                                        </div>
                                                        <div>
                                                            <div className="font-bold text-slate-800 text-base group-hover:text-indigo-600 transition-colors">{suite.name}</div>
                                                            {suite.description && <div className="text-xs text-slate-500 mt-1 max-w-[280px] truncate" title={suite.description}>{suite.description}</div>}
                                                        </div>
                                                    </div>
                                                </Link>
                                            </TableCell>
                                            <TableCell className="py-4">
                                                <ExecModeBadge mode={suite.execution_mode} />
                                            </TableCell>
                                            <TableCell className="py-4">
                                                <div className="flex flex-col gap-1.5">
                                                    <span className="text-xs font-semibold text-slate-600 flex items-center gap-1">
                                                        <FileText className="w-3.5 h-3.5 text-indigo-400"/> {suite.total_test_cases || 0} Test Cases
                                                    </span>
                                                    <span className="text-xs font-semibold text-slate-600 flex items-center gap-1">
                                                        <FolderOpen className="w-3.5 h-3.5 text-amber-500"/> {suite.total_sub_modules || 0} Modules
                                                    </span>
                                                </div>
                                            </TableCell>
                                            <TableCell className="py-4">
                                                <span className="text-sm font-medium text-slate-500 whitespace-nowrap">
                                                    {formatDate(suite.created_at)}
                                                </span>
                                            </TableCell>
                                            <TableCell className="text-right py-4 pr-6">
                                                <div className="flex justify-end gap-1.5">
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => runMutation.mutate(suite.id)}
                                                        disabled={runMutation.isPending || !canExecuteTest}
                                                        className="h-9 w-9 rounded-xl text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50 border border-transparent shadow-[0_1px_2px_rgba(0,0,0,0.05)] bg-white"
                                                        title="Run Suite"
                                                    >
                                                        <Play className="w-4 h-4" fill="currentColor" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => handleExportSuite(suite.id, suite.name)}
                                                        className="h-9 w-9 rounded-xl text-slate-500 hover:text-indigo-600 hover:bg-indigo-50 border border-transparent shadow-[0_1px_2px_rgba(0,0,0,0.05)] bg-white"
                                                        title="Export Suite"
                                                    >
                                                        <Download className="w-4 h-4" />
                                                    </Button>
                                                    <Link to={`/suites/${suite.id}`}>
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="h-9 w-9 rounded-xl text-slate-500 hover:text-indigo-600 hover:bg-indigo-50 border border-transparent shadow-[0_1px_2px_rgba(0,0,0,0.05)] bg-white"
                                                            title="Open Suite"
                                                        >
                                                            <ChevronRight className="w-4 h-4" />
                                                        </Button>
                                                    </Link>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
