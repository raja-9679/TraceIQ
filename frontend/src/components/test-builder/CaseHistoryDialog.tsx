import React from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { History, X, RotateCcw, Loader2, User, Bot, ChevronDown, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import {
    getCaseRevisions, getCaseRevision, restoreCaseRevision, CaseRevision,
} from '@/lib/api';

const SOURCE_LABELS: Record<string, { label: string; cls: string }> = {
    create: { label: 'Created', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    update: { label: 'Edited', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
    heal: { label: 'Selector heal', cls: 'bg-violet-50 text-violet-700 border-violet-200' },
    proposal: { label: 'AI proposal', cls: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
    restore: { label: 'Restored', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
};

function SourceBadge({ source }: { source: string }) {
    const meta = SOURCE_LABELS[source] || { label: source, cls: 'bg-slate-100 text-slate-600 border-slate-200' };
    return (
        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide border ${meta.cls}`}>
            {meta.label}
        </span>
    );
}

function RevisionRow({ rev, caseId, isCurrent, onRestored }: {
    rev: CaseRevision;
    caseId: number;
    isCurrent: boolean;
    onRestored: () => void;
}) {
    const [expanded, setExpanded] = React.useState(false);

    // Snapshot is fetched lazily on expand — the list endpoint ships metadata only.
    const { data: detail, isLoading: detailLoading } = useQuery({
        queryKey: ['case-revision', caseId, rev.revision_number],
        queryFn: () => getCaseRevision(caseId, rev.revision_number),
        enabled: expanded,
        staleTime: Infinity, // revisions are immutable
    });

    const restoreMutation = useMutation({
        mutationFn: () => restoreCaseRevision(caseId, rev.revision_number),
        onSuccess: () => {
            toast.success(`Restored revision #${rev.revision_number}`);
            onRestored();
        },
        onError: (err: any) => {
            toast.error('Restore failed', { description: err.response?.data?.detail });
        },
    });

    const steps: any[] = detail?.snapshot?.steps || [];

    return (
        <div className={`border rounded-2xl ${isCurrent ? 'border-ink/20 bg-background' : 'border-border bg-white'}`}>
            <div className="flex items-center gap-3 px-4 py-3">
                <button
                    type="button"
                    onClick={() => setExpanded(!expanded)}
                    aria-expanded={expanded}
                    className="inline-flex h-8 shrink-0 items-center gap-1 rounded-full px-2 text-xs font-medium text-ink-soft transition-colors hover:bg-secondary hover:text-ink"
                    title={expanded ? 'Collapse' : 'Show steps at this revision'}
                >
                    {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                    Steps
                </button>
                <span className="font-mono text-xs font-bold text-ink-soft shrink-0">#{rev.revision_number}</span>
                <SourceBadge source={rev.change_source} />
                <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-ink truncate">{rev.name || '(unnamed)'}</p>
                    <p className="text-xs text-ink-soft flex items-center gap-1.5">
                        {rev.changed_by_agent_id ? <Bot size={11} /> : <User size={11} />}
                        {rev.changed_by_agent_id || 'human'} · {rev.step_count ?? '?'} steps ·{' '}
                        {new Date(rev.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </p>
                </div>
                {isCurrent ? (
                    <span className="rounded-full bg-pastel-lavender px-2 py-0.5 text-[10px] font-bold uppercase text-pastel-ink-lavender shrink-0">Current</span>
                ) : (
                    <button
                        type="button"
                        onClick={() => {
                            if (window.confirm(`Restore the case to revision #${rev.revision_number}? The current state stays in history.`)) {
                                restoreMutation.mutate();
                            }
                        }}
                        disabled={restoreMutation.isPending}
                        className="shrink-0 flex items-center gap-1.5 px-3 h-8 rounded-full text-xs font-semibold text-ink border border-border bg-white hover:bg-background transition-colors disabled:opacity-50"
                    >
                        {restoreMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                        Restore
                    </button>
                )}
            </div>
            {expanded && (
                <div className="border-t border-[#F0EEE8] px-4 py-3 bg-background/60 rounded-b-2xl">
                    {detailLoading ? (
                        <p className="text-xs text-slate-400">Loading snapshot…</p>
                    ) : steps.length === 0 ? (
                        <p className="text-xs text-slate-400">No steps in this revision.</p>
                    ) : (
                        <ol className="space-y-1">
                            {steps.map((s, i) => (
                                <li key={s.id || i} className="text-xs font-mono text-slate-600 truncate">
                                    <span className="text-slate-300 mr-2">{i + 1}.</span>
                                    <span className="font-bold text-slate-700">{s.type}</span>
                                    {s.selector && <span className="text-indigo-600"> {s.selector}</span>}
                                    {s.value && <span className="text-emerald-700"> "{s.value}"</span>}
                                </li>
                            ))}
                        </ol>
                    )}
                </div>
            )}
        </div>
    );
}

export function CaseHistoryDialog({ caseId, onRestored }: {
    caseId: number;
    /** Called after a successful restore — reload the editor's state. */
    onRestored: () => void;
}) {
    const [open, setOpen] = React.useState(false);

    const { data: revisions, isLoading } = useQuery({
        queryKey: ['case-revisions', caseId],
        queryFn: () => getCaseRevisions(caseId),
        enabled: open,
    });

    const latest = revisions?.[0]?.revision_number;

    return (
        <>
            <button
                type="button"
                onClick={() => setOpen(true)}
                className="inline-flex h-10 items-center gap-2 rounded-full border border-input bg-white px-5 text-sm font-medium text-ink transition-colors hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                title="Revision history — every save is snapshotted; restore any earlier version"
            >
                <History size={16} aria-hidden="true" /> History
            </button>
            {open && (
                <div
                    className="fixed inset-0 bg-ink/40 backdrop-blur-sm flex items-center justify-center z-50 p-4"
                    onClick={() => setOpen(false)}
                >
                    <div
                        role="dialog" aria-modal="true" aria-labelledby={`case-history-title-${caseId}`}
                        className="bg-white rounded-3xl p-6 max-w-2xl w-full max-h-[80vh] flex flex-col shadow-[0_12px_40px_rgba(28,27,34,0.12)] border border-border"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between mb-4">
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-full bg-pastel-lavender flex items-center justify-center" aria-hidden="true">
                                    <History size={20} className="text-pastel-ink-lavender" />
                                </div>
                                <div>
                                    <h3 id={`case-history-title-${caseId}`} className="text-lg font-semibold text-ink">Revision History</h3>
                                    <p className="text-xs text-ink-soft">Every save is snapshotted — including AI edits. Restoring never deletes history.</p>
                                </div>
                            </div>
                            <button type="button" onClick={() => setOpen(false)} className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-white px-3 text-sm font-medium text-ink-soft transition-colors hover:bg-background hover:text-ink">
                                <X size={16} aria-hidden="true" /> Close
                            </button>
                        </div>
                        <div className="overflow-y-auto space-y-2 pr-1">
                            {isLoading ? (
                                <p className="text-sm text-slate-400 py-8 text-center">Loading history…</p>
                            ) : !revisions || revisions.length === 0 ? (
                                <p className="text-sm text-slate-400 py-8 text-center">
                                    No revisions yet — history starts recording from this case's next save.
                                </p>
                            ) : (
                                revisions.map((rev) => (
                                    <RevisionRow
                                        key={rev.id}
                                        rev={rev}
                                        caseId={caseId}
                                        isCurrent={rev.revision_number === latest}
                                        onRestored={() => { setOpen(false); onRestored(); }}
                                    />
                                ))
                            )}
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}
