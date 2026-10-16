import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { reliabilityApi, type DoraReport } from '@/api/reliability';
import { EmptyState, PageHeader, Panel, Pill } from '@/components/redesign/primitives';
import { LabelledSelect, MetricTile, WindowSelect } from '@/components/reports/ReportControls';
import { BAND_LABEL, CAUSE_LABEL, bandTone, fmtDateTime, fmtHours, fmtPct } from '@/components/reports/chartMath';
import { errorDetail } from '@/components/flakes/flakeHelpers';

function BandPill({ band }: { band: string | null }) {
    if (!band) return <Pill tone="neutral">No band</Pill>;
    return <Pill tone={bandTone(band)}>{BAND_LABEL[band] ?? band}</Pill>;
}

function Thresholds({ t }: { t?: Record<string, string> }) {
    if (!t) return null;
    return (
        <details className="text-xs">
            <summary className="cursor-pointer text-ink-soft">Band thresholds</summary>
            <ul className="mt-1 flex flex-col gap-0.5">
                {(['elite', 'high', 'medium', 'low'] as const).map((b) => (
                    <li key={b}><span className="font-semibold text-ink">{BAND_LABEL[b]}:</span> {t[b]}</li>
                ))}
            </ul>
        </details>
    );
}

function Tiles({ r }: { r: DoraReport }) {
    const f = r.deployment_frequency;
    const l = r.lead_time;
    const c = r.change_failure_rate;
    const t = r.time_to_restore;
    return (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
            <MetricTile label="Deployment frequency" value={f.deployments ? `${f.per_week} / week` : '—'} pill={<BandPill band={f.band} />}>
                <span>{f.deployments} deployment{f.deployments === 1 ? '' : 's'} in {r.days} days ({f.per_day} per day)</span>
                {f.missing && <span className="text-ink">{f.missing}</span>}
                <Thresholds t={r.thresholds.deployment_frequency} />
            </MetricTile>
            <MetricTile label="Lead time for changes" value={fmtHours(l.median_hours)} pill={<BandPill band={l.band} />}>
                <span>Median of {l.samples} deployment{l.samples === 1 ? '' : 's'} · {l.sources.commit_at} from commit time, {l.sources.first_run} from the first test run</span>
                {l.missing && <span className="text-ink">{l.missing}</span>}
                <Thresholds t={r.thresholds.lead_time} />
            </MetricTile>
            <MetricTile label="Change failure rate" value={fmtPct(c.percent)} pill={<BandPill band={c.band} />}>
                <span>{c.failed_deployments} of {c.deployments} deployments · {c.causes.incident} by incident, {c.causes.failed_run} by failed run, {c.causes.deployment_failed} failed deploys</span>
                {c.missing && <span className="text-ink">{c.missing}</span>}
                <Thresholds t={r.thresholds.change_failure_rate} />
            </MetricTile>
            <MetricTile label="Time to restore" value={fmtHours(t.median_hours)} pill={<BandPill band={t.band} />}>
                <span>Median of {t.resolved_incidents} resolved incident{t.resolved_incidents === 1 ? '' : 's'} · {t.unresolved_incidents} still open</span>
                {t.missing && <span className="text-ink">{t.missing}</span>}
                <Thresholds t={r.thresholds.time_to_restore} />
            </MetricTile>
        </div>
    );
}

