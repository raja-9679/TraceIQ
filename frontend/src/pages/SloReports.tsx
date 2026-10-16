import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { reliabilityApi, type SloReport } from '@/api/reliability';
import { EmptyState, Eyebrow, PageHeader, Panel, Pill } from '@/components/redesign/primitives';
import { FieldRow, SettingsCard, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { BarStrip, Meter } from '@/components/reports/charts';
import { LabelledSelect, MetricTile } from '@/components/reports/ReportControls';
import { SLO_STATUS, budgetColour, fmtMs, fmtPct, fmtRate } from '@/components/reports/chartMath';
import { errorDetail } from '@/components/flakes/flakeHelpers';

function TargetsForm({ report, projectId }: { report: SloReport; projectId: number }) {
    const qc = useQueryClient();
    const id = `slo-${report.schedule_id}`;
    const [availability, setAvailability] = useState(report.configured.availability_target?.toString() ?? '');
    const [latency, setLatency] = useState(report.configured.latency_p95_ms?.toString() ?? '');
    const [windowDays, setWindowDays] = useState(report.configured.window_days?.toString() ?? '');

    const save = useMutation({
        mutationFn: () => reliabilityApi.setMonitorSlo(report.schedule_id, {
            availability_target: availability.trim() ? Number(availability) : null,
            latency_p95_ms: latency.trim() ? Number(latency) : null,
            window_days: windowDays ? Number(windowDays) : null,
        }),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['project-slo', projectId] });
            toast.success(`Targets saved for "${report.name}"`);
        },
        onError: (err: unknown) => toast.error(`Couldn't save targets: ${errorDetail(err)}`),
    });

    return (
        <SettingsCard
            title="Edit targets"
            description="Leave availability empty to use the default target. Editors only."
            footer={<Button onClick={() => save.mutate()} disabled={save.isPending}>Save targets</Button>}
        >
            <FieldRow label="Availability target (%)" htmlFor={`${id}-avail`} hint="Share of checks that must pass, e.g. 99.9">
                <Input id={`${id}-avail`} className={inputClass} inputMode="decimal" value={availability}
                    placeholder={String(report.target_pct)} onChange={(e) => setAvailability(e.target.value)} />
            </FieldRow>
            <FieldRow label="Latency p95 target (ms)" htmlFor={`${id}-lat`} hint="Optional; measured from each check's run duration">
                <Input id={`${id}-lat`} className={inputClass} inputMode="numeric" value={latency}
                    placeholder="none" onChange={(e) => setLatency(e.target.value)} />
            </FieldRow>
            <FieldRow label="Default window" htmlFor={`${id}-win`}>
                <select id={`${id}-win`} className="h-10 w-full rounded-full border border-border bg-white px-4 text-sm"
                    value={windowDays} onChange={(e) => setWindowDays(e.target.value)}>
                    <option value="">30 days (default)</option>
                    <option value="7">7 days</option>
                    <option value="30">30 days</option>
                    <option value="90">90 days</option>
                </select>
            </FieldRow>
        </SettingsCard>
    );
}

