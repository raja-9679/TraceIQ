import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { reliabilityApi, type RateBucket } from '@/api/reliability';
import { EmptyState, Eyebrow, PageHeader, Panel } from '@/components/redesign/primitives';
import { BarStrip, HBarList, Legend, Sparkline, TrendChart } from '@/components/reports/charts';
import { WindowSelect } from '@/components/reports/ReportControls';
import { fmtPct, niceMax } from '@/components/reports/chartMath';
import { errorDetail } from '@/components/flakes/flakeHelpers';

const FLAKY = '#F2C94C';
const INK = '#1C1B22';
const FAILED = '#E0603A';

function Card({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
    return (
        <section aria-label={title} className="flex min-w-0 flex-col gap-3 rounded-3xl border border-border bg-white p-6">
            <Eyebrow right={right}>{title}</Eyebrow>
            {children}
        </section>
    );
}

function RateList({ buckets, label }: { buckets: RateBucket[]; label: string }) {
    if (buckets.length === 0) return <p className="text-sm text-ink-soft">No results.</p>;
    const max = niceMax(buckets.map((b) => b.flaky_pct), 1);
    return (
        <HBarList label={label} rows={buckets.map((b) => ({
            key: String(b.key), label: b.label, value: b.flaky_pct ?? 0, max, colour: FLAKY,
            text: `${fmtPct(b.flaky_pct)} flaky · ${b.flaky}/${b.total} · ${fmtPct(b.failed_pct)} failed`,
        }))} />
    );
}

export default function FlakeTrends() {
    const projectId = useActiveProjectId();
    const [trendDays, setTrendDays] = useState(90);
    const [patternDays, setPatternDays] = useState(30);

    const trend = useQuery({
        queryKey: ['flake-trend', projectId, trendDays],
        queryFn: () => reliabilityApi.flakeTrend(projectId!, trendDays),
        enabled: !!projectId,
    });
    const patterns = useQuery({
        queryKey: ['flake-patterns', projectId, patternDays],
        queryFn: () => reliabilityApi.flakePatterns(projectId!, patternDays),
        enabled: !!projectId,
    });

    const t = trend.data;
    const p = patterns.data;
    const hours = p?.by_hour_utc ?? [];
    const stepMax = niceMax((p?.by_failing_step_type ?? []).map((s) => s.total), 1);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Flake trends"
                subtitle="How flakiness changes over time, and where it concentrates: browser, time of day, environment and the step that fails."
                toolbar={projectId ? (
                    <>
                        <WindowSelect id="ft-trend" label="Trend" value={trendDays} options={[30, 90, 180]} onChange={setTrendDays} />
                        <WindowSelect id="ft-patterns" label="Patterns" value={patternDays} options={[7, 30, 90]} onChange={setPatternDays} />
                    </>
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its flake trends.</EmptyState>
            ) : (
                <>
                    <Card title="Flaky tests over time" right={<Legend items={[{ colour: FLAKY, label: 'Flaky cases' }, { colour: INK, label: 'Flaky results %' }]} />}>
                        {trend.isLoading ? <div className="h-[140px] animate-pulse rounded-2xl bg-background" aria-busy="true" aria-label="Loading" />
                            : trend.error ? <p className="text-sm text-ink-soft">{errorDetail(trend.error)}</p>
                                : t && t.daily.length === 0 ? <p className="text-sm text-ink-soft">{t.missing}</p>
                                    : t && (
                                        <>
                                            <TrendChart
                                                primary={t.daily.map((d) => d.flaky_cases)}
                                                secondary={t.daily.map((d) => d.flaky_result_pct)}
                                                label={`Flaky cases per day over ${t.daily.length} days; latest ${t.daily[t.daily.length - 1].flaky_cases}`}
                                            />
                                            <span className="flex justify-between text-xs text-ink-soft">
                                                <span>{t.daily[0].date}</span>
                                                <span>Latest: {t.daily[t.daily.length - 1].flaky_cases} flaky cases (score ≥ {Math.round(t.flaky_score_threshold * 100)}%), {t.daily[t.daily.length - 1].quarantined_cases} quarantined</span>
                                                <span>{t.daily[t.daily.length - 1].date}</span>
                                            </span>
                                        </>
                                    )}
                    </Card>

                    {t && t.cases.length > 0 && (
                        <Card title="Flakiest tests: score history">
                            <ul className="flex flex-col divide-y divide-[#F0EEE8]">
                                {t.cases.map((c) => (
                                    <li key={c.test_case_id} className="flex items-center gap-4 py-2.5">
                                        <span className="min-w-0 flex-1 break-words text-sm font-medium text-ink">{c.name}</span>
                                        <Sparkline values={c.history.map((h) => h.flake_score)} max={1} colour={FAILED}
                                            label={`Flake score history for ${c.name}`} />
                                        <span className="w-12 flex-none text-right font-mono text-[13px] tabular-nums text-ink">{Math.round(c.flake_score * 100)}%</span>
                                    </li>
                                ))}
                            </ul>
                        </Card>
                    )}

                    {patterns.isLoading ? (
                        <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
                    ) : patterns.error ? (
                        <EmptyState title="Couldn't load flake patterns">{errorDetail(patterns.error)}</EmptyState>
                    ) : p && p.missing ? (
                        <EmptyState title="No patterns yet">{p.missing}</EmptyState>
                    ) : p ? (
                        <>
                            <Panel title={`Patterns, last ${p.days} days`}>
                                {p.flaky_results} of {p.results} finished results were flaky. Rates are per result in each group, so a busy browser does not look flakier just for running more.
                            </Panel>
                            <div className="grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-4">
                                <Card title="By browser"><RateList buckets={p.by_browser} label="Flaky rate by browser" /></Card>
                                <Card title="By environment"><RateList buckets={p.by_environment} label="Flaky rate by environment" /></Card>
                                <Card title="By time of day (UTC)">
                                    <BarStrip values={hours.map((h) => (h.total ? h.flaky_pct ?? 0 : null))} colour={FLAKY} height={72}
                                        label={`Flaky rate by UTC hour; worst ${[...hours].sort((a, b) => (b.flaky_pct ?? 0) - (a.flaky_pct ?? 0))[0]?.label ?? 'none'}`} />
                                    <span className="flex justify-between text-xs text-ink-soft"><span>00:00</span><span>12:00</span><span>23:00</span></span>
                                    <ul className="sr-only">
                                        {hours.map((h) => <li key={h.key}>{h.label}: {fmtPct(h.flaky_pct)} flaky of {h.total}</li>)}
                                    </ul>
                                </Card>
                                <Card title="By failing step type">
                                    {p.by_failing_step_type.length === 0 ? <p className="text-sm text-ink-soft">No failed or flaky results.</p> : (
                                        <HBarList label="Flaky and failed results by failing step type" rows={p.by_failing_step_type.map((s) => ({
                                            key: s.step_type, label: s.step_type === 'unknown' ? 'unknown (no per-step evidence)' : s.step_type,
                                            value: s.total, max: stepMax, colour: FAILED,
                                            text: `${s.flaky} flaky · ${s.failed} failed`,
                                        }))} />
                                    )}
                                </Card>
                            </div>
                        </>
                    ) : null}
                </>
            )}
        </div>
    );
}
