import { useQuery } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import { ArrowDownRight, ArrowUpRight, Minus, Globe, RefreshCw } from 'lucide-react';
import { getRun, getComparison, ComparisonDelta } from '@/lib/api';
import { EmptyState, KindChip, PageHeader, Pill } from '@/components/redesign/primitives';
import { SkeletonRows, Tile } from '@/components/redesign-local/groupB/bits';
import { cn } from '@/lib/utils';

const ACTIVE_STATUSES = ['pending', 'running'];

function StatusChip({ status }: { status: string | null }) {
    if (!status) return <Pill tone="neutral">Absent</Pill>;
    const passed = status === 'passed';
    return <Pill tone={passed ? 'mint' : 'peach'}>{passed ? 'Passed' : status.charAt(0).toUpperCase() + status.slice(1)}</Pill>;
}

function DurationDelta({ delta }: { delta: ComparisonDelta }) {
    const b = delta.baseline_duration_ms;
    const c = delta.candidate_duration_ms;
    if (b == null || c == null) return <span className="text-ink-soft">—</span>;
    const diff = c - b;
    const pct = b > 0 ? Math.round((diff / b) * 100) : 0;
    const slower = diff > 0;
    const negligible = Math.abs(pct) < 5;
    return (
        <span className={cn(
            'inline-flex items-center gap-1 font-mono text-xs',
            negligible ? 'text-ink-soft' : slower ? 'text-pastel-ink-butter' : 'text-pastel-ink-mint'
        )}>
            {negligible ? <Minus size={13} aria-hidden="true" /> : slower ? <ArrowUpRight size={13} aria-hidden="true" /> : <ArrowDownRight size={13} aria-hidden="true" />}
            {(c / 1000).toFixed(2)}s ({pct > 0 ? '+' : ''}{pct}%)
        </span>
    );
}

