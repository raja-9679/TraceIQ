import { useQuery } from '@tanstack/react-query';
import { estatesApi } from '@/api/estates';
import { errDetail } from '@/lib/errors';
import Sparkline from './Sparkline';
import { scoreTone, statusTone } from './helpers';

interface Props {
    domainId: number;
}

/** Every check with its explanation, and the score as 100 minus each deduction. */
export default function DomainDrilldown({ domainId }: Props) {
    const q = useQuery({ queryKey: ['estate-domain', domainId], queryFn: () => estatesApi.domain(domainId) });
    if (q.isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
    if (q.error) return <p role="alert" className="text-sm text-rose-700">{errDetail(q.error)}</p>;
    const d = q.data!;
    const latest = d.latest;
    return (
        <section aria-label={`Checks for ${d.domain.hostname}`} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
            <header className="flex flex-wrap items-center gap-3">
                <h3 className="text-base font-semibold text-slate-900">{d.domain.hostname}</h3>
                <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${scoreTone(latest?.score)}`}>
                    {latest ? `${latest.score} / 100` : 'not scanned yet'}
                </span>
                <Sparkline values={d.history.map(h => h.score).reverse()} label="score history" />
            </header>
            {latest && (
                <>
                    <div>
                        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">How the score is made</h4>
                        <ol className="mt-1 space-y-0.5 font-mono text-xs text-slate-700">
                            <li>  100  start</li>
                            {latest.deductions.map(x => (
                                <li key={x.check}>− {String(x.points).padStart(3)}  {x.check}: {x.reason}</li>
                            ))}
                            <li className="border-t border-slate-200 pt-0.5 font-semibold">
                                = {String(latest.arithmetic.score).padStart(3)}  (−{latest.arithmetic.deducted} total, floored at 0)
                            </li>
                        </ol>
                    </div>
                    <ul className="divide-y divide-slate-100">
                        {latest.checks.map(c => (
                            <li key={c.id} className="py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase ${statusTone(c.status)}`}>{c.status}</span>
                                    <span className="text-sm font-medium text-slate-800">{c.title}</span>
                                    <span className="text-sm text-slate-600">{c.summary}</span>
                                    {c.points > 0 && (c.status === 'warn' || c.status === 'fail') && (
                                        <span className="text-xs text-rose-700">−{c.points}</span>
                                    )}
                                </div>
                                {c.explanation && <p className="mt-0.5 text-xs text-slate-500">{c.explanation}</p>}
                                {c.evidence && <p className="mt-0.5 break-all font-mono text-[11px] text-slate-500">{c.evidence}</p>}
                            </li>
                        ))}
                    </ul>
                </>
            )}
        </section>
    );
}