function MonitorCard({ r, projectId }: { r: SloReport; projectId: number }) {
    const [editing, setEditing] = useState(false);
    const status = SLO_STATUS[r.status] ?? SLO_STATUS.no_data;
    const remaining = r.error_budget.remaining_pct;
    const daily = r.daily.map((d) => (d.checks ? (100 * d.good) / d.checks : null));
    const colours = daily.map((v) => (v === null ? '#EDEBE5' : v >= r.target_pct ? '#8FD3B6' : '#E0603A'));

    return (
        <section aria-label={`SLO for ${r.name}`} className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 flex-col gap-1">
                    <h2 className="break-words text-lg font-semibold text-ink">{r.name}</h2>
                    <span className="text-xs text-ink-soft">
                        Last {r.window_days} days · {r.checks} checks · target {r.target_pct}%{r.target_is_default ? ' (default target)' : ''}
                    </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={status.tone}>{status.label}</Pill>
                    <Button variant="outline" size="sm" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
                        {editing ? 'Close targets' : 'Edit targets'}
                    </Button>
                </div>
            </div>

            {r.missing ? (
                <p className="text-sm text-ink-soft">{r.missing}</p>
            ) : (
                <>
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
                        <MetricTile label="Compliance" value={fmtPct(r.compliance_pct, 2)}>
                            <span>{r.good} good · {r.bad} failed checks</span>
                        </MetricTile>
                        <MetricTile label="Error budget left" value={remaining === null ? '—' : `${remaining}%`}>
                            <Meter percent={remaining ?? 0} colour={budgetColour(remaining)} label={`Error budget remaining ${remaining ?? 'unknown'}%`} />
                            <span>{r.error_budget.spent_failures} of {r.error_budget.allowed_failures} allowed failures spent</span>
                        </MetricTile>
                        <MetricTile label="Burn rate" value={fmtRate(r.error_budget.burn_rate)}>
                            <span>Last 24 h: {fmtRate(r.burn_rate_24h)}. Above 1× spends the budget before the window ends.</span>
                        </MetricTile>
                        <MetricTile label="Latency p95" value={fmtMs(r.latency.p95_ms)}
                            pill={r.latency.met === null ? undefined : <Pill tone={r.latency.met ? 'mint' : 'peach'}>{r.latency.met ? 'Within target' : 'Over target'}</Pill>}>
                            {r.latency.missing ? <span>{r.latency.missing}</span>
                                : <span>{r.latency.target_ms ? `Target ${fmtMs(r.latency.target_ms)}` : 'No latency target'} · {r.latency.checks_with_latency} checks with a duration</span>}
                        </MetricTile>
                    </div>
                    <div className="flex flex-col gap-2">
                        <Eyebrow right={<span className="text-xs">green = at or above target</span>}>Daily compliance</Eyebrow>
                        <BarStrip values={daily} max={100} colours={colours} label={`Daily compliance over ${r.window_days} days`} />
                    </div>
                    {r.regions.length > 0 && (
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[520px] text-sm">
                                <caption className="pb-2 text-left text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">By region</caption>
                                <thead className="text-left text-xs text-ink-soft">
                                    <tr>
                                        <th className="py-1.5 font-medium">Region</th>
                                        <th className="py-1.5 font-medium">Checks</th>
                                        <th className="py-1.5 font-medium">Compliance</th>
                                        <th className="py-1.5 font-medium">Budget left</th>
                                        <th className="py-1.5 font-medium">Burn (24 h)</th>
                                        <th className="py-1.5 font-medium">p95</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-[#F0EEE8]">
                                    {r.regions.map((g) => (
                                        <tr key={g.region}>
                                            <td className="py-2 font-medium">{g.region}</td>
                                            <td className="py-2 tabular-nums">{g.checks}</td>
                                            <td className="py-2 tabular-nums">{fmtPct(g.compliance_pct, 2)}</td>
                                            <td className="py-2 tabular-nums">{g.error_budget.remaining_pct === null ? '—' : `${g.error_budget.remaining_pct}%`}</td>
                                            <td className="py-2 tabular-nums">{fmtRate(g.burn_rate_24h)}</td>
                                            <td className="py-2 tabular-nums">{g.latency.p95_ms === null ? 'No latency data' : fmtMs(g.latency.p95_ms)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
            {editing && <TargetsForm report={r} projectId={projectId} />}
        </section>
    );
}

export default function SloReports() {
    const projectId = useActiveProjectId();
    const [windowDays, setWindowDays] = useState('');

    const { data, isLoading, error } = useQuery({
        queryKey: ['project-slo', projectId, windowDays],
        queryFn: () => reliabilityApi.projectSlo(projectId!, windowDays ? Number(windowDays) : null),
        enabled: !!projectId,
    });

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="SLO reports"
                subtitle="Availability and latency of each monitor against its target, with the error budget left and how fast it is burning."
                toolbar={projectId ? (
                    <LabelledSelect id="slo-window" label="Window" value={windowDays} onChange={setWindowDays}>
                        <option value="">Each monitor's own window</option>
                        <option value="7">Last 7 days</option>
                        <option value="30">Last 30 days</option>
                        <option value="90">Last 90 days</option>
                    </LabelledSelect>
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its monitors' SLOs.</EmptyState>
            ) : isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : error ? (
                <EmptyState title="Couldn't load SLO reports">{errorDetail(error)}</EmptyState>
            ) : data && data.monitors.length === 0 ? (
                <EmptyState title="No monitors">{data.missing}</EmptyState>
            ) : data ? (
                <>
                    {data.monitors.map((m) => <MonitorCard key={m.schedule_id} r={m} projectId={projectId} />)}
                    <Panel title="How this is measured">
                        Each monitor check is one event: good when its run passed. The error budget is the share of checks the target allows to fail;
                        a burn rate of 1× uses it up exactly at the end of the window. Latency is the check's run duration.
                    </Panel>
                </>
            ) : null}
        </div>
    );
}
