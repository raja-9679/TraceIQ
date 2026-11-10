import { useQuery } from '@tanstack/react-query';
import { estatesApi } from '@/api/estates';
import { errDetail } from '@/lib/errors';
import { DetailCard, EmptyState, Eyebrow, Pill } from '@/components/redesign/primitives';
import Sparkline from './Sparkline';
import { scorePill, statusPill } from './helpers';

interface Props {
    domainId: number;
}

/** Every check with its explanation, and the score as 100 minus each deduction. */
export default function DomainDrilldown({ domainId }: Props) {
    const q = useQuery({ queryKey: ['estate-domain', domainId], queryFn: () => estatesApi.domain(domainId) });
    if (q.isLoading) return <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />;
    if (q.error) return <EmptyState title="Couldn't load this domain">{errDetail(q.error)}</EmptyState>;
    const d = q.data!;
    const latest = d.latest;
    return (
        <DetailCard label={`Checks for ${d.domain.hostname}`}>
            <header className="flex flex-wrap items-center gap-3">
                <h3 className="break-all text-xl font-bold tracking-[-0.01em] text-ink">{d.domain.hostname}</h3>
                <Pill tone={scorePill(latest?.score)} className="tabular-nums">
                    {latest ? `${latest.score} / 100` : 'not scanned yet'}
                </Pill>
                <Sparkline values={d.history.map(h => h.score).reverse()} label="score history" />
            </header>
            {latest && (
                <>
                    <div className="flex flex-col gap-2 rounded-2xl bg-background p-4">
                        <Eyebrow>How the score is made</Eyebrow>
                        <ol className="space-y-0.5 font-mono text-xs text-ink">
                            <li>  100  start</li>
                            {latest.deductions.map(x => (
                                <li key={x.check}>− {String(x.points).padStart(3)}  {x.check}: {x.reason}</li>
                            ))}
                            <li className="border-t border-border pt-0.5 font-semibold">
                                = {String(latest.arithmetic.score).padStart(3)}  (−{latest.arithmetic.deducted} total, floored at 0)
                            </li>
                        </ol>
                    </div>
                    <div className="flex flex-col gap-2">
                        <Eyebrow>Checks</Eyebrow>
                        <ul className="divide-y divide-[#F0EEE8] overflow-hidden rounded-2xl border border-border">
                            {latest.checks.map(c => (
                                <li key={c.id} className="px-4 py-3">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Pill tone={statusPill(c.status)} className="uppercase">{c.status}</Pill>
                                        <span className="text-sm font-semibold text-ink">{c.title}</span>
                                        <span className="text-sm text-ink-soft">{c.summary}</span>
                                        {c.points > 0 && (c.status === 'warn' || c.status === 'fail') && (
                                            <span className="text-xs font-semibold text-pastel-ink-peach">−{c.points}</span>
                                        )}
                                    </div>
                                    {c.explanation && <p className="mt-1 text-xs text-ink-soft">{c.explanation}</p>}
                                    {c.evidence && <p className="mt-1 break-all font-mono text-[11px] text-ink-soft">{c.evidence}</p>}
                                </li>
                            ))}
                        </ul>
                    </div>
                </>
            )}
        </DetailCard>
    );
}
