import type { ReactNode } from 'react';
import { Pill } from '@/components/redesign/primitives';
import { toneClasses } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';
import { labelReason } from '@/lib/gateChecks';
import type { ClusterRow, DailyPoint, SummaryProjectRow, TopFailing, Verdict, WindowStats } from '@/api/reports';
import { deltaTone, fmtDelta, fmtPct, verdictOf } from './reportHelpers';

// One-page executive summary. Shared by the signed-in page and the public
// share page, so it takes only what both have; the private extras (cluster
// titles, readiness reasons) are optional and simply absent on a share.

export interface SummaryData {
    name: string;
    window_days: number;
    generated_at: string;
    headline: string;
    current: WindowStats;
    trend: { pass_rate: number | null; runs: number; failures: number };
    daily: DailyPoint[];
    top_failing: TopFailing[];
    open_clusters: number;
    flaky_count: number;
    incidents: { escaped: number; open: number; open_critical: number };
    readiness: { verdict: Verdict; reasons?: string[] } | null;
    verdicts?: Record<Verdict, number>;
    projects?: SummaryProjectRow[];
    clusters?: ClusterRow[];
}

function Kpi({ label, value, delta, tone }: { label: string; value: string | number; delta?: string; tone?: ReturnType<typeof deltaTone> }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4 break-inside-avoid">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[26px] font-bold leading-tight tracking-tight text-ink tabular-nums">{value}</span>
            {delta && <span className={cn('text-xs font-medium tabular-nums', tone && tone !== 'neutral' ? toneClasses[tone].ink : 'text-ink-soft')}>{delta}</span>}
        </div>
    );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
    return (
        <section aria-label={title} className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-5 break-inside-avoid">
            <h2 className="text-base font-semibold text-ink">{title}</h2>
            {children}
        </section>
    );
}

function PassRateTrend({ daily }: { daily: DailyPoint[] }) {
    const withRuns = daily.filter((d) => d.runs > 0);
    const label = withRuns.length
        ? `Daily pass rate from ${daily[0]?.date} to ${daily[daily.length - 1]?.date}: lowest ${Math.min(...withRuns.map((d) => d.pass_rate ?? 0))}%, highest ${Math.max(...withRuns.map((d) => d.pass_rate ?? 0))}%`
        : 'No runs in this period';
    return (
        <div>
            <div role="img" aria-label={label} className="flex h-32 items-end gap-[2px] border-b border-border">
                {daily.map((d) => (
                    // Each day gets an equal slot across the whole width, so the
                    // first and last dates sit under the first and last bars;
                    // the bar itself is capped and centred in its slot.
                    <div key={d.date} className="flex h-full min-w-0 flex-1 items-end justify-center" title={d.runs ? `${d.date}: ${d.pass_rate}% of ${d.runs} runs` : `${d.date}: no runs`}>
                        {d.runs > 0 ? (
                            <div className="w-full max-w-7 rounded-t-[4px] bg-ink/75 print:bg-ink" style={{ height: `${Math.max(3, d.pass_rate ?? 0)}%` }} />
                        ) : (
                            <div className="h-[3px] w-full max-w-7 rounded-full bg-secondary" />
                        )}
                    </div>
                ))}
            </div>
            <div className="mt-1 flex justify-between text-xs text-ink-soft">
                <span>{daily[0]?.date}</span>
                <span>Bar height = share of runs that passed that day</span>
                <span>{daily[daily.length - 1]?.date}</span>
            </div>
        </div>
    );
}

