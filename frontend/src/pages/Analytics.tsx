import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Send, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { analyticsApi } from '@/api/analytics';
import { reportsApi, ReportSchedule } from '@/api/reports';
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

function ReportsSection({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [adding, setAdding] = useState(false);
    const [f, setF] = useState({ name: '', cron_expression: '0 9 * * 1', window_days: 7, channels: 'slack', recipients: '' });
    const reports = useQuery({ queryKey: ['reports', projectId], queryFn: () => reportsApi.list(projectId) });

    const create = useMutation({
        mutationFn: () => reportsApi.create(projectId, {
            name: f.name, cron_expression: f.cron_expression, window_days: f.window_days,
            channels: f.channels.split(',').map((s) => s.trim()).filter(Boolean),
            recipients: f.recipients.split(',').map((s) => s.trim()).filter(Boolean),
        }),
        onSuccess: () => { toast.success('Report scheduled'); qc.invalidateQueries({ queryKey: ['reports', projectId] }); setAdding(false); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const sendNow = useMutation({ mutationFn: (id: number) => reportsApi.sendNow(id), onSuccess: () => toast.success('Report queued'), onError: (e) => toast.error(errDetail(e)) });
    const del = useMutation({ mutationFn: (id: number) => reportsApi.remove(id), onSuccess: () => { toast.success('Removed'); qc.invalidateQueries({ queryKey: ['reports', projectId] }); }, onError: (e) => toast.error(errDetail(e)) });

    return (
        <section className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex flex-col gap-1">
                    <h2 className="text-base font-semibold text-ink">Scheduled reports</h2>
                    <p className="text-[13px] text-ink-soft">A recurring quality summary sent to Slack, Teams or email.</p>
                </div>
                {!adding && <Button variant="outline" onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> New report</Button>}
            </div>
            {adding && (
                <div className="grid gap-3 rounded-2xl bg-background p-4 sm:grid-cols-2">
                    <label className="flex flex-col gap-1 text-[13px] text-ink-soft">Name
                        <Input className="h-10 rounded-full bg-white" placeholder="Weekly quality" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1 text-[13px] text-ink-soft">Cron schedule
                        <Input className="h-10 rounded-full bg-white font-mono" placeholder="0 9 * * 1" value={f.cron_expression} onChange={(e) => setF({ ...f, cron_expression: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1 text-[13px] text-ink-soft">Window (days)
                        <Input className="h-10 rounded-full bg-white" type="number" placeholder="7" value={f.window_days} onChange={(e) => setF({ ...f, window_days: parseInt(e.target.value) || 7 })} />
                    </label>
                    <label className="flex flex-col gap-1 text-[13px] text-ink-soft">Channels
                        <Input className="h-10 rounded-full bg-white" placeholder="slack,email" value={f.channels} onChange={(e) => setF({ ...f, channels: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1 text-[13px] text-ink-soft sm:col-span-2">Email recipients (comma-separated)
                        <Input className="h-10 rounded-full bg-white" placeholder="qa@example.com" value={f.recipients} onChange={(e) => setF({ ...f, recipients: e.target.value })} />
                    </label>
                    <div className="flex gap-2 sm:col-span-2">
                        <Button onClick={() => create.mutate()} disabled={create.isPending || !f.name || !f.cron_expression}>Create</Button>
                        <Button variant="outline" onClick={() => setAdding(false)}>Cancel</Button>
                    </div>
                </div>
            )}
            {!reports.data || reports.data.length === 0 ? (
                <p className="text-sm text-ink-soft">No scheduled reports yet.</p>
            ) : (
                <ul className="flex flex-col">
                    {reports.data.map((r: ReportSchedule, i: number) => (
                        <li key={r.id} className={`flex min-h-14 flex-wrap items-center justify-between gap-2 py-2 ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                            <div className="flex min-w-0 flex-col">
                                <span className="text-sm font-semibold text-ink">{r.name}</span>
                                <span className="text-xs text-ink-soft"><span className="font-mono">{r.cron_expression}</span> · last {r.window_days} days · {(r.channels || []).join(', ') || 'slack'}</span>
                            </div>
                            <div className="flex items-center gap-1">
                                <Button size="sm" variant="outline" className="h-10 px-4" onClick={() => sendNow.mutate(r.id)}><Send className="h-4 w-4" /> Send now</Button>
                                <Button size="icon" variant="ghost" className="text-ink-soft hover:bg-pastel-peach hover:text-pastel-ink-peach" aria-label={`Delete report ${r.name}`} onClick={() => del.mutate(r.id)}><Trash2 className="h-4 w-4" /></Button>
                            </div>
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
                            title="Flakiest" empty="No flaky tests in this window." color={BAR.warn}
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

                    <ReportsSection projectId={projectId} />
                </>
            )}
        </div>
    );
}
