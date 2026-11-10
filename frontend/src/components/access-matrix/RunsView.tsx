import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { accessMatrixApi, type AccessMatrix } from '@/api/accessMatrix';
import { Button } from '@/components/ui/button';
import EvidenceSheet from './EvidenceSheet';
import MatrixFindings from './MatrixFindings';
import ResultsGrid from './ResultsGrid';
import { Pill } from '@/components/redesign/primitives';
import { VERDICT_PILL } from './model';
import { errorDetail, needsAttestation, runIsActive, summaryParts, VERDICT_LABEL } from './grid';

interface Props {
    matrix: AccessMatrix;
    runId: number | null;
    cellId: number | null;
    onSelect: (runId: number | null, cellId: number | null) => void;
}

const POLL_MS = 3000;

export default function RunsView({ matrix, runId, cellId, onSelect }: Props) {
    const qc = useQueryClient();
    const [authorized, setAuthorized] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const runs = useQuery({
        queryKey: ['am-runs', matrix.id],
        queryFn: () => accessMatrixApi.runs(matrix.id),
        refetchInterval: (q) => ((q.state.data ?? []).some(runIsActive) ? POLL_MS : false),
    });
    const activeId = runId ?? runs.data?.[0]?.id ?? null;
    const run = useQuery({
        queryKey: ['am-run', activeId],
        queryFn: () => accessMatrixApi.getRun(activeId!),
        enabled: !!activeId,
        // Poll until the collector marks the run finished, then stop.
        refetchInterval: (q) => (!q.state.data || runIsActive(q.state.data) ? POLL_MS : false),
    });
    // Findings are written when a run finishes; refetch them once it has.
    const finished = run.data && !runIsActive(run.data);

    const start = useMutation({
        mutationFn: () => accessMatrixApi.run(matrix.id, authorized),
        onSuccess: (r) => {
            setError(null);
            toast.success(`Matrix run #${r.id} started`);
            qc.invalidateQueries({ queryKey: ['am-runs', matrix.id] });
            onSelect(r.id, null);
        },
        onError: (e) => setError(errorDetail(e)),
    });

    const cell = run.data?.cells?.find(c => c.id === cellId) ?? null;
    const mutating = needsAttestation(matrix.rows);

    return (
        <div className="space-y-5">
            <section className="space-y-3 rounded-2xl border border-border p-4">
                <div className="flex flex-wrap items-center gap-3">
                    <Button type="button" onClick={() => start.mutate()} disabled={start.isPending}>
                        {start.isPending ? 'Starting…' : 'Run matrix'}
                    </Button>
                    <label className="flex items-center gap-2 text-sm text-ink">
                        <input type="checkbox" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} />
                        I am authorized to send this matrix's mutating requests to the target
                        {mutating && <span className="text-xs font-semibold text-pastel-ink-butter">(required: this matrix has mutating rows)</span>}
                    </label>
                </div>
                {error && (
                    <div role="alert" className="whitespace-pre-wrap rounded-2xl border border-border bg-background p-3 text-sm text-pastel-ink-peach">{error}</div>
                )}
            </section>

            <div className="grid gap-5 2xl:grid-cols-[240px_1fr]">
                <section aria-labelledby="am-history">
                    <h3 id="am-history" className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Run history</h3>
                    {runs.isLoading ? <p className="text-xs text-ink-soft">Loading…</p>
                        : runs.error ? <p className="text-xs text-pastel-ink-peach">{errorDetail(runs.error)}</p>
                        : !runs.data?.length ? <p className="text-xs text-ink-soft">No runs yet.</p>
                        : (
                            <ul className="flex max-h-[420px] flex-col divide-y divide-[#F0EEE8] overflow-y-auto rounded-2xl border border-border">
                                {runs.data.map(r => (
                                    <li key={r.id}>
                                        <button type="button" onClick={() => onSelect(r.id, null)}
                                            aria-pressed={r.id === activeId}
                                            className={`w-full px-3 py-2.5 text-left text-xs text-ink ${r.id === activeId ? 'bg-accent shadow-[inset_3px_0_0_#1C1B22]' : 'hover:bg-background'}`}>
                                            <div className="font-semibold text-ink">Run #{r.id} · {r.status}</div>
                                            <div className="text-ink-soft">{new Date(r.created_at).toLocaleString()}</div>
                                            <div className="mt-1 flex flex-wrap gap-1">
                                                {summaryParts(r.summary).map(p => (
                                                    <Pill key={p.verdict} tone={VERDICT_PILL[p.verdict]} className="px-2 text-[11px]">{VERDICT_LABEL[p.verdict]} {p.count}</Pill>
                                                ))}
                                            </div>
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                </section>

                <section aria-labelledby="am-results" className="min-w-0">
                    <h3 id="am-results" className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                        {activeId ? `Results — run #${activeId}` : 'Results'}
                        {run.data && runIsActive(run.data) && <span className="ml-2 font-normal normal-case tracking-normal text-pastel-ink-lavender">in progress, refreshing…</span>}
                    </h3>
                    {!activeId ? <p className="text-xs text-ink-soft">Run the matrix to see results.</p>
                        : run.isLoading ? <p className="text-xs text-ink-soft">Loading…</p>
                        : run.error ? <p className="text-xs text-pastel-ink-peach">{errorDetail(run.error)}</p>
                        : run.data && (
                            <>
                                {run.data.error && <p role="alert" className="mb-2 text-sm text-pastel-ink-peach">{run.data.error}</p>}
                                <ResultsGrid run={run.data} selectedCellId={cellId} onSelect={(c) => onSelect(run.data!.id, c.id)} />
                            </>
                        )}
                </section>
            </div>

            <MatrixFindings matrixId={matrix.id} refreshKey={finished ? activeId : null}
                onOpen={(rid, cid) => onSelect(rid, cid)} />

            {run.data && <EvidenceSheet run={run.data} cell={cell} onClose={() => onSelect(run.data!.id, null)} />}
        </div>
    );
}