function DeploymentsTable({ r }: { r: DoraReport }) {
    if (r.deployments.length === 0) return null;
    return (
        <section aria-label="Recent deployments" className="overflow-hidden rounded-3xl border border-border bg-white">
            <h2 className="border-b border-[#F0EEE8] px-5 py-3 text-sm font-semibold text-ink">Recent deployments</h2>
            <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                    <thead className="text-left text-xs text-ink-soft">
                        <tr>
                            <th className="px-5 py-2 font-medium">Deployed</th>
                            <th className="px-3 py-2 font-medium">Environment</th>
                            <th className="px-3 py-2 font-medium">Version / commit</th>
                            <th className="px-3 py-2 font-medium">Lead time</th>
                            <th className="px-5 py-2 font-medium">Outcome</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-[#F0EEE8]">
                        {r.deployments.map((d) => (
                            <tr key={d.id}>
                                <td className="px-5 py-2.5 tabular-nums">{fmtDateTime(d.deployed_at)}</td>
                                <td className="px-3 py-2.5">{d.environment ?? 'default'}</td>
                                <td className="px-3 py-2.5">
                                    <span className="flex flex-col">
                                        {d.version && <span>{d.version}</span>}
                                        {d.git_commit && <span className="font-mono text-xs text-ink-soft">{d.git_commit.slice(0, 12)}</span>}
                                        {!d.version && !d.git_commit && <span className="text-ink-soft">—</span>}
                                    </span>
                                </td>
                                <td className="px-3 py-2.5 tabular-nums">
                                    {fmtHours(d.lead_time_hours)}
                                    {d.lead_time_source && <span className="block text-xs text-ink-soft">{d.lead_time_source === 'commit_at' ? 'from commit time' : 'from first test run'}</span>}
                                </td>
                                <td className="px-5 py-2.5">
                                    {d.failure_causes.length === 0
                                        ? <Pill tone="mint">No failure seen</Pill>
                                        : (
                                            <span className="flex flex-wrap gap-1">
                                                {d.failure_causes.map((c) => <Pill key={c} tone="peach">{CAUSE_LABEL[c] ?? c}</Pill>)}
                                            </span>
                                        )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </section>
    );
}

function HowToSend({ projectId }: { projectId: number }) {
    const origin = typeof window !== 'undefined' ? window.location.origin : 'https://traceiq.example.com';
    const snippet = `curl -X POST "${origin}/api/projects/${projectId}/deployments" \\
  -H "X-API-Key: $TRACEIQ_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "git_commit": "'"$GITHUB_SHA"'",
    "environment": "production",
    "version": "v1.4.2",
    "external_id": "'"$GITHUB_RUN_ID"'",
    "status": "success"
  }'`;
    return (
        <Panel title="How to send deployments">
            <p className="text-sm text-ink-soft">
                Call this from your CI pipeline after each deployment, with an API key scoped to this project. A repeated
                <span className="font-mono"> external_id </span> updates the earlier record instead of counting twice. Send
                <span className="font-mono"> commit_at </span> if you know when the change was committed; otherwise lead time starts at the first test run with that commit.
            </p>
            <pre className="mt-2 overflow-x-auto rounded-2xl border border-border bg-white p-4 font-mono text-xs leading-relaxed text-ink">{snippet}</pre>
        </Panel>
    );
}

export default function DeliveryReport() {
    const projectId = useActiveProjectId();
    const [days, setDays] = useState(30);
    const [env, setEnv] = useState('');
    const environmentId = env ? Number(env) : null;

    const { data: envs } = useQuery({
        queryKey: ['environments', projectId],
        queryFn: () => reliabilityApi.environments(projectId!),
        enabled: !!projectId,
    });
    const { data, isLoading, error } = useQuery({
        queryKey: ['dora', projectId, days, environmentId],
        queryFn: () => reliabilityApi.dora(projectId!, days, environmentId),
        enabled: !!projectId,
    });

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Delivery (DORA)"
                subtitle="Deployment frequency, lead time, change failure rate and time to restore, from the deployments your CI reports."
                toolbar={projectId ? (
                    <>
                        <WindowSelect id="dora-window" value={days} options={[7, 30, 90, 180]} onChange={setDays} />
                        <LabelledSelect id="dora-env" label="Environment" value={env} onChange={setEnv}>
                            <option value="">All environments</option>
                            {(envs ?? []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                        </LabelledSelect>
                    </>
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its delivery metrics.</EmptyState>
            ) : isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : error ? (
                <EmptyState title="Couldn't load delivery metrics">{errorDetail(error)}</EmptyState>
            ) : data ? (
                <>
                    {data.inputs.deployments === 0 && (
                        <EmptyState title="No deployments recorded">
                            Nothing has been reported for this window{data.environment ? ` in ${data.environment}` : ''}. Send deployment events from CI (below) to get DORA metrics.
                        </EmptyState>
                    )}
                    <Tiles r={data} />
                    <Panel title="Inputs used">
                        <span className="text-sm">
                            {data.inputs.deployments} deployments, {data.inputs.incidents} incidents, {data.inputs.failed_runs} failed runs, {data.inputs.commits_with_test_runs} commits matched to test runs.
                        </span>
                        <ul className="mt-1 list-disc pl-5 text-xs text-ink-soft">
                            {data.inputs.notes.map((n) => <li key={n}>{n}</li>)}
                        </ul>
                    </Panel>
                    <DeploymentsTable r={data} />
                    <HowToSend projectId={projectId} />
                </>
            ) : null}
        </div>
    );
}