export function SummaryView({ data }: { data: SummaryData }) {
    const v = data.readiness ? verdictOf(data.readiness.verdict) : null;
    const period = data.window_days === 7 ? 'week' : `${data.window_days} days`;
    return (
        <div className="flex flex-col gap-5">
            <section aria-label="Headline" className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6 break-inside-avoid">
                <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-soft">
                    <span>{data.name}</span><span>·</span><span>last {period}</span><span>·</span>
                    <span>generated {new Date(data.generated_at).toLocaleString()}</span>
                </div>
                <p className="text-lg leading-relaxed text-ink">{data.headline}</p>
                {v && (
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-ink">Release readiness</span>
                        <Pill tone={v.tone}>{v.label}</Pill>
                    </div>
                )}
                {data.verdicts && (
                    <div className="flex flex-wrap items-center gap-2 text-sm text-ink-soft">
                        <span className="font-medium text-ink">Release readiness by project</span>
                        <Pill tone="mint">{data.verdicts.ready ?? 0} ready</Pill>
                        <Pill tone="peach">{data.verdicts.not_ready ?? 0} not ready</Pill>
                        <Pill tone="butter">{data.verdicts.insufficient_data ?? 0} not enough data</Pill>
                    </div>
                )}
                {data.readiness?.reasons && data.readiness.reasons.length > 0 && (
                    <ul className="list-disc pl-5 text-[13px] text-ink-soft">
                        {data.readiness.reasons.map((r) => <li key={r}>{labelReason(r)}</li>)}
                    </ul>
                )}
            </section>

            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6 print:grid-cols-3">
                <Kpi label="Pass rate" value={fmtPct(data.current.pass_rate)} delta={`${fmtDelta(data.trend.pass_rate, ' pts')} vs previous`} tone={deltaTone(data.trend.pass_rate)} />
                <Kpi label="Runs" value={data.current.runs} delta={`${fmtDelta(data.trend.runs)} vs previous`} />
                <Kpi label="Failing results" value={data.current.failures} delta={`${fmtDelta(data.trend.failures)} vs previous`} tone={deltaTone(data.trend.failures, false)} />
                <Kpi label="Flaky tests" value={data.flaky_count} />
                <Kpi label="Open failure groups" value={data.open_clusters} />
                <Kpi label="Escaped defects" value={data.incidents.escaped} delta={data.incidents.open_critical ? `${data.incidents.open_critical} critical incident(s) open` : undefined} tone={data.incidents.open_critical ? 'peach' : undefined} />
            </div>

            <Section title="Pass rate trend">
                <PassRateTrend daily={data.daily} />
            </Section>

            {data.projects && (
                <Section title="Projects">
                    {data.projects.length === 0 ? (
                        <p className="text-sm text-ink-soft">No projects in this summary.</p>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="text-left text-xs text-ink-soft">
                                    <tr><th className="py-2 pr-3 font-medium">Project</th><th className="py-2 pr-3 font-medium">Pass rate</th><th className="py-2 pr-3 font-medium">Change</th><th className="py-2 pr-3 font-medium">Runs</th><th className="py-2 pr-3 font-medium">Flaky</th><th className="py-2 font-medium">Readiness</th></tr>
                                </thead>
                                <tbody>
                                    {data.projects.map((p) => {
                                        const pv = verdictOf(p.verdict);
                                        const name = p.project_name ?? p.name ?? '';
                                        return (
                                            <tr key={p.project_id ?? name} className="border-t border-[#F0EEE8]">
                                                <td className="py-2 pr-3 font-medium text-ink">{name}</td>
                                                <td className="py-2 pr-3 tabular-nums">{fmtPct(p.pass_rate)}</td>
                                                <td className="py-2 pr-3 tabular-nums">{fmtDelta(p.trend_pass_rate, ' pts')}</td>
                                                <td className="py-2 pr-3 tabular-nums">{p.runs}</td>
                                                <td className="py-2 pr-3 tabular-nums">{p.flaky_count}</td>
                                                <td className="py-2"><Pill tone={pv.tone}>{pv.label}</Pill></td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </Section>
            )}

            <div className="grid gap-5 lg:grid-cols-2 print:grid-cols-2">
                <Section title="Top failing tests">
                    {data.top_failing.length === 0 ? (
                        <p className="text-sm text-ink-soft">No failing tests in this period.</p>
                    ) : (
                        <ul className="flex flex-col gap-2">
                            {data.top_failing.map((t) => (
                                <li key={t.test_name} className="flex justify-between gap-3 text-sm">
                                    <span className="truncate text-ink" title={t.test_name}>{t.test_name}</span>
                                    <span className="flex-none font-mono text-xs text-ink-soft tabular-nums">{t.failures} failure{t.failures === 1 ? '' : 's'}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </Section>
                {data.clusters && (
                    <Section title="Open failure groups">
                        {data.clusters.length === 0 ? (
                            <p className="text-sm text-ink-soft">No open failure groups.</p>
                        ) : (
                            <ul className="flex flex-col gap-2">
                                {data.clusters.map((c) => (
                                    <li key={c.id} className="flex justify-between gap-3 text-sm">
                                        <span className="min-w-0 truncate text-ink" title={c.title}>{c.title}</span>
                                        <span className="flex-none text-xs text-ink-soft">{c.category} · {c.occurrences}×</span>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Section>
                )}
            </div>
        </div>
    );
}
