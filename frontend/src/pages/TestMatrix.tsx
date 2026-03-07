import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getRuns, deleteRun, deleteRuns } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
    Eye, Clock, CheckCircle2, Trash2,
    MoreHorizontal, Search, Activity, X, ChevronLeft,
    ChevronRight, ChevronsLeft, ChevronsRight, Globe, Hash
} from "lucide-react";
import { Link } from "react-router-dom";
import { Checkbox } from "@/components/ui/checkbox";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

/* ── Helpers ── */
function StatusDot({ status }: { status: string }) {
    const conf: Record<string, string> = {
        passed: 'bg-emerald-500 shadow-emerald-200',
        failed: 'bg-rose-500 shadow-rose-200',
        error: 'bg-amber-500 shadow-amber-200',
        running: 'bg-indigo-500 shadow-indigo-200 animate-pulse',
        pending: 'bg-slate-400',
    };
    return (
        <span className={cn('w-2 h-2 rounded-full inline-block shadow-[0_0_8px_rgba(0,0,0,0.1)]', conf[status] ?? 'bg-slate-300')} />
    );
}

function StatusBadge({ status }: { status: string }) {
    const conf: Record<string, string> = {
        passed: 'bg-emerald-50 text-emerald-700 border-emerald-200/50 shadow-sm shadow-emerald-100/50',
        failed: 'bg-rose-50 text-rose-700 border-rose-200/50 shadow-sm shadow-rose-100/50',
        error: 'bg-amber-50 text-amber-700 border-amber-200/50 shadow-sm shadow-amber-100/50',
        running: 'bg-indigo-50 text-indigo-700 border-indigo-200/50 shadow-sm shadow-indigo-100/50',
        pending: 'bg-slate-50 text-slate-600 border-slate-200/50 shadow-sm shadow-slate-100/50',
    };
    return (
        <span className={cn(
            'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border',
            conf[status] ?? 'bg-slate-50 text-slate-600 border-slate-200'
        )}>
            <StatusDot status={status} />
            {status}
        </span>
    );
}

