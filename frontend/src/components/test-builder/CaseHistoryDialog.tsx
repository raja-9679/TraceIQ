import React from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { History, X, RotateCcw, Loader2, User, Bot, ChevronDown, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import {
    getCaseRevisions, getCaseRevision, restoreCaseRevision, CaseRevision,
} from '@/lib/api';

const SOURCE_LABELS: Record<string, { label: string; tone: Tone }> = {
    create: { label: 'Created', tone: 'mint' },
    update: { label: 'Edited', tone: 'neutral' },
    heal: { label: 'Selector heal', tone: 'pink' },
    proposal: { label: 'AI proposal', tone: 'lavender' },
    restore: { label: 'Restored', tone: 'butter' },
};

function SourceBadge({ source }: { source: string }) {
    const meta = SOURCE_LABELS[source] || { label: source, tone: 'neutral' as Tone };
    return <Pill tone={meta.tone} className="shrink-0">{meta.label}</Pill>;
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
        <div className={`border rounded-2xl bg-white ${isCurrent ? 'border-ink-muted' : 'border-border'}`}>
            <div className="flex items-center gap-3 px-4 py-3">
                <button
                    type="button"
                    onClick={() => setExpanded(!expanded)}
                    aria-expanded={expanded}
                    aria-label={expanded ? `Hide steps of revision ${rev.revision_number}` : `Show steps of revision ${rev.revision_number}`}
                    className="-ml-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-ink-soft transition-colors hover:bg-background hover:text-ink"
                    title={expanded ? 'Collapse' : 'Show steps at this revision'}
                >
                    {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                <span className="font-mono text-xs text-ink-soft shrink-0">#{rev.revision_number}</span>
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
                    <Pill tone="neutral" className="shrink-0">Current</Pill>
                ) : (
                    <Button
                        variant="outline"
                        size="sm"
                        className="shrink-0"
                        onClick={() => {
                            if (window.confirm(`Restore the case to revision #${rev.revision_number}? The current state stays in history.`)) {
                                restoreMutation.mutate();
                            }
                        }}
                        disabled={restoreMutation.isPending}
                    >
                        {restoreMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                        Restore
                    </Button>
                )}
            </div>
            {expanded && (
                <div className="border-t border-border px-4 py-3 bg-background rounded-b-2xl">
                    {detailLoading ? (
                        <p className="text-xs text-ink-soft">Loading snapshot…</p>
                    ) : steps.length === 0 ? (
                        <p className="text-xs text-ink-soft">No steps in this revision.</p>
                    ) : (
                        <ol className="space-y-1">
                            {steps.map((s, i) => (
                                <li key={s.id || i} className="text-xs font-mono text-ink-soft truncate">
                                    <span className="text-ink-muted mr-2">{i + 1}.</span>
                                    <span className="font-semibold text-ink">{s.type}</span>
                                    {s.selector && <span className="text-ink"> {s.selector}</span>}
                                    {s.value && <span className="text-pastel-ink-mint"> "{s.value}"</span>}
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
            <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(true)}
                title="Revision history — every save is snapshotted; restore any earlier version"
            >
                <History className="h-4 w-4" /> History
            </Button>
            {open && (
                <div
                    className="fixed inset-0 bg-ink/40 backdrop-blur-sm flex items-center justify-center z-50 p-4"
                    onClick={() => setOpen(false)}
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="case-history-title"
                        className="bg-white rounded-3xl p-6 max-w-2xl w-full max-h-[80vh] flex flex-col gap-4 shadow-[0_12px_40px_rgba(28,27,34,0.12)] border border-border"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-3">
                                <div className="w-9 h-9 shrink-0 rounded-[11px] bg-pastel-lavender text-pastel-ink-lavender flex items-center justify-center" aria-hidden="true">
                                    <History size={17} strokeWidth={2.2} />
                                </div>
                                <div>
                                    <h3 id="case-history-title" className="text-[22px] font-bold tracking-[-0.02em] text-ink">Revision history</h3>
                                    <p className="text-sm text-ink-soft">Every save is kept, including AI edits. Restoring never deletes history.</p>
                                </div>
                            </div>
                            <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close revision history" className="shrink-0 text-ink-soft hover:text-ink">
                                <X className="h-5 w-5" />
                            </Button>
                        </div>
                        <div className="overflow-y-auto space-y-2 pr-1">
                            {isLoading ? (
                                <p className="text-sm text-ink-soft py-8 text-center">Loading history…</p>
                            ) : !revisions || revisions.length === 0 ? (
                                <p className="text-sm text-ink-soft py-8 text-center">
                                    No revisions yet. History starts recording from this case's next save.
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
