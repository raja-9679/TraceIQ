import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getRuns, deleteRun, deleteRuns } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
    Eye, Trash2, MoreHorizontal, Search, X, ChevronLeft,
    ChevronRight, ChevronsLeft, ChevronsRight,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
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
import { cn } from "@/lib/utils";
import { EmptyState, KindChip, PageHeader, Pill } from "@/components/redesign/primitives";
import { Segmented, SkeletonRows, Tile } from "@/components/redesign-local/groupB/bits";
import { statusLook } from "@/components/redesign-local/groupB/look";

const browserLabel: Record<string, string> = { chromium: 'Chrome', firefox: 'Firefox', webkit: 'Safari' };

/* ── Main Component ── */
export default function TestMatrix() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
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

    const clearFilters = () => { setSearchTerm(''); setStatusFilter(''); setBrowserFilter(''); resetPage(); };
    const askDeleteSelected = () => { setRunToDelete(null); setIsDeletingAll(false); setDeleteDialogOpen(true); };
    const askDeleteAll = () => { setRunToDelete(null); setIsDeletingAll(true); setDeleteDialogOpen(true); };
    const allOnPageSelected = selectedRuns.length === runs.length && runs.length > 0;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Test runs"
                subtitle="Every execution, newest first. Open one to see its steps, or select several to clean up."
                actions={
                    <>
                        {selectedRuns.length > 0 && (
                            <Button variant="outline" onClick={askDeleteSelected} className="text-pastel-ink-peach">
                                <Trash2 className="h-4 w-4" />
                                Delete {selectedRuns.length} selected
                            </Button>
                        )}
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button variant="outline">
                                    <MoreHorizontal className="h-4 w-4" /> More
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-48 rounded-2xl p-1.5">
                                <DropdownMenuItem
                                    onClick={askDeleteAll}
                                    className="cursor-pointer rounded-xl py-2.5 font-semibold text-pastel-ink-peach focus:bg-pastel-peach focus:text-pastel-ink-peach"
                                >
                                    <Trash2 className="mr-2 h-4 w-4" /> Delete all runs
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </>
                }
                toolbar={<>
                        <label className="relative block w-full sm:w-64">
                            <span className="sr-only">Search runs</span>
                            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-soft" aria-hidden="true" />
                            <input
                                type="text"
                                placeholder="Search suite or test case"
                                value={searchTerm}
                                onChange={(e) => { setSearchTerm(e.target.value); resetPage(); }}
                                className="h-10 w-full rounded-full border border-input bg-white pl-10 pr-4 text-sm text-ink placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-ring/30"
                            />
                        </label>
                        <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
                            <Segmented
                                label="Filter by status"
                                value={statusFilter}
                                onChange={(v) => { setStatusFilter(v); resetPage(); }}
                                options={[
                                    { id: '', label: 'All' },
                                    { id: 'passed', label: 'Passed' },
                                    { id: 'failed', label: 'Failed' },
                                    { id: 'running', label: 'Active' },
                                ]}
                            />
                            <Segmented
                                label="Filter by browser"
                                value={browserFilter}
                                onChange={(v) => { setBrowserFilter(v); resetPage(); }}
                                options={[
                                    { id: '', label: 'All browsers' },
                                    { id: 'chromium', label: 'Chrome' },
                                    { id: 'firefox', label: 'Firefox' },
                                    { id: 'webkit', label: 'Safari' },
                                ]}
                            />
                            {hasFilters && (
                                <Button variant="ghost" onClick={clearFilters}>
                                    <X className="h-4 w-4" /> Clear
                                </Button>
                            )}
                        </div>
                </>}
            />

            {isLoading && runs.length === 0 ? (
                <>
                    <div className="grid gap-3 sm:grid-cols-3">
                        {[0, 1, 2].map((i) => <div key={i} className="h-[92px] animate-pulse rounded-2xl bg-white/70" />)}
                    </div>
                    <SkeletonRows count={6} />
                </>
            ) : (
                <>
                    {/* ── Summary tiles ── */}
                    <div className="grid gap-3 sm:grid-cols-3">
                        <Tile label="Total runs" value={total} />
                        <Tile label="Pass rate on this page" value={`${passRate}%`} hint={`${passedCount} of ${validRuns.length} finished runs`} />
                        <Tile label="Average duration" value={avgDuration ? `${(avgDuration / 1000).toFixed(1)}s` : '—'} />
                    </div>

                    {/* ── Runs list ── */}
                    <div className="flex flex-col gap-3">
                        {runs.length > 0 && (
                        <div className="overflow-hidden rounded-3xl border border-border bg-white">
                            <div className="flex items-center justify-between gap-3 border-b border-[#F0EEE8] px-3 py-2 sm:px-4">
                                <label className="flex min-h-10 cursor-pointer items-center gap-3 text-[13px] text-ink">
                                    <span className="flex h-10 w-10 items-center justify-center"><Checkbox checked={allOnPageSelected} onCheckedChange={toggleSelectAll} aria-label="Select all runs on this page" /></span>
                                    Select all on this page
                                </label>
                                <span className="font-mono text-xs text-ink-soft">{runs.length} of {total}</span>
                            </div>
                        <ul className="flex flex-col divide-y divide-[#F0EEE8]">
                            {runs.map((run) => {
                                const look = statusLook(run.status);
                                const selected = selectedRuns.includes(run.id);
                                const title = run.suite_name || run.test_case_name;
                                return (
                                    <li
                                        key={run.id}
                                        onClick={() => navigate(`/runs/${run.id}`)}
                                        className={cn(
                                            'group flex min-h-[68px] cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors sm:gap-4 sm:px-4',
                                            selected ? 'bg-accent shadow-[inset_3px_0_0_#1C1B22]' : 'hover:bg-background'
                                        )}
                                    >
                                        <span
                                            className="flex h-10 w-10 flex-none items-center justify-center"
                                            onClick={(e) => { e.stopPropagation(); toggleSelectRun(run.id); }}
                                        >
                                            <Checkbox
                                                checked={selected}
                                                onCheckedChange={() => toggleSelectRun(run.id)}
                                                onClick={(e) => e.stopPropagation()}
                                                aria-label={`Select run ${run.id}`}
                                            />
                                        </span>
                                        <KindChip kind={look.kind} size="sm" />
                                        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                                            <span className="flex min-w-0 items-center gap-2">
                                                <Link
                                                    to={run.executor === 'manual' ? `/manual-runs/${run.id}` : `/runs/${run.id}`}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="truncate text-sm font-semibold text-ink hover:underline"
                                                >
                                                    {title || <span className="italic text-ink-soft">Unnamed execution</span>}
                                                </Link>
                                                {run.executor === 'manual' && <Pill tone="lavender">Manual</Pill>}
                                            </span>
                                            <span className="truncate text-xs text-ink-soft" title={run.suite_name && run.test_case_name ? run.test_case_name : undefined}>
                                                <span className="font-mono">#{run.id}</span>
                                                {' · '}{formatDate(run.created_at)}
                                                {run.suite_name && run.test_case_name && <> · {run.test_case_name}</>}
                                            </span>
                                        </div>
                                        <div className="hidden flex-none items-center gap-4 sm:flex">
                                            <Pill tone={look.tone} className={cn(run.status === 'running' && 'animate-pulse')}>{look.label}</Pill>
                                            <span className="w-16 text-right font-mono text-xs text-ink-soft">
                                                {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(2)}s` : '—'}
                                            </span>
                                            <span className="hidden w-16 text-right text-xs text-ink-soft lg:inline">
                                                {run.browser ? (browserLabel[run.browser] ?? run.browser) : ''}
                                            </span>
                                        </div>
                                        <div className="flex flex-none items-center gap-1">
                                            <Button asChild variant="ghost" size="icon" title="View details">
                                                <Link to={`/runs/${run.id}`} onClick={(e) => e.stopPropagation()} aria-label={`View run ${run.id}`}>
                                                    <Eye className="h-4 w-4" />
                                                </Link>
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={(e) => { e.stopPropagation(); setRunToDelete(run.id); setIsDeletingAll(false); setDeleteDialogOpen(true); }}
                                                className="text-ink-soft hover:bg-pastel-peach hover:text-pastel-ink-peach"
                                                title="Delete run"
                                                aria-label={`Delete run ${run.id}`}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </li>
                                );
                            })}
                        </ul>
                        </div>
                        )}

                        {/* Empty state */}
                        {(!runs || runs.length === 0) && !isLoading && (
                            <EmptyState title={hasFilters ? 'No runs match these filters' : 'No runs yet'}>
                                {hasFilters
                                    ? 'Clear or change the filters above to see more runs.'
                                    : 'Run a suite or a module and its results will show up here.'}
                            </EmptyState>
                        )}
                        {(!runs || runs.length === 0) && !isLoading && hasFilters && (
                            <Button variant="outline" onClick={clearFilters} className="self-center">
                                <X className="h-4 w-4" /> Clear all filters
                            </Button>
                        )}
                    </div>

                    {/* ── Pagination ── */}
                    {totalPages > 1 && (
                        <nav aria-label="Pagination" className="flex flex-col items-center justify-between gap-4 sm:flex-row">
                            <div className="flex items-center gap-3">
                                <span className="text-sm text-ink-soft">
                                    Showing <span className="font-semibold text-ink">{((currentPage - 1) * pageSize) + 1}</span>–
                                    <span className="font-semibold text-ink">{Math.min(currentPage * pageSize, total)}</span> of <span className="font-semibold text-ink">{total}</span> runs
                                </span>
                                <Select value={pageSize.toString()} onValueChange={(v) => { setPageSize(Number(v)); setCurrentPage(1); }}>
                                    <SelectTrigger className="h-10 w-[120px] rounded-full bg-white text-xs" aria-label="Runs per page">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent className="rounded-2xl">
                                        <SelectItem value="25">25 / page</SelectItem>
                                        <SelectItem value="50">50 / page</SelectItem>
                                        <SelectItem value="100">100 / page</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="flex items-center gap-1 rounded-full border border-border bg-white p-1">
                                <Button variant="ghost" size="icon" onClick={() => setCurrentPage(1)} disabled={currentPage === 1} aria-label="First page">
                                    <ChevronsLeft className="h-4 w-4" />
                                </Button>
                                <Button variant="ghost" size="icon" onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1} aria-label="Previous page">
                                    <ChevronLeft className="h-4 w-4" />
                                </Button>
                                <span className="min-w-[5rem] px-3 text-center text-[13px] font-semibold text-ink tabular-nums">
                                    {currentPage} / {totalPages}
                                </span>
                                <Button variant="ghost" size="icon" onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages} aria-label="Next page">
                                    <ChevronRight className="h-4 w-4" />
                                </Button>
                                <Button variant="ghost" size="icon" onClick={() => setCurrentPage(totalPages)} disabled={currentPage === totalPages} aria-label="Last page">
                                    <ChevronsRight className="h-4 w-4" />
                                </Button>
                            </div>
                        </nav>
                    )}
                </>
            )}

            {/* ── Dialogs ── */}
            <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
                <AlertDialogContent className="max-w-md rounded-3xl p-6 md:p-8">
                    <AlertDialogHeader>
                        <span className="mb-2 flex h-10 w-10 items-center justify-center rounded-[12px] bg-pastel-peach text-pastel-ink-peach" aria-hidden="true">
                            <Trash2 className="h-5 w-5" />
                        </span>
                        <AlertDialogTitle className="text-xl font-bold text-ink">
                            Delete {isDeletingAll ? 'all test runs' : selectedRuns.length > 1 ? `${selectedRuns.length} selected runs` : 'test run'}?
                        </AlertDialogTitle>
                        <AlertDialogDescription className="leading-relaxed text-ink-soft">
                            {runToDelete
                                ? "This will permanently delete this test run and all its execution logs. This action cannot be undone."
                                : isDeletingAll
                                    ? "This will permanently delete ALL test runs across the system. This cannot be undone."
                                    : `This will permanently delete ${selectedRuns.length} selected test runs. This cannot be undone.`}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter className="gap-2">
                        <AlertDialogCancel className="mt-0 rounded-full">Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={confirmDelete} className="rounded-full bg-destructive text-destructive-foreground hover:bg-destructive/90">
                            Yes, delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
