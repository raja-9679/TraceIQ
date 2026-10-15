import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { accessMatrixApi, type AccessMatrix } from '@/api/accessMatrix';
import { Button } from '@/components/ui/button';
import EvidenceSheet from './EvidenceSheet';
import MatrixFindings from './MatrixFindings';
import ResultsGrid from './ResultsGrid';
import { VERDICT_TONE } from './model';
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
            <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                    <Button type="button" onClick={() => start.mutate()} disabled={start.isPending}>
                        {start.isPending ? 'Starting…' : 'Run matrix'}
                    </Button>
                    <label className="flex items-center gap-2 text-sm text-slate-700">
                        <input type="checkbox" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} />
                        I am authorized to send this matrix's mutating requests to the target
                        {mutating && <span className="text-xs font-semibold text-amber-700">(required: this matrix has mutating rows)</span>}
                    </label>
                </div>
                {error && (
                    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800 whitespace-pre-wrap">{error}</div>
                )}
            </section>

            <div className="grid gap-5 lg:grid-cols-[240px_1fr]">
                <section aria-labelledby="am-history">
                    <h3 id="am-history" className="mb-2 text-sm font-bold text-slate-800">Run history</h3>
                    {runs.isLoading ? <p className="text-xs text-slate-400">Loading…</p>
                        : runs.error ? <p className="text-xs text-rose-600">{errorDetail(runs.error)}</p>
                        : !runs.data?.length ? <p className="text-xs text-slate-500">No runs yet.</p>
                        : (
                            <ul className="space-y-1.5">
                                {runs.data.map(r => (
                                    <li key={r.id}>
                                        <button type="button" onClick={() => onSelect(r.id, null)}
                                            className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${r.id === activeId ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                                            <div className="font-semibold text-slate-800">Run #{r.id} · {r.status}</div>
                                            <div className="text-slate-500">{new Date(r.created_at).toLocaleString()}</div>
                                            <div className="mt-1 flex flex-wrap gap-1">
                                                {summaryParts(r.summary).map(p => (
                                                    <span key={p.verdict} className={`rounded border px-1 ${VERDICT_TONE[p.verdict]}`}>{VERDICT_LABEL[p.verdict]} {p.count}</span>
                                                ))}
                                            </div>
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                </section>

                <section aria-labelledby="am-results" className="min-w-0">
                    <h3 id="am-results" className="mb-2 text-sm font-bold text-slate-800">
                        {activeId ? `Results — run #${activeId}` : 'Results'}
                        {run.data && runIsActive(run.data) && <span className="ml-2 text-xs font-normal text-sky-700">in progress, refreshing…</span>}
                    </h3>
                    {!activeId ? <p className="text-xs text-slate-500">Run the matrix to see results.</p>
                        : run.isLoading ? <p className="text-xs text-slate-400">Loading…</p>
                        : run.error ? <p className="text-xs text-rose-600">{errorDetail(run.error)}</p>
                        : run.data && (
                            <>
                                {run.data.error && <p role="alert" className="mb-2 text-sm text-rose-700">{run.data.error}</p>}
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