export default function ComparisonView() {
    const { runId: idParam } = useParams<{ runId: string }>();
    const runId = parseInt(idParam || '0');

    // Poll the candidate run until it finishes, then fetch the diff.
    const { data: run } = useQuery({
        queryKey: ['run', runId],
        queryFn: () => getRun(runId),
        enabled: runId > 0,
        refetchInterval: (query) =>
            ACTIVE_STATUSES.includes(query.state.data?.status ?? 'pending') ? 3000 : false,
    });
    const finished = !!run && !ACTIVE_STATUSES.includes(run.status);

    const { data: report, isLoading: reportLoading, error } = useQuery({
        queryKey: ['comparison', runId],
        queryFn: () => getComparison(runId),
        enabled: runId > 0 && finished,
    });

    if (!runId) return <EmptyState title="Invalid run ID" />;

    const summary = report?.summary;
    const verdictRegressed = (summary?.regressed ?? 0) > 0;

    // Regressions first, then recoveries, then everything else.
    const deltas = [...(report?.deltas || [])].sort((a, b) =>
        Number(b.regressed) - Number(a.regressed) || Number(b.recovered) - Number(a.recovered) || a.test_name.localeCompare(b.test_name));

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                crumbs={
                    <>
                        <Link to={`/runs/${runId}`} className="hover:text-ink">Run #{runId}</Link>
                        <span aria-hidden="true">/</span>
                        <span className="text-ink">Deployment comparison</span>
                    </>
                }
                title="Deployment comparison"
                subtitle={report ? (
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span>
                            Baseline{' '}
                            <Link to={`/runs/${report.baseline_run_id}`} className="font-semibold text-ink underline-offset-2 hover:underline">
                                run #{report.baseline_run_id}
                            </Link>
                        </span>
                        <span aria-hidden="true">vs</span>
                        <span>
                            candidate{' '}
                            <Link to={`/runs/${report.candidate_run_id}`} className="font-semibold text-ink underline-offset-2 hover:underline">
                                run #{report.candidate_run_id}
                            </Link>
                        </span>
                        {report.target_url && (
                            <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-2.5 py-0.5 font-mono text-xs text-ink-soft">
                                <Globe size={12} aria-hidden="true" /> {report.target_url}
                            </span>
                        )}
                    </span>
                ) : 'The same suite run against a new deployment, compared test by test with its baseline.'}
            />

            {!finished ? (
                <div className="flex items-center gap-4 rounded-3xl border border-border bg-white p-6">
                    <span className="flex h-10 w-10 flex-none items-center justify-center rounded-[12px] bg-pastel-lavender text-pastel-ink-lavender" aria-hidden="true">
                        <RefreshCw className="h-5 w-5 animate-spin" />
                    </span>
                    <div>
                        <h2 className="text-base font-semibold text-ink">Candidate run in progress</h2>
                        <p className="text-sm text-ink-soft">
                            Re-running the baseline's suite against the new deployment. This page updates automatically.
                        </p>
                    </div>
                </div>
            ) : error ? (
                <EmptyState title="Couldn't load the comparison">
                    {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                    {(error as any)?.response?.data?.detail || 'Failed to load the comparison — is this a comparison run?'}
                </EmptyState>
            ) : reportLoading || !report ? (
                <SkeletonRows count={5} />
            ) : (
                <>
                    {/* Verdict */}
                    <div className="flex items-center gap-4 rounded-3xl border border-border bg-white p-5">
                        <KindChip kind={verdictRegressed ? 'failure' : 'pass'} />
                        <div className="flex flex-col gap-0.5">
                            <h2 className="text-lg font-bold text-ink">
                                {verdictRegressed
                                    ? `${summary!.regressed} test${summary!.regressed === 1 ? '' : 's'} regressed on this deployment`
                                    : 'No regressions — safe to promote'}
                            </h2>
                            <p className="text-sm text-ink-soft">
                                {summary!.recovered > 0 && `${summary!.recovered} recovered · `}
                                {summary!.unchanged} unchanged out of {deltas.length} compared tests.
                            </p>
                        </div>
                    </div>

                    {/* Summary tiles */}
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                        <Tile label="Regressed" value={summary!.regressed} hint="Passed on baseline, failing on candidate" tone={summary!.regressed ? 'text-pastel-ink-peach' : undefined} />
                        <Tile label="Recovered" value={summary!.recovered} hint="Failing on baseline, passing on candidate" />
                        <Tile label="Unchanged" value={summary!.unchanged} hint="Same outcome on both deployments" />
                    </div>

                    {/* Deltas */}
                    {deltas.length === 0 ? (
                        <EmptyState title="Nothing to compare">No comparable results between the two runs.</EmptyState>
                    ) : (
                        <div className="overflow-x-auto rounded-3xl border border-border bg-white">
                            <table className="w-full min-w-[640px] text-sm">
                                <thead>
                                    <tr className="border-b border-border text-left text-[13px] text-ink-soft">
                                        <th scope="col" className="px-5 py-3 font-medium">Test</th>
                                        <th scope="col" className="px-5 py-3 font-medium">Baseline</th>
                                        <th scope="col" className="px-5 py-3 font-medium">Candidate</th>
                                        <th scope="col" className="px-5 py-3 font-medium">Candidate duration</th>
                                        <th scope="col" className="px-5 py-3 font-medium">Change</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {deltas.map((d) => (
                                        <tr key={d.test_name} className="border-b border-[#F0EEE8] last:border-0">
                                            <td className="px-5 py-3">
                                                <span className="flex items-center gap-3">
                                                    <KindChip kind={d.regressed ? 'failure' : d.recovered ? 'pass' : 'skip'} size="sm" />
                                                    <span className="font-semibold text-ink">{d.test_name}</span>
                                                </span>
                                            </td>
                                            <td className="px-5 py-3"><StatusChip status={d.baseline_status} /></td>
                                            <td className="px-5 py-3"><StatusChip status={d.candidate_status} /></td>
                                            <td className="px-5 py-3"><DurationDelta delta={d} /></td>
                                            <td className="px-5 py-3">
                                                {d.regressed ? (
                                                    <Pill tone="peach">Regressed</Pill>
                                                ) : d.recovered ? (
                                                    <Pill tone="mint">Recovered</Pill>
                                                ) : (
                                                    <span className="text-xs text-ink-soft">No change</span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
