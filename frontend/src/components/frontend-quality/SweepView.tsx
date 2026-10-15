import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ImageOff } from 'lucide-react';
import { getArtifactUrl } from '@/lib/api';
import { frontendQualityApi, SweepStep, SweepVariant } from '@/api/frontendQuality';
import { alignSteps } from './matrixHelpers';

function Shot({ step, label }: { step: SweepStep | null; label: string }) {
    const key = step?.screenshot || null;
    const { data: url } = useQuery({
        queryKey: ['artifact-url', key],
        queryFn: () => getArtifactUrl(key!),
        enabled: !!key,
        staleTime: 5 * 60 * 1000,
    });
    if (!step) return <div className="h-40 rounded-lg border border-dashed border-slate-200 bg-slate-50" aria-label={`${label}: step not run`} />;
    const tone = step.status === 'failed' ? 'border-rose-300' : step.status === 'skipped' ? 'border-slate-200 opacity-60' : 'border-slate-200';
    return (
        <figure className={`rounded-lg border ${tone} bg-white overflow-hidden`}>
            {url ? (
                <a href={url} target="_blank" rel="noreferrer" aria-label={`Open ${label} screenshot full size`}>
                    <img src={url} alt={`${label}, step ${step.index + 1} (${step.type})`} className="w-full max-h-72 object-contain object-top bg-slate-50" />
                </a>
            ) : (
                <div className="h-40 flex items-center justify-center text-xs text-slate-400 gap-1">
                    <ImageOff className="w-4 h-4" /> {key ? 'loading…' : 'no screenshot'}
                </div>
            )}
            <figcaption className="px-2 py-1 text-[11px] text-slate-500 flex justify-between gap-2">
                <span className="truncate">{step.type}</span>
                <span className={step.status === 'failed' ? 'text-rose-600 font-semibold' : ''}>{step.status}</span>
            </figcaption>
            {step.error && <p className="px-2 pb-2 text-[11px] text-rose-600 line-clamp-3">{step.error}</p>}
        </figure>
    );
}

/** Runs of one sweep (viewport × locale × scheme) with screenshots per step, side by side. */
export default function SweepView({ runId }: { runId: number }) {
    const { data, isLoading, error } = useQuery({
        queryKey: ['run-sweep', runId],
        queryFn: () => frontendQualityApi.sweep(runId),
        refetchInterval: (q) => (q.state.data?.variants || []).some(v => v.status === 'pending' || v.status === 'running') ? 5000 : false,
    });
    const cases = useMemo(() => {
        const byCase = new Map<string, SweepVariant[]>();
        for (const v of data?.variants || []) {
            const k = `${v.test_case_id ?? 'suite'}|${v.browser}|${v.device ?? ''}`;
            byCase.set(k, [...(byCase.get(k) || []), v]);
        }
        return [...byCase.entries()];
    }, [data]);
    const [selected, setSelected] = useState(0);

    if (isLoading) return <p className="text-sm text-slate-500">Loading sweep…</p>;
    if (error) return <p className="text-sm text-rose-600">Could not load the sweep.</p>;
    if (!cases.length) return <p className="text-sm text-slate-500">This run is not part of a sweep.</p>;
    const [, variants] = cases[Math.min(selected, cases.length - 1)];
    const testNames = [...new Set(variants.flatMap(v => v.tests.map(t => t.test_name)))];

    return (
        <div className="space-y-4">
            {cases.length > 1 && (
                <div className="flex items-center gap-2">
                    <label htmlFor="sweep-case" className="text-sm font-medium text-slate-600">Case</label>
                    <select id="sweep-case" className="h-9 rounded-lg border border-slate-200 px-2 text-sm"
                        value={selected} onChange={(e) => setSelected(Number(e.target.value))}>
                        {cases.map(([k, vs], i) => (
                            <option key={k} value={i}>{vs[0].test_case_name || 'Suite run'} · {vs[0].browser}{vs[0].device ? ` · ${vs[0].device}` : ''}</option>
                        ))}
                    </select>
                </div>
            )}
            {testNames.map(name => {
                const columns = variants.map(v => v.tests.find(t => t.test_name === name)?.steps || []);
                const rows = alignSteps(columns);
                return (
                    <section key={name} className="bg-white border border-slate-200 rounded-xl p-4 overflow-x-auto">
                        <h3 className="font-semibold text-slate-800 mb-3">{name}</h3>
                        <div className="grid gap-3" style={{ gridTemplateColumns: `4rem repeat(${variants.length}, minmax(12rem, 1fr))` }}>
                            <div />
                            {variants.map(v => (
                                <div key={v.run_id} className="text-xs font-semibold text-slate-700" dir="auto">
                                    <Link to={`/runs/${v.run_id}`} className="hover:underline">{v.label}</Link>
                                    <span className={`ml-2 uppercase text-[10px] ${v.status === 'passed' ? 'text-emerald-600' : v.status === 'failed' ? 'text-rose-600' : 'text-slate-400'}`}>{v.status}</span>
                                    {v.rtl && <span className="ml-1 text-[10px] text-indigo-600">RTL</span>}
                                </div>
                            ))}
                            {rows.map(row => (
                                <div key={row.index} className="contents">
                                    <div className="text-xs text-slate-400 pt-2">Step {row.index + 1}</div>
                                    {row.cells.map((cell, i) => <Shot key={variants[i].run_id} step={cell} label={variants[i].label} />)}
                                </div>
                            ))}
                        </div>
                        {rows.length === 0 && <p className="text-sm text-slate-500">No step results yet.</p>}
                    </section>
                );
            })}
            {testNames.length === 0 && <p className="text-sm text-slate-500">Waiting for results…</p>}
        </div>
    );
}
