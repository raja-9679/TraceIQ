import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { accessMatrixApi, type MatrixCellResult, type MatrixRun } from '@/api/accessMatrix';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { columnLabel, expectedText, VERDICT_TONE } from './model';
import { errorDetail, failureKind, rowTargetText, VERDICT_LABEL } from './grid';

interface Props {
    run: MatrixRun;
    cell: MatrixCellResult | null;
    onClose: () => void;
}

const dt = 'text-[11px] font-semibold uppercase tracking-wide text-slate-500';

/** Evidence for one cell. Everything here was redacted on capture and again
 * on ingestion (redactAccessObservation / redact_access_observation). */
export default function EvidenceSheet({ run, cell, onClose }: Props) {
    const obs = cell?.observed ?? null;
    const shot = useQuery({
        queryKey: ['am-shot', run.id, cell?.id],
        queryFn: () => accessMatrixApi.screenshot(run.id, cell!.id),
        enabled: !!cell && !!obs?.screenshot,
        staleTime: 5 * 60 * 1000,   // presigned URLs outlive this
    });
    const row = run.snapshot?.rows.find(r => r.key === cell?.row_key);
    const who = cell ? columnLabel(cell.persona_key, run.snapshot?.persona_names || {}) : '';
    const testRunId = cell?.test_run_id ?? obs?.run_id ?? null;
    const headers = Object.entries(obs?.headers ?? {});

    return (
        <Sheet open={!!cell} onOpenChange={(open) => { if (!open) onClose(); }}>
            <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
                {cell && (
                    <>
                        <SheetHeader>
                            <SheetTitle>{row?.label || cell.row_key} as {who}</SheetTitle>
                            <SheetDescription className="font-mono text-xs break-all">{row ? rowTargetText(row) : ''}</SheetDescription>
                        </SheetHeader>
                        <div className="mt-4 space-y-4 text-sm">
                            <div className={`rounded-lg border p-3 ${VERDICT_TONE[cell.verdict]}`}>
                                <div className="font-bold">
                                    {VERDICT_LABEL[cell.verdict]}{failureKind(cell.expect, cell.verdict) ? ` — ${failureKind(cell.expect, cell.verdict)}` : ''}
                                </div>
                                <div className="text-xs">Expected {expectedText(cell)}</div>
                                {cell.reason && <div className="mt-1 text-xs">Reason: {cell.reason}</div>}
                            </div>

                            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
                                {obs?.status !== undefined && <><dt className={dt}>Status</dt><dd>{obs.status}</dd></>}
                                {obs?.final_url && <><dt className={dt}>Final URL</dt><dd className="break-all font-mono text-xs">{obs.final_url}</dd></>}
                                {obs?.url && obs.url !== obs.final_url && <><dt className={dt}>URL</dt><dd className="break-all font-mono text-xs">{obs.url}</dd></>}
                                {obs?.element_present !== undefined && obs?.element_present !== null && (
                                    <><dt className={dt}>Element</dt><dd>{obs.element_present ? 'present' : 'absent'}</dd></>
                                )}
                                {obs?.swapped_id && <><dt className={dt}>Swapped id</dt><dd className="font-mono text-xs">{obs.swapped_id}</dd></>}
                                {obs?.owner_status !== undefined && <><dt className={dt}>Owner status</dt><dd>{obs.owner_status}</dd></>}
                                {obs?.error_kind && <><dt className={dt}>Error</dt><dd>{obs.error_kind}{obs.error ? `: ${obs.error}` : ''}</dd></>}
                                {obs?.run_status && <><dt className={dt}>Test run</dt><dd>{obs.run_status}</dd></>}
                            </dl>

                            {testRunId && (
                                <Link to={`/runs/${testRunId}`} className="inline-block text-xs font-medium text-indigo-600 hover:underline">
                                    Open test run #{testRunId}
                                </Link>
                            )}

                            {headers.length > 0 && (
                                <section>
                                    <h4 className={dt}>Response headers (redacted)</h4>
                                    <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-slate-50 p-2 text-[11px]">
                                        {headers.map(([k, v]) => `${k}: ${v}`).join('\n')}
                                    </pre>
                                </section>
                            )}
                            {obs?.body_excerpt && (
                                <section>
                                    <h4 className={dt}>Body excerpt (redacted)</h4>
                                    <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-50 p-2 text-[11px]">{obs.body_excerpt}</pre>
                                </section>
                            )}
                            {obs?.screenshot && (
                                <section>
                                    <h4 className={dt}>Screenshot</h4>
                                    {shot.isLoading ? <p className="text-xs text-slate-400">Loading screenshot…</p>
                                        : shot.error ? <p className="text-xs text-rose-600">{errorDetail(shot.error)}</p>
                                        : shot.data && (
                                            <a href={shot.data.url} target="_blank" rel="noreferrer">
                                                <img src={shot.data.url} alt={`Page as ${who}`} className="mt-1 rounded-md border border-slate-200" />
                                            </a>
                                        )}
                                </section>
                            )}
                            {!obs && <p className="text-xs text-slate-500">No observation recorded yet.</p>}
                        </div>
                    </>
                )}
            </SheetContent>
        </Sheet>
    );
}
