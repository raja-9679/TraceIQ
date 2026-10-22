import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { reliabilityApi, type VendorHealth } from '@/api/reliability';
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { HBarList, Sparkline } from '@/components/reports/charts';
import { MetricTile, WindowSelect } from '@/components/reports/ReportControls';
import { fmtDateTime, fmtMs, fmtPct } from '@/components/reports/chartMath';
import { errorDetail } from '@/components/flakes/flakeHelpers';

const ERR = '#E0603A';

function VendorDetail({ v, note }: { v: VendorHealth; note: string }) {
    const c = v.correlation;
    return (
        <DetailCard label={`Vendor ${v.name}`}>
            <div className="flex flex-col gap-1.5">
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{v.name}</h2>
                <span className="font-mono text-sm text-ink-soft">{v.vendor}</span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
                <MetricTile label="Requests" value={v.requests.toLocaleString()}><span>in {v.runs} runs</span></MetricTile>
                <MetricTile label="Error rate" value={fmtPct(v.error_rate)}><span>{v.errors} errors · {v.runs_with_errors} runs affected</span></MetricTile>
                <MetricTile label="Latency p50" value={fmtMs(v.latency_p50_ms)} />
                <MetricTile label="Latency p95" value={fmtMs(v.latency_p95_ms)} />
            </div>
            <p className="text-xs text-ink-soft">{note}</p>

            <div className="flex flex-col gap-2">
                <Eyebrow>Errors per day</Eyebrow>
                <Sparkline values={v.daily.map((d) => d.errors)} width={480} height={48} colour={ERR} fill
                    label={`Errors per day for ${v.name} over ${v.daily.length} days`} />
                <span className="flex justify-between text-xs text-ink-soft">
                    <span>{v.daily[0]?.date}</span><span>{v.daily[v.daily.length - 1]?.date}</span>
                </span>
            </div>

            <div className="flex flex-col gap-2">
                <Eyebrow>Does it line up with failing tests?</Eyebrow>
                <HBarList label="Share of results whose run saw errors from this vendor" rows={[
                    { key: 'failed', label: `Failed results (${c.failed_results})`, value: c.failed_with_vendor_errors_pct ?? 0, max: 100, colour: ERR, text: fmtPct(c.failed_with_vendor_errors_pct) },
                    { key: 'passed', label: `Passed results (${c.passed_results})`, value: c.passed_with_vendor_errors_pct ?? 0, max: 100, colour: '#8FD3B6', text: fmtPct(c.passed_with_vendor_errors_pct) },
                ]} />
                <span className="text-sm text-ink">
                    {c.lift === null
                        ? 'Not enough data on both sides to compare.'
                        : c.lift > 1.5
                            ? `Failing results saw errors from this vendor ${c.lift_is_lower_bound ? 'at least ' : ''}${c.lift}× as often as passing ones. Worth a look.`
                            : `Failing and passing results saw its errors about as often (${c.lift_is_lower_bound ? 'at least ' : ''}${c.lift}×). It is unlikely to be the cause.`}
                </span>
            </div>

            {v.hosts.length > 0 && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Hosts seen</Eyebrow>
                    <ul className="flex flex-wrap gap-1.5">
                        {v.hosts.map((h) => <li key={h}><Pill tone="neutral"><span className="font-mono">{h}</span></Pill></li>)}
                    </ul>
                </div>
            )}
        </DetailCard>
    );
}

export default function DependencyHealth() {
    const projectId = useActiveProjectId();
    const [days, setDays] = useState(30);
    const [selected, setSelected] = useState<string | null>(null);

    const { data, isLoading, error } = useQuery({
        queryKey: ['dependency-health', projectId, days],
        queryFn: () => reliabilityApi.dependencyHealth(projectId!, days),
        enabled: !!projectId,
    });
    const vendors = data?.vendors ?? [];
    const current = vendors.find((v) => v.vendor === selected) ?? vendors[0] ?? null;
    const erroring = vendors.filter((v) => v.errors > 0);
    const clean = vendors.filter((v) => v.errors === 0);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Third-party dependencies"
                subtitle={data && vendors.length
                    ? `${vendors.length} third-party vendors seen in test traffic · ${erroring.length} returned errors · updated ${fmtDateTime(data.computed_at)}`
                    : 'Analytics, payments, CDNs and other outside services your tests load: how often they fail, how slow they are, and whether that lines up with failing tests.'}
                toolbar={projectId ? <WindowSelect id="dep-window" value={days} options={[7, 30, 90, 180]} onChange={setDays} /> : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its third-party dependencies.</EmptyState>
            ) : isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : error ? (
                <EmptyState title="Couldn't load dependency health">{errorDetail(error)}</EmptyState>
            ) : vendors.length === 0 ? (
                <EmptyState title="No third-party requests">{data?.missing}</EmptyState>
            ) : (
                <>
                    <SplitView
                        list={[{ name: 'Returned errors', items: erroring }, { name: 'No errors', items: clean }]
                            .filter((g) => g.items.length > 0)
                            .map((g) => (
                                <ListGroup key={g.name} title={g.name} count={g.items.length}>
                                    {g.items.map((v) => (
                                        <ListRow
                                            key={v.vendor}
                                            selected={current?.vendor === v.vendor}
                                            onSelect={() => setSelected(v.vendor)}
                                            title={<span className="break-words">{v.name}</span>}
                                            meta={<span>{v.requests.toLocaleString()} requests · p95 {fmtMs(v.latency_p95_ms)}</span>}
                                            trailing={<span className="font-mono text-[13px] tabular-nums text-ink">{fmtPct(v.error_rate)}</span>}
                                        />
                                    ))}
                                </ListGroup>
                            ))}
                        detail={current && <VendorDetail key={current.vendor} v={current} note={data!.latency_note} />}
                    />
                    <Panel title="How this is built">
                        Rolled up hourly from each run's captured network responses. A request is third-party when its domain differs from the site
                        under test. Only host names are kept; paths and query strings never leave the run. Requests that got no response at all are not counted.
                    </Panel>
                </>
            )}
        </div>
    );
}
