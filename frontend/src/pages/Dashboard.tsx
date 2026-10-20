import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronRight, Loader2 } from 'lucide-react';
import { api, getRuns, type TestRun } from '@/lib/api';
import { cn, formatDate } from '@/lib/utils';
import { useAuth } from '@/context/AuthContext';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { Button } from '@/components/ui/button';
import { EmptyState, Eyebrow, KindChip, PageHeader, Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';

interface TrendBucket {
    date: string;
    total_runs: number;
    passed: number;
    failed: number;
    pass_rate: number | null;
    avg_duration_ms: number | null;
}

const RUN_LOOK: Record<TestRun['status'], { label: string; tone: Tone }> = {
    passed: { label: 'Passed', tone: 'mint' },
    failed: { label: 'Failed', tone: 'peach' },
    error: { label: 'Error', tone: 'peach' },
    running: { label: 'Running', tone: 'lavender' },
    pending: { label: 'Queued', tone: 'neutral' },
    // WP15f: over, but no verdict.
    cancelled: { label: 'Cancelled', tone: 'neutral' },
    timed_out: { label: 'Timed out', tone: 'butter' },
};

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[28px] font-bold leading-none tracking-tight tabular-nums text-ink">{value}</span>
            {sub && <span className="text-xs text-ink-soft">{sub}</span>}
        </div>
    );
}

function RunLeading({ status }: { status: TestRun['status'] }) {
    if (status === 'passed') return <KindChip kind="pass" size="sm" />;
    if (status === 'failed' || status === 'error') return <KindChip kind="failure" size="sm" />;
    if (status === 'running') {
        return (
            <span className="flex h-7 w-7 flex-none items-center justify-center rounded-[9px] bg-pastel-lavender text-pastel-ink-lavender" aria-hidden="true">
                <Loader2 className="h-[15px] w-[15px] animate-spin" strokeWidth={2.2} />
            </span>
        );
    }
    return <KindChip kind="skip" size="sm" />;
}