/* ── Main Component ── */
export default function TestMatrix() {
    const queryClient = useQueryClient();
    const [selectedRuns, setSelectedRuns] = useState<number[]>([]);
    const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
    const [runToDelete, setRunToDelete] = useState<number | null>(null);
    const [isDeletingAll, setIsDeletingAll] = useState(false);
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [searchTerm, setSearchTerm] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [browserFilter, setBrowserFilter] = useState('');

    const hasFilters = !!(searchTerm || statusFilter || browserFilter);

    const { data, isLoading } = useQuery({
        queryKey: ["runs", currentPage, pageSize, searchTerm, statusFilter, browserFilter],
        queryFn: () => getRuns(
            pageSize, (currentPage - 1) * pageSize,
            searchTerm || undefined,
            statusFilter === 'all_status' ? undefined : statusFilter || undefined,
            browserFilter === 'all_browsers' ? undefined : browserFilter || undefined,
            undefined
        ),
        refetchInterval: 5000,
    });

    const runs = data?.runs || [];
    const total = data?.total || 0;
    const totalPages = Math.ceil(total / pageSize);

    const resetPage = () => setCurrentPage(1);

    const deleteMutation = useMutation({
        mutationFn: (runId: number) => deleteRun(runId),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["runs"] }); setRunToDelete(null); setDeleteDialogOpen(false); },
    });

    const deleteBulkMutation = useMutation({
        mutationFn: (data: { runIds?: number[]; all?: boolean }) => deleteRuns(data),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["runs"] }); setSelectedRuns([]); setIsDeletingAll(false); setDeleteDialogOpen(false); },
    });

    const confirmDelete = () => {
        if (runToDelete) deleteMutation.mutate(runToDelete);
        else if (isDeletingAll) deleteBulkMutation.mutate({ all: true });
        else deleteBulkMutation.mutate({ runIds: selectedRuns });
    };

    const toggleSelectAll = () => {
        setSelectedRuns(selectedRuns.length === (runs?.length || 0) ? [] : runs?.map(r => r.id) || []);
    };

    const toggleSelectRun = (runId: number) => {
        setSelectedRuns(prev => prev.includes(runId) ? prev.filter(id => id !== runId) : [...prev, runId]);
    };

    // Calculate generic stats for the health header based on ALL runs returned from the API if possible,
    // or specifically the current page to give a snapshot. (We'll use current page as proxy for recent health).
    const validRuns = runs.filter(r => r.status && r.status !== 'pending' && r.status !== 'running');
    const passedCount = validRuns.filter(r => r.status === 'passed').length;
    const avgDuration = validRuns.length > 0 ? (validRuns.reduce((acc, r) => acc + (r.duration_ms || 0), 0) / validRuns.length) : 0;
    const passRate = validRuns.length > 0 ? Math.round((passedCount / validRuns.length) * 100) : 0;

    /* ── Loading skeleton ── */
    if (isLoading && runs.length === 0) {
        return (
            <div className="space-y-8 animate-pulse max-w-[1600px] mx-auto pb-12">
                <div className="h-10 w-48 bg-slate-100 rounded-2xl" />
                <div className="grid gap-4 md:grid-cols-3">
                    {[...Array(3)].map((_, i) => <div key={i} className="h-28 rounded-3xl bg-slate-100" />)}
                </div>
                <div className="space-y-3">
                    <div className="h-12 w-full bg-slate-100 rounded-2xl" />
                    {[...Array(5)].map((_, i) => (
                        <div key={i} className="h-20 w-full bg-slate-50 rounded-2xl" />
                    ))}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-8 pb-12 font-sans max-w-[1600px] mx-auto px-4 md:px-0">
            {/* ── Header ── */}
            <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-6">
                <div>
                    <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-50 text-indigo-600 text-[10px] font-black uppercase tracking-widest mb-4 border border-indigo-100 shadow-sm">
                        <Activity size={12} strokeWidth={3} /> Execution History
                    </div>
                    <h1 className="text-4xl sm:text-5xl font-extrabold text-slate-900 tracking-tight">Test Runs</h1>
                    <p className="text-slate-500 mt-2 max-w-xl leading-relaxed">
                        Monitor test executions, track pass rates, and analyze failures across all your environments.
                    </p>
                </div>

                <div className="flex flex-wrap items-center gap-3 shrink-0">
                    <AnimatePresence>
                        {selectedRuns.length > 0 && (
                            <motion.div
                                initial={{ opacity: 0, scale: 0.9, x: 20 }}
                                animate={{ opacity: 1, scale: 1, x: 0 }}
                                exit={{ opacity: 0, scale: 0.9, x: 20 }}
                            >
                                <Button
                                    variant="destructive"
                                    onClick={() => { setRunToDelete(null); setIsDeletingAll(false); setDeleteDialogOpen(true); }}
                                    className="rounded-xl shadow-sm h-11 px-6 font-bold"
                                >
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Delete ({selectedRuns.length})
                                </Button>
                            </motion.div>
                        )}
                    </AnimatePresence>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="outline" className="rounded-xl border-slate-200 text-slate-700 bg-white shadow-sm h-11 px-5 hover:bg-slate-50 font-bold transition-all">
                                <MoreHorizontal className="mr-2 h-4 w-4" /> Options
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="rounded-xl p-2 w-48 shadow-xl border-slate-100">
                            <DropdownMenuItem
                                onClick={() => { setRunToDelete(null); setIsDeletingAll(true); setDeleteDialogOpen(true); }}
                                className="text-rose-600 focus:text-rose-700 focus:bg-rose-50 rounded-lg py-2.5 font-semibold cursor-pointer"
                            >
                                <Trash2 className="mr-2 h-4 w-4" /> Delete All Runs
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
            </div>

            {/* ── Execution Health Widgets ── */}
            <div className="grid gap-4 md:grid-cols-3">
                <div className="bg-white p-6 rounded-[2rem] border border-slate-200 shadow-[0_2px_10px_-4px_rgba(0,0,0,0.05)] flex items-center gap-5 hover:border-indigo-200 hover:shadow-md transition-all">
                    <div className="w-14 h-14 rounded-2xl bg-indigo-50 text-indigo-500 flex items-center justify-center shrink-0 border border-indigo-100">
                        <Activity className="h-6 w-6" strokeWidth={2.5} />
                    </div>
                    <div>
                        <div className="text-sm font-bold text-slate-400 mb-1 uppercase tracking-wider">Total Runs</div>
                        <div className="text-3xl font-black text-slate-800">{total}</div>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-[2rem] border border-slate-200 shadow-[0_2px_10px_-4px_rgba(0,0,0,0.05)] flex items-center gap-5 hover:border-emerald-200 hover:shadow-md transition-all">
                    <div className="w-14 h-14 rounded-2xl bg-emerald-50 text-emerald-500 flex items-center justify-center shrink-0 border border-emerald-100">
                        <CheckCircle2 className="h-6 w-6" strokeWidth={2.5} />
                    </div>
                    <div>
                        <div className="text-sm font-bold text-slate-400 mb-1 uppercase tracking-wider">Pass Rate (Page)</div>
                        <div className="text-3xl font-black text-slate-800 flex items-baseline gap-1">
                            {passRate}%
                        </div>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-[2rem] border border-slate-200 shadow-[0_2px_10px_-4px_rgba(0,0,0,0.05)] flex items-center gap-5 hover:border-amber-200 hover:shadow-md transition-all">
                    <div className="w-14 h-14 rounded-2xl bg-amber-50 text-amber-500 flex items-center justify-center shrink-0 border border-amber-100">
                        <Clock className="h-6 w-6" strokeWidth={2.5} />
                    </div>
                    <div>
                        <div className="text-sm font-bold text-slate-400 mb-1 uppercase tracking-wider">Avg Duration</div>
                        <div className="text-3xl font-black text-slate-800">
                            {avgDuration ? `${(avgDuration / 1000).toFixed(1)}s` : '—'}
                        </div>
                    </div>
                </div>
            </div>

            {/* ── Toolbar: Inline Tag-Based Filters ── */}
            <div className="bg-white p-3 rounded-2xl border border-slate-200 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
                {/* Search */}
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
                    <input
                        type="text"
                        placeholder="Search suite or test case..."
                        value={searchTerm}
                        onChange={(e) => { setSearchTerm(e.target.value); resetPage(); }}
                        className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-xl text-sm bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 focus:bg-white transition-all font-medium text-slate-800"
                    />
                </div>

                <div className="flex flex-wrap items-center gap-4">
                    {/* Status Tags */}
                    <div className="flex items-center gap-1.5 p-1 bg-slate-100/70 border border-slate-200/60 rounded-xl">
                        {[
                            { id: '', label: 'All' },
                            { id: 'passed', label: 'Passed' },
                            { id: 'failed', label: 'Failed' },
                            { id: 'running', label: 'Active' },
                        ].map((s) => (
                            <button
                                key={s.id}
                                onClick={() => { setStatusFilter(s.id); resetPage(); }}
                                className={cn(
                                    "px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer",
                                    statusFilter === s.id
                                        ? "bg-white text-slate-900 shadow-sm pointer-events-none"
                                        : "text-slate-500 hover:text-slate-700 hover:bg-slate-200/50"
                                )}
                            >
                                {s.label}
                            </button>
                        ))}
                    </div>

                    <div className="w-px h-6 bg-slate-200 hidden md:block" />

                    {/* Browser Tags */}
                    <div className="flex items-center gap-1.5 p-1 bg-slate-100/70 border border-slate-200/60 rounded-xl">
                        {[
                            { id: '', label: 'All' },
                            { id: 'chromium', label: 'Chrome' },
                            { id: 'firefox', label: 'Firefox' },
                            { id: 'webkit', label: 'Safari' },
                        ].map((b) => (
                            <button
                                key={b.id}
                                onClick={() => { setBrowserFilter(b.id); resetPage(); }}
                                className={cn(
                                    "px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer",
                                    browserFilter === b.id
                                        ? "bg-white text-slate-900 shadow-sm pointer-events-none"
                                        : "text-slate-500 hover:text-slate-700 hover:bg-slate-200/50"
                                )}
                            >
                                {b.label}
                            </button>
                        ))}
                    </div>

                    {hasFilters && (
                        <button
                            onClick={() => { setSearchTerm(''); setStatusFilter(''); setBrowserFilter(''); resetPage(); }}
                            className="flex items-center gap-1 px-3 py-2 text-xs font-bold text-slate-500 hover:text-rose-600 hover:bg-rose-50 rounded-xl transition-all h-9"
                        >
                            <X size={14} /> Clear
                        </button>
                    )}
                </div>
            </div>

            {/* ── Main Rich List Layout ── */}
            <div className="space-y-3">
                {/* Header Row for Selection */}
                {runs.length > 0 && (
                    <div className="flex items-center gap-4 px-6 py-2">
                        <Checkbox
                            checked={selectedRuns.length === runs.length && runs.length > 0}
                            onCheckedChange={toggleSelectAll}
                            className="border-slate-300 data-[state=checked]:bg-indigo-600 data-[state=checked]:border-indigo-600"
                        />
                        <span className="text-xs font-bold text-slate-400 tracking-wider uppercase">Select All on Page</span>
                        <div className="flex-1" />
                        <span className="text-xs font-bold text-slate-400 tracking-wider uppercase pr-10">Actions</span>
                    </div>
                )}

                <AnimatePresence mode="popLayout">
                    {runs.map((run, i) => (
                        <motion.div
                            key={run.id}
                            initial={{ opacity: 0, y: 10, scale: 0.99 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.95 }}
                            transition={{ delay: i * 0.02, duration: 0.2, type: "spring", stiffness: 300, damping: 25 }}
                            className={cn(
                                "group bg-white border rounded-[1.25rem] shadow-sm hover:shadow-md transition-all relative overflow-hidden flex flex-col sm:flex-row sm:items-center p-1 cursor-pointer",
                                selectedRuns.includes(run.id) ? "border-indigo-300 bg-indigo-50/10" : "border-slate-200 hover:border-slate-300"
                            )}
                            onClick={() => { /* Wait to open until link is clicked to avoid accidental navigation */ }}
                        >
                            {/* Content Grid */}
                            <div className="px-5 py-4 shrink-0 border-r border-slate-50" onClick={(e) => { e.stopPropagation(); toggleSelectRun(run.id); }}>
                                <Checkbox
                                    checked={selectedRuns.includes(run.id)}
                                    onCheckedChange={() => toggleSelectRun(run.id)}
                                    className="border-slate-300 data-[state=checked]:bg-indigo-600 data-[state=checked]:border-indigo-600 w-5 h-5 rounded-[5px]"
                                />
                            </div>

                            {/* Content Grid */}
                            <div className="flex-1 px-5 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-5 relative">
                                
                                <div className="flex flex-col flex-1 min-w-0">
                                    <div className="flex items-center gap-3 mb-1.5">
                                        <Hash className="w-3.5 h-3.5 text-slate-400" />
                                        <span className="font-mono text-sm font-bold text-slate-500">{run.id}</span>
                                        <div className="w-1 h-1 rounded-full bg-slate-200" />
                                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{formatDate(run.created_at)}</span>
                                    </div>
                                    <h3 className="text-base font-extrabold text-slate-900 truncate pr-4 group-hover:text-indigo-700 transition-colors">
                                        {run.suite_name || run.test_case_name || <span className="text-slate-400 italic">Unnamed execution</span>}
                                    </h3>
                                    {run.suite_name && run.test_case_name && (
                                        <p className="text-sm font-medium text-slate-500 mt-0.5 truncate pr-4" title={run.test_case_name}>
                                            <span className="text-slate-400 mr-2">↳</span>{run.test_case_name}
                                        </p>
                                    )}
                                </div>

                                <div className="flex flex-wrap sm:flex-nowrap items-center gap-5 sm:gap-8 shrink-0">
                                    <div className="flex flex-col items-start sm:items-end w-32">
                                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Status</span>
                                        <StatusBadge status={run.status} />
                                    </div>

                                    <div className="flex flex-col items-start sm:items-end w-24">
                                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Duration</span>
                                        <span className="font-mono text-sm font-semibold text-slate-700 flex items-center gap-1.5">
                                            <Clock className="w-3.5 h-3.5 text-slate-300" />
                                            {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(2)}s` : '—'}
                                        </span>
                                    </div>

                                    <div className="hidden lg:flex flex-col items-start sm:items-end w-36">
                                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Environment</span>
                                        <div className="flex items-center gap-1.5">
                                            {run.browser && (
                                                <span className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 text-xs font-semibold uppercase tracking-wider border border-slate-200">
                                                    <Globe className="w-3 h-3" /> {run.browser}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Actions Overlay (Hover) inside the row bounding box, or just button cluster on extreme right */}
                            <div className="px-5 py-4 border-t sm:border-t-0 sm:border-l border-slate-100 flex items-center justify-end gap-2 bg-slate-50/50 sm:bg-transparent">
                                <Link to={`/runs/${run.id}`} onClick={(e) => e.stopPropagation()}>
                                    <Button variant="outline" size="icon" className="h-10 w-10 rounded-xl hover:scale-105 transition-transform bg-white border-slate-200 text-slate-700 shadow-sm" title="View details">
                                        <Eye size={18} />
                                    </Button>
                                </Link>
                                <Button 
                                    variant="outline" 
                                    size="icon" 
                                    onClick={(e) => { e.stopPropagation(); setRunToDelete(run.id); setIsDeletingAll(false); setDeleteDialogOpen(true); }}
                                    className="h-10 w-10 rounded-xl hover:scale-105 transition-transform bg-white border-slate-200 text-rose-500 hover:text-rose-600 hover:bg-rose-50 hover:border-rose-200 shadow-sm" 
                                    title="Delete run"
                                >
                                    <Trash2 size={16} />
                                </Button>
                            </div>
                        </motion.div>
                    ))}
                </AnimatePresence>

                {/* Empty state */}
                {(!runs || runs.length === 0) && !isLoading && (
                    <div className="bg-white rounded-3xl border border-dashed border-slate-300 flex flex-col items-center justify-center py-24 text-center">
                        <div className="w-20 h-20 rounded-3xl bg-slate-50 flex items-center justify-center mb-5 border border-slate-100 shadow-inner">
                            <Activity size={32} strokeWidth={2.5} className="text-slate-300" />
                        </div>
                        <h3 className="text-xl font-bold text-slate-900 mb-2">
                            {hasFilters ? 'No runs match your criteria' : 'No execution history'}
                        </h3>
                        <p className="text-sm font-medium text-slate-500 max-w-sm">
                            {hasFilters
                                ? 'Try clearing or modifying the filters above to see more results.'
                                : 'Trigger a test suite or a module to start generating execution logs and metrics.'}
                        </p>
                        {hasFilters && (
                            <Button
                                variant="outline"
                                onClick={() => { setSearchTerm(''); setStatusFilter(''); setBrowserFilter(''); resetPage(); }}
                                className="mt-6 rounded-xl font-bold h-11 px-6 shadow-sm border-slate-200"
                            >
                                <X size={16} className="mr-2" /> Clear All Filters
                            </Button>
                        )}
                    </div>
                )}
            </div>

            {/* ── Pagination ── */}
            {totalPages > 1 && (
                <div className="flex flex-col sm:flex-row items-center justify-between gap-4 py-4 px-2">
                    <div className="flex items-center gap-3">
                        <span className="text-sm font-medium text-slate-500">
                            Showing <span className="font-extrabold text-slate-700">{((currentPage - 1) * pageSize) + 1}</span>–
                            <span className="font-extrabold text-slate-700">{Math.min(currentPage * pageSize, total)}</span> of <span className="font-extrabold text-slate-700">{total}</span> runs
                        </span>
                        
                        <div className="h-6 w-px bg-slate-200" />

                        <Select value={pageSize.toString()} onValueChange={(v) => { setPageSize(Number(v)); setCurrentPage(1); }}>
                            <SelectTrigger className="w-[120px] h-9 text-xs rounded-xl border-slate-200 font-bold bg-white shadow-sm">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent className="rounded-xl border-slate-100 shadow-xl">
                                <SelectItem value="25">25 / page</SelectItem>
                                <SelectItem value="50">50 / page</SelectItem>
                                <SelectItem value="100">100 / page</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="flex items-center gap-1.5 p-1 bg-white rounded-2xl border border-slate-200 shadow-sm">
                        <Button 
                            variant="ghost" 
                            size="icon" 
                            onClick={() => setCurrentPage(1)} 
                            disabled={currentPage === 1} 
                            className="w-10 h-10 rounded-xl text-slate-500 hover:text-slate-900 disabled:opacity-40 transition-colors"
                        >
                            <ChevronsLeft size={18} />
                        </Button>
                        <Button 
                            variant="ghost" 
                            size="icon" 
                            onClick={() => setCurrentPage(p => Math.max(1, p - 1))} 
                            disabled={currentPage === 1} 
                            className="w-10 h-10 rounded-xl text-slate-500 hover:text-slate-900 disabled:opacity-40 transition-colors"
                        >
                            <ChevronLeft size={18} />
                        </Button>
                        <span className="px-4 py-1.5 text-xs font-black text-slate-700 min-w-[5rem] text-center uppercase tracking-widest">
                            {currentPage} / {totalPages}
                        </span>
                        <Button 
                            variant="ghost" 
                            size="icon" 
                            onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} 
                            disabled={currentPage === totalPages} 
                            className="w-10 h-10 rounded-xl text-slate-500 hover:text-slate-900 disabled:opacity-40 transition-colors"
                        >
                            <ChevronRight size={18} />
                        </Button>
                        <Button 
                            variant="ghost" 
                            size="icon" 
                            onClick={() => setCurrentPage(totalPages)} 
                            disabled={currentPage === totalPages} 
                            className="w-10 h-10 rounded-xl text-slate-500 hover:text-slate-900 disabled:opacity-40 transition-colors"
                        >
                            <ChevronsRight size={18} />
                        </Button>
                    </div>
                </div>
            )}

            {/* ── Dialogs ── */}
            <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
                <AlertDialogContent className="rounded-3xl border-slate-100 shadow-2xl p-6 md:p-8 max-w-md">
                    <AlertDialogHeader className="mb-4">
                        <div className="w-12 h-12 rounded-2xl bg-rose-50 flex items-center justify-center mb-4 border border-rose-100">
                            <Trash2 className="h-6 w-6 text-rose-500" />
                        </div>
                        <AlertDialogTitle className="text-xl font-extrabold text-slate-900">
                            Delete {isDeletingAll ? 'all test runs' : selectedRuns.length > 1 ? `${selectedRuns.length} selected runs` : 'test run'}?
                        </AlertDialogTitle>
                        <AlertDialogDescription className="text-slate-500 font-medium leading-relaxed">
                            {runToDelete
                                ? "This will permanently delete this test run and all its execution logs. This action cannot be undone."
                                : isDeletingAll
                                    ? "This will permanently delete ALL test runs across the system. This cannot be undone."
                                    : `This will permanently delete ${selectedRuns.length} selected test runs. This cannot be undone.`}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter className="gap-3">
                        <AlertDialogCancel className="rounded-xl h-11 px-6 font-bold border-slate-200 text-slate-600 hover:bg-slate-50 mt-0">
                            Cancel
                        </AlertDialogCancel>
                        <AlertDialogAction 
                            onClick={confirmDelete} 
                            className="rounded-xl h-11 px-6 font-bold bg-rose-600 hover:bg-rose-700 text-white shadow-sm border border-transparent"
                        >
                            Yes, delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
