import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle, CircleSlash, Code2, Globe, ListOrdered, Terminal, Wrench, XCircle } from 'lucide-react';
import { getArtifactUrl } from '@/lib/api';
import { requestOutcome, stepAnchorId, type StepEvidence } from './evidenceHelpers';

// Per-step evidence for one result (result_payload.step_evidence, sent by
// workers since failure investigation v2). Each step is an anchor that the
// analysis panel's evidence references scroll to and highlight. Results from
// older workers have no step evidence — then this renders nothing.

const STATUS_STYLE: Record<StepEvidence['status'], { icon: typeof CheckCircle; cls: string; label: string }> = {
    passed: { icon: CheckCircle, cls: 'text-emerald-600', label: 'passed' },
    failed: { icon: XCircle, cls: 'text-rose-600', label: 'failed' },
    skipped: { icon: CircleSlash, cls: 'text-slate-400', label: 'not run' },
    healed: { icon: Wrench, cls: 'text-amber-600', label: 'healed' },
};

function ScreenshotThumb({ path, label }: { path: string; label: string }) {
    const { data: url } = useQuery({
        queryKey: ['screenshot', path],
        queryFn: () => getArtifactUrl(path),
        staleTime: 5 * 60_000,
    });
    if (!url) return <div className="w-40 h-24 rounded-md bg-slate-100 animate-pulse" aria-label="Loading screenshot" />;
    return (
        <a href={url} target="_blank" rel="noreferrer" className="block w-40 shrink-0" title="Open screenshot full size">
            <img src={url} alt={label} className="w-40 h-24 object-cover object-top rounded-md border border-slate-200" />
            <span className="text-[10px] text-slate-500">Open screenshot</span>
        </a>
    );
}

export function StepEvidenceTimeline({
    resultId, steps, highlightedStep, highlightedRef,
}: {
    resultId: number | string;
    steps: StepEvidence[];
    highlightedStep?: number | null;
    highlightedRef?: string | null;
}) {
    if (!steps.length) return null;
    return (
        <div className="bg-white border border-slate-200 rounded-xl p-4">
            <p className="text-[10px] font-bold text-slate-600 mb-3 uppercase tracking-wider flex items-center gap-1.5">
                <ListOrdered size={14} /> Step evidence ({steps.length})
            </p>
            <ol className="space-y-1.5">
                {steps.map((s) => {
                    const st = STATUS_STYLE[s.status] ?? STATUS_STYLE.passed;
                    const Icon = st.icon;
                    const highlighted = highlightedStep === s.index;
                    const hasDetail = !!(s.error || s.console?.length || s.requests?.length || s.screenshot || s.dom);
                    const mark = (id: string) => (highlightedRef === id ? 'ring-2 ring-indigo-400 rounded' : '');
                    return (
                        <li
                            key={s.id}
                            id={stepAnchorId(resultId, s.index)}
                            className={`rounded-lg border px-3 py-2 text-xs transition-colors scroll-mt-24 ${highlighted
                                ? 'border-indigo-400 bg-indigo-50/70 ring-2 ring-indigo-200'
                                : s.status === 'failed' ? 'border-rose-200 bg-rose-50/40' : 'border-slate-100'}`}
                        >
                            <div className="flex items-center gap-2 flex-wrap">
                                <Icon size={14} className={st.cls} aria-hidden />
                                <span className="font-semibold text-slate-700 tabular-nums">Step {s.index + 1}</span>
                                <span className="font-mono text-slate-600">{s.type}</span>
                                {s.selector && (
                                    <span className="font-mono text-slate-500 truncate max-w-[22rem]" title={s.selector}>{s.selector}</span>
                                )}
                                <span className={`ml-auto font-semibold ${st.cls}`}>{st.label}</span>
                                {typeof s.duration_ms === 'number' && (
                                    <span className="text-slate-400 tabular-nums">{s.duration_ms} ms</span>
                                )}
                            </div>
                            {hasDetail && (
                                <div className="mt-2 space-y-1.5 pl-5">
                                    {s.error && (
                                        <pre className="text-[11px] text-rose-700 whitespace-pre-wrap font-mono max-h-24 overflow-y-auto">{s.error}</pre>
                                    )}
                                    {s.console?.map((c) => (
                                        <div key={c.id} className={`flex gap-1.5 items-start ${mark(c.id)}`}>
                                            {c.type === 'warning'
                                                ? <AlertTriangle size={12} className="text-amber-500 mt-0.5 shrink-0" aria-label="Console warning" />
                                                : <Terminal size={12} className="text-rose-500 mt-0.5 shrink-0" aria-label="Console error" />}
                                            <span className="font-mono text-[11px] text-slate-700 break-all">
                                                {c.type === 'pageerror' && <span className="font-semibold text-rose-700">Uncaught: </span>}
                                                {c.text}
                                            </span>
                                        </div>
                                    ))}
                                    {s.requests?.map((r) => (
                                        <div key={r.id} className={`flex gap-1.5 items-start ${mark(r.id)}`}>
                                            <Globe size={12} className="text-sky-600 mt-0.5 shrink-0" aria-label="Failed request" />
                                            <span className="font-mono text-[11px] text-slate-700 break-all">
                                                <span className="font-semibold">{r.method}</span> {r.url}{' '}
                                                <span className="font-semibold text-rose-700">→ {requestOutcome(r)}</span>
                                            </span>
                                        </div>
                                    ))}
                                    {s.screenshot && (
                                        <div className={mark(`${s.id}.shot`)}>
                                            <ScreenshotThumb path={s.screenshot} label={`Screenshot at step ${s.index + 1}`} />
                                        </div>
                                    )}
                                    {s.dom && (
                                        <details className={mark(`${s.id}.dom`)} open={highlightedRef === `${s.id}.dom`}>
                                            <summary className="cursor-pointer text-[11px] text-slate-600 flex items-center gap-1">
                                                <Code2 size={12} aria-hidden />
                                                DOM {s.dom_scope === 'target' ? 'around the target' : 'of the page (target not found)'}
                                            </summary>
                                            <pre className="mt-1 text-[10px] text-slate-600 whitespace-pre-wrap font-mono max-h-40 overflow-y-auto bg-slate-50 rounded p-2">{s.dom}</pre>
                                        </details>
                                    )}
                                </div>
                            )}
                        </li>
                    );
                })}
            </ol>
        </div>
    );
}