export default function Dashboard() {
    const { user } = useAuth();
    const activeProjectId = useActiveProjectId();

    const { data: runsData, isLoading } = useQuery({
        queryKey: ['runs'],
        queryFn: () => getRuns(),
        refetchInterval: 15000,
    });

    const { data: trendsData } = useQuery({
        queryKey: ['project-trends', activeProjectId],
        queryFn: () => api.get(`/analytics/projects/${activeProjectId}/trends`, { params: { days: 14 } }).then(res => res.data),
        enabled: !!activeProjectId,
        refetchInterval: 60000,
    });

    const trendBuckets: TrendBucket[] = trendsData?.buckets || [];
    const todayBucket = trendBuckets.length > 0 ? trendBuckets[trendBuckets.length - 1] : null;

    const runs = runsData?.runs || [];
    const stats = {
        total: runsData?.total || 0,
        passed: runs.filter(r => r.status === 'passed').length,
        failed: runs.filter(r => r.status === 'failed').length,
        running: runs.filter(r => r.status === 'running').length,
    };

    // Pass rate: prefer today's server-side daily aggregate (correct across
    // ALL runs). The old computation divided first-page pass counts by the
    // server-wide total, which understated the rate as soon as history
    // exceeded one page — keep it only as a fallback (over the fetched page,
    // not the server total) when no project trends are available.
    const completedOnPage = stats.passed + stats.failed;
    const passRate = todayBucket && todayBucket.pass_rate !== null
        ? todayBucket.pass_rate.toFixed(1)
        : (completedOnPage > 0 ? ((stats.passed / completedOnPage) * 100).toFixed(1) : '0.0');

    const firstName = user?.full_name?.split(' ')[0] || 'there';

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Overview"
                subtitle={`Welcome back, ${firstName}. How test runs are going across the workspace.`}
                actions={<Button asChild variant="outline"><Link to="/runs">All runs</Link></Button>}
            />

            {isLoading ? (
                <>
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3" aria-busy="true" aria-label="Loading">
                        {[0, 1, 2, 3].map((i) => <div key={i} className="h-[92px] animate-pulse rounded-2xl bg-white/70" />)}
                    </div>
                    <div className="flex flex-col gap-2">
                        {[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-[60px] animate-pulse rounded-2xl bg-white/70" />)}
                    </div>
                </>
            ) : (
                <>
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
                        <Tile label="Total runs" value={stats.total.toString()} sub="All test executions" />
                        <Tile
                            label="Passed"
                            value={stats.passed.toString()}
                            sub={`${passRate}% pass rate${todayBucket && todayBucket.pass_rate !== null ? ' today' : ''}`}
                        />
                        <Tile label="Failed" value={stats.failed.toString()} sub={stats.failed > 0 ? 'Needs attention' : 'Nothing failing'} />
                        <Tile label="Running" value={stats.running.toString()} sub="In progress now" />
                    </div>

                    {/* 14-day pass-rate trend */}
                    {activeProjectId && trendBuckets.length > 0 && (
                        <section aria-labelledby="trend-h" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <h2 id="trend-h" className="text-base font-semibold text-ink">Pass rate, last 14 days</h2>
                                {todayBucket && todayBucket.pass_rate !== null && (
                                    <Pill tone={todayBucket.pass_rate >= 90 ? 'mint' : todayBucket.pass_rate >= 60 ? 'butter' : 'peach'}>
                                        Today {todayBucket.pass_rate.toFixed(1)}%
                                    </Pill>
                                )}
                            </div>
                            <div
                                className="flex h-36 items-end gap-1.5 sm:gap-2"
                                role="img"
                                aria-label={`Daily pass rate: ${trendBuckets.map((b) => `${b.date} ${b.pass_rate === null ? 'no runs' : `${b.pass_rate.toFixed(1)}%`}`).join(', ')}`}
                            >
                                {trendBuckets.map((b) => {
                                    const hasData = b.pass_rate !== null;
                                    const height = hasData ? Math.max(4, b.pass_rate as number) : 4;
                                    return (
                                        <div key={b.date} className="flex h-full flex-1 flex-col items-center justify-end">
                                            <div
                                                className={cn(
                                                    'w-full rounded-t-md',
                                                    !hasData ? 'bg-secondary'
                                                        : (b.pass_rate as number) >= 90 ? 'bg-[#8FD3B6]'
                                                            : (b.pass_rate as number) >= 60 ? 'bg-[#F2C94C]'
                                                                : 'bg-[#E0603A]'
                                                )}
                                                style={{ height: `${height}%` }}
                                                title={hasData
                                                    ? `${b.date}: ${(b.pass_rate as number).toFixed(1)}% pass rate (${b.passed} passed / ${b.failed} failed of ${b.total_runs} runs)`
                                                    : `${b.date}: no completed runs`}
                                            />
                                            <span className="mt-1.5 whitespace-nowrap font-mono text-[10px] text-ink-soft">{b.date.slice(5)}</span>
                                        </div>
                                    );
                                })}
                            </div>
                        </section>
                    )}

                    {/* Recent runs */}
                    <section aria-labelledby="recent-h" className="flex flex-col gap-2">
                        <div className="px-1">
                            <Eyebrow>
                                <span id="recent-h">Recent runs</span>
                            </Eyebrow>
                        </div>
                        {runs.length === 0 ? (
                            <EmptyState title="No runs yet">When you run a test suite, it will appear here.</EmptyState>
                        ) : (
                            <ul className="overflow-hidden rounded-3xl border border-border bg-white">
                                {runs.slice(0, 10).map((run, i) => {
                                    const look = RUN_LOOK[run.status] ?? { label: run.status, tone: 'neutral' as Tone };
                                    return (
                                        <li key={run.id} className={cn(i > 0 && 'border-t border-[#F0EEE8]')}>
                                            <Link
                                                to={`/runs/${run.id}`}
                                                className="group flex min-h-[60px] items-center gap-3 px-4 py-3 text-ink transition-colors hover:bg-background sm:px-5"
                                            >
                                                <RunLeading status={run.status} />
                                                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                                                    <span className="flex flex-wrap items-center gap-2 text-sm font-semibold leading-snug">
                                                        <span className="break-words">{run.suite_name || `Run #${run.id}`}</span>
                                                        {run.test_case_name && (
                                                            <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-normal text-ink-soft">{run.test_case_name}</span>
                                                        )}
                                                    </span>
                                                    <span className="font-mono text-xs text-ink-soft">
                                                        {formatDate(run.created_at)}
                                                        {run.duration_ms ? ` · ${(run.duration_ms / 1000).toFixed(2)}s` : ''}
                                                    </span>
                                                </span>
                                                <Pill tone={look.tone}>{look.label}</Pill>
                                                <ChevronRight className="hidden h-4 w-4 flex-none text-ink-soft transition-transform group-hover:translate-x-0.5 sm:block" aria-hidden="true" />
                                            </Link>
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                    </section>
                </>
            )}
        </div>
    );
}
