import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Loader2, Network, RefreshCw } from 'lucide-react';

import { tracesApi, type StepTraces } from '@/api/traces';
import { Eyebrow, Pill } from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';
import { formatMs, layoutWaterfall } from './waterfall';

/**
 * "Backend traces" on a result (WP16a): per step, the spans the application
 * under test recorded under the step's own span id, as a compact waterfall.
 * A step with nothing found says "No telemetry" and the server's reason — the
 * page never guesses which spans belong to a step.
 */
export function BackendTracesPanel({ runId, resultId }: { runId: number | string; resultId: number }) {
    const { data, isLoading, error, refetch, isFetching } = useQuery({
        queryKey: ['result-traces', runId, resultId],
        queryFn: () => tracesApi.forResult(runId, resultId),
        retry: false,
        staleTime: 30_000,
    });

    return (
        <section aria-labelledby={`traces-${resultId}`} className="flex flex-col gap-3 rounded-2xl border border-border bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 id={`traces-${resultId}`} className="flex items-center gap-2 text-sm font-semibold text-ink">
                    <Network className="h-4 w-4" aria-hidden="true" /> Backend traces
                    {data?.backend && <span className="text-xs font-normal text-ink-soft">from {data.backend === 'tempo' ? 'Tempo' : 'Jaeger'}</span>}
                </h3>
                <button
                    type="button"
                    onClick={() => refetch()}
                    className="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border px-3 text-xs font-medium text-ink hover:bg-background"
                >
                    <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} aria-hidden="true" /> Refresh
                </button>
            </div>
            {isLoading ? (
                <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Looking up traces…</p>
            ) : error ? (
                <p className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
                    <AlertCircle className="h-4 w-4" aria-hidden="true" />
                    {(error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Could not load backend traces.'}
                </p>
            ) : !data ? null : data.steps.length === 0 ? (
                <NoTelemetry reason={data.reason} />
            ) : (
                <>
                    {data.error && (
                        <p className="flex items-center gap-1.5 text-xs text-destructive" role="alert">
                            <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" /> {data.error}
                        </p>
                    )}
                    {data.reason && <NoTelemetry reason={data.reason} />}
                    <ol className="flex flex-col gap-3">
                        {data.steps.map((s) => <StepWaterfall key={s.step} step={s} />)}
                    </ol>
                </>
            )}
        </section>
    );
}

function NoTelemetry({ reason }: { reason: string | null }) {
    return (
        <div className="rounded-xl bg-background px-3 py-2 text-sm text-ink">
            <span className="font-medium">No telemetry.</span>{' '}
            <span className="text-ink-soft">{reason || 'Nothing was found for this result.'}</span>
        </div>
    );
}

function StepWaterfall({ step }: { step: StepTraces }) {
    const { rows, totalMs } = layoutWaterfall(step.spans);
    return (
        <li className="flex flex-col gap-2 rounded-xl border border-border p-3">
            <Eyebrow right={step.status === 'ok'
                ? <span className="flex items-center gap-2 text-xs text-ink-soft">
                    {step.spans.length} span{step.spans.length === 1 ? '' : 's'} · {formatMs(totalMs)}
                    {step.error_spans > 0 && <Pill tone="peach">{step.error_spans} error{step.error_spans === 1 ? '' : 's'}</Pill>}
                </span>
                : <Pill tone="neutral">No telemetry</Pill>}>
                Step {step.index + 1}{step.type ? ` · ${step.type}` : ''}
            </Eyebrow>
            {step.status !== 'ok' ? (
                <p className="text-xs text-ink-soft">{step.reason}</p>
            ) : (
                <ul className="flex flex-col gap-1" aria-label={`Spans recorded during step ${step.index + 1}`}>
                    {rows.map(({ span, depth, leftPct, widthPct }) => (
                        <li key={span.span_id} className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] sm:items-center sm:gap-3">
                            <div className="min-w-0 truncate" style={{ paddingLeft: `${Math.min(depth, 6) * 10}px` }}
                                title={`${span.service} · ${span.operation}`}>
                                <span className="font-medium text-ink">{span.service}</span>{' '}
                                <span className="text-ink-soft">{span.operation}</span>
                            </div>
                            <div className="flex min-w-0 items-center gap-2">
                                <div className="relative h-3 min-w-0 flex-1 rounded-full bg-background" aria-hidden="true">
                                    <div
                                        className={cn('absolute top-0 h-3 rounded-full', span.error ? 'bg-[#E8836B]' : 'bg-[#9B8AE0]')}
                                        style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                                    />
                                </div>
                                <span className="w-16 flex-none text-right tabular-nums text-ink-soft">{formatMs(span.duration_ms)}</span>
                            </div>
                            {(span.error || Object.keys(span.attributes).length > 0) && (
                                <p className="break-words text-[11px] text-ink-soft sm:col-span-2" style={{ paddingLeft: `${Math.min(depth, 6) * 10}px` }}>
                                    {span.error && <span className="font-semibold text-destructive">error{span.status_message ? `: ${span.status_message}` : ''} </span>}
                                    {Object.entries(span.attributes).map(([k, v]) => `${k}=${v}`).join(' · ')}
                                </p>
                            )}
                        </li>
                    ))}
                    {step.truncated && <li className="text-[11px] text-ink-soft">More spans exist; only the first {step.spans.length} are shown.</li>}
                </ul>
            )}
            <p className="font-mono text-[10px] text-ink-soft">trace {step.trace_id} · step span {step.span_id}</p>
        </li>
    );
}
