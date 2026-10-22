import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { analyticsApi } from '@/api/analytics';
import { DigestCard } from '@/components/reports/DigestCard';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { Segmented, SkeletonRows, Tile } from '@/components/redesign-local/groupB/bits';
import { BAR } from '@/components/redesign-local/groupB/look';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const fmtMs = (ms: number | null) => ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;

// One ranked list ("Top failing", "Slowest", ...) with a thin bar per row,
// scaled to the largest value in the list.
function RankedList({ title, empty, rows, color }: {
    title: string;
    empty: string;
    rows: { name: string; value: number; label: string }[];
    color: string;
}) {
    const max = Math.max(1, ...rows.map((r) => r.value));
    return (
        <section className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-5">
            <h2 className="text-base font-semibold text-ink">{title}</h2>
            {rows.length === 0 ? (
                <p className="text-sm text-ink-soft">{empty}</p>
            ) : (
                <ul className="flex flex-col gap-2.5">
                    {rows.map((r) => (
                        <li key={r.name} className="flex flex-col gap-1">
                            <span className="flex justify-between gap-2 text-sm">
                                <span className="truncate text-ink" title={r.name}>{r.name}</span>
                                <span className="flex-none font-mono text-xs text-ink-soft tabular-nums">{r.label}</span>
                            </span>
                            <span className="h-1.5 overflow-hidden rounded-full bg-secondary" aria-hidden="true">
                                <span className="block h-full rounded-full" style={{ width: `${Math.max(4, (r.value / max) * 100)}%`, background: color }} />
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}

export default function Analytics() {
    const projectId = useActiveProjectId();
    const [days, setDays] = useState(30);

    const summary = useQuery({ queryKey: ['eff-summary', projectId, days], queryFn: () => analyticsApi.summary(projectId!, days), enabled: !!projectId });
    const eff = useQuery({ queryKey: ['eff', projectId, days], queryFn: () => analyticsApi.effectiveness(projectId!, days, 50), enabled: !!projectId });
    const s = summary.data;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Analytics"
                subtitle="Which tests earn their keep: what fails, what is slow, what is flaky, and how fast failures get fixed."
                actions={
                    <Segmented
                        label="Time window"
                        value={String(days)}
                        onChange={(v) => setDays(parseInt(v))}
                        options={[{ id: '7', label: '7 days' }, { id: '30', label: '30 days' }, { id: '90', label: '90 days' }]}
                    />
                }
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its analytics.</EmptyState>
            ) : summary.isLoading ? (
                <>
                    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                        {[0, 1, 2, 3].map((i) => <div key={i} className="h-[92px] animate-pulse rounded-2xl bg-white/70" />)}
                    </div>
                    <SkeletonRows count={4} />
                </>
            ) : summary.error ? (
                <EmptyState title="Couldn't load analytics">{errDetail(summary.error)}</EmptyState>
            ) : s && (
                <>
                    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                        <Tile
                            label="Pass rate" value={`${s.pass_rate}%`}
                            tone={s.pass_rate >= 90 ? undefined : s.pass_rate >= 60 ? 'text-pastel-ink-butter' : 'text-pastel-ink-peach'}
                        />
                        <Tile label="Finished runs" value={s.total_finished_runs} />
                        <Tile label="Open failure groups" value={s.open_clusters} tone={s.open_clusters ? 'text-pastel-ink-peach' : undefined} />
                        <Tile label="Mean time to fix" value={s.mttr_hours == null ? '—' : `${s.mttr_hours}h`} />
                    </div>

                    <div className="grid gap-5 lg:grid-cols-3">
                        <RankedList
                            title="Top failing" empty="Nothing failed in this window." color={BAR.fail}
                            rows={s.top_failing_tests.map((t) => ({ name: t.test_name, value: t.failures, label: `${t.failures}` }))}
                        />
                        <RankedList
                            title="Slowest" empty="No timings yet." color="#1C1B22"
                            rows={s.slowest_tests.map((t) => ({ name: t.test_name, value: t.avg_duration_ms ?? 0, label: fmtMs(t.avg_duration_ms) }))}
                        />
                        <RankedList
                            title="Flakiest" empty="No flaky tests." color={BAR.warn}
                            rows={s.flakiest_tests.map((t) => ({ name: t.test_name, value: t.flake_score, label: `${Math.round(t.flake_score * 100)}%` }))}
                        />
                    </div>

                    <section className="overflow-hidden rounded-3xl border border-border bg-white">
                        <div className="flex flex-col gap-1 px-6 pb-3 pt-5">
                            <h2 className="text-base font-semibold text-ink">Per-test effectiveness</h2>
                            <p className="text-[13px] text-ink-soft">Failure groups found means the test surfaced a real problem someone triaged.</p>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[640px] text-sm">
                                <thead>
                                    <tr className="border-y border-border text-left text-[13px] text-ink-soft">
                                        <th scope="col" className="px-6 py-2.5 font-medium">Test</th>
                                        <th scope="col" className="px-4 py-2.5 text-right font-medium">Runs</th>
                                        <th scope="col" className="px-4 py-2.5 text-right font-medium">Failures</th>
                                        <th scope="col" className="px-4 py-2.5 text-right font-medium">Failure rate</th>
                                        <th scope="col" className="px-4 py-2.5 text-right font-medium">Failure groups found</th>
                                        <th scope="col" className="px-6 py-2.5 text-right font-medium">Average time</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {(eff.data || []).map((e) => (
                                        <tr key={e.test_name} className="border-b border-[#F0EEE8] last:border-0">
                                            <td className="max-w-[320px] truncate px-6 py-3 font-medium text-ink" title={e.test_name}>{e.test_name}</td>
                                            <td className="px-4 py-3 text-right tabular-nums text-ink-soft">{e.runs}</td>
                                            <td className="px-4 py-3 text-right tabular-nums">{e.failures > 0 ? <span className="font-semibold text-pastel-ink-peach">{e.failures}</span> : <span className="text-ink-soft">0</span>}</td>
                                            <td className="px-4 py-3 text-right tabular-nums text-ink-soft">{e.failure_rate}%</td>
                                            <td className="px-4 py-3 text-right tabular-nums">{e.clusters_surfaced > 0 ? <Pill tone="lavender">{e.clusters_surfaced}</Pill> : <span className="text-ink-soft">—</span>}</td>
                                            <td className="px-6 py-3 text-right font-mono text-xs tabular-nums text-ink-soft">{fmtMs(e.avg_duration_ms)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </section>

                    <DigestCard projectId={projectId} />
                </>
            )}
        </div>
    );
}
