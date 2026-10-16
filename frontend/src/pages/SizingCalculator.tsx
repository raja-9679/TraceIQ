import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageHeader, Pill } from '@/components/redesign/primitives';
import { CheckRow, RowGroup, SettingsCard, inputClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { errorText, sizingApi, type CaptureLevel, type SizingInput } from '@/api/solution';

const DEFAULTS: SizingInput = {
    executions_per_day: 2000, avg_duration_s: 60, peak_hour_share: 0.25, matrix_factor: 1,
    largest_suite_cases: 25, target_max_queue_wait_s: 300, capture_level: 'standard', failure_rate: 0.05,
    retention_days: 90, mobile_emulator: false, mobile_workers: 1, ha: false,
};

const LEVELS: { id: CaptureLevel; label: string }[] = [
    { id: 'none', label: 'None — results only' },
    { id: 'minimal', label: 'Minimal — screenshots' },
    { id: 'standard', label: 'Standard — + console, network, visual diffs' },
    { id: 'full', label: 'Full — + trace, video on failure' },
];

function NumberField({ id, label, hint, value, onChange, step = 1, min = 0, suffix }: {
    id: string; label: string; hint?: string; value: number; onChange: (v: number) => void;
    step?: number; min?: number; suffix?: string;
}) {
    // Stacked (label above a full-width input): the Workload card is a narrow
    // column, and FieldRow's side-by-side layout squeezed the labels into a
    // few characters per line.
    return (
        <div className="flex flex-col gap-1.5 py-1.5">
            <label htmlFor={id} className="text-sm font-medium text-ink">{label}</label>
            {hint && <p className="text-xs leading-relaxed text-ink-soft">{hint}</p>}
            <div className="flex items-center gap-2">
                <Input id={id} type="number" inputMode="decimal" className={`${inputClass} w-full`} value={Number.isFinite(value) ? value : ''}
                    step={step} min={min} onChange={(e) => onChange(parseFloat(e.target.value))} />
                {suffix && <span className="flex-none text-xs text-ink-soft">{suffix}</span>}
            </div>
        </div>
    );
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
    return (
        <div className="flex min-w-0 flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[26px] font-bold leading-tight tracking-tight text-ink tabular-nums">{value}</span>
            {sub && <span className="text-xs text-ink-soft">{sub}</span>}
        </div>
    );
}

function Snippet({ title, text }: { title: string; text: string }) {
    return (
        <div className="flex min-w-0 flex-col gap-2">
            <h3 className="text-sm font-semibold text-ink">{title}</h3>
            <pre className="overflow-x-auto rounded-xl border border-border bg-background p-3 font-mono text-xs leading-relaxed text-ink">{text}</pre>
        </div>
    );
}

/** Deployment sizing estimate with every formula and assumption shown (decision C6). */
export default function SizingCalculator() {
    const [input, setInput] = useState<SizingInput>(DEFAULTS);
    const [debounced, setDebounced] = useState<SizingInput>(DEFAULTS);
    useEffect(() => {
        const t = setTimeout(() => setDebounced(input), 300);
        return () => clearTimeout(t);
    }, [input]);
    const valid = Object.values(debounced).every((v) => typeof v !== 'number' || Number.isFinite(v));
    const estimate = useQuery({
        queryKey: ['sizing', debounced],
        queryFn: () => sizingApi.estimate(debounced),
        enabled: valid,
        placeholderData: keepPreviousData,
        retry: false,
    });
    const set = <K extends keyof SizingInput>(k: K) => (v: SizingInput[K]) => setInput((p) => ({ ...p, [k]: v }));
    const out = estimate.data;
    const r = out?.results;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Sizing calculator"
                subtitle="Estimate browser workers, control-plane sizing and storage for a TraceIQ deployment. Every number below shows the formula and the assumption it rests on."
                badge={<Pill tone="lavender">Estimate</Pill>}
                actions={estimate.isFetching ? <span className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Recalculating…</span> : undefined}
            />

            <div className="flex flex-wrap items-start gap-5">
                <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-5 lg:max-w-[460px]">
                    <SettingsCard title="Workload" labelledBy="sizing-workload"
                        description="Test case executions before the browser/device matrix is applied.">
                        <div className="flex flex-col">
                            <NumberField id="sz-exec" label="Executions per day" value={input.executions_per_day} onChange={set('executions_per_day')} />
                            <NumberField id="sz-dur" label="Average test duration" value={input.avg_duration_s} onChange={set('avg_duration_s')} suffix="s" />
                            <NumberField id="sz-peak" label="Peak-hour share" hint="Fraction of the day's executions in the busiest hour (0–1)." step={0.05}
                                value={input.peak_hour_share} onChange={set('peak_hour_share')} />
                            <NumberField id="sz-matrix" label="Browsers × devices per test" value={input.matrix_factor} onChange={set('matrix_factor')} min={1} />
                            <NumberField id="sz-burst" label="Largest suite (cases)" hint="Cases dispatched at once by your biggest run." value={input.largest_suite_cases}
                                onChange={set('largest_suite_cases')} min={1} />
                            <NumberField id="sz-wait" label="Target max queue wait" value={input.target_max_queue_wait_s} onChange={set('target_max_queue_wait_s')} suffix="s" />
                        </div>
                    </SettingsCard>
                    <SettingsCard title="Storage and options" labelledBy="sizing-storage">
                        <div className="flex flex-col gap-1.5 py-1.5">
                            <label htmlFor="sz-capture" className="text-sm font-medium text-ink">Capture level</label>
                            <p className="text-xs leading-relaxed text-ink-soft">What each run may store (project data policy).</p>
                            <select id="sz-capture" value={input.capture_level} onChange={(e) => set('capture_level')(e.target.value as CaptureLevel)}
                                className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm text-ink focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                                {LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                            </select>
                        </div>
                        <div className="flex flex-col">
                            <NumberField id="sz-fail" label="Failure rate" hint="0–1; videos are kept on failure only." step={0.01} value={input.failure_rate} onChange={set('failure_rate')} />
                            <NumberField id="sz-ret" label="Retention" hint="RUN_RETENTION_DAYS" value={input.retention_days} onChange={set('retention_days')} min={1} suffix="days" />
                        </div>
                        <RowGroup>
                            <CheckRow id="sz-ha" label="High availability" hint="N+1 workers, two replicas of the control plane, a Postgres standby."
                                checked={input.ha} onChange={set('ha')} />
                            <CheckRow id="sz-mobile" label="Android emulators" hint="Mobile workers with a local emulator each (~4 GB)."
                                checked={input.mobile_emulator} onChange={set('mobile_emulator')} />
                            {input.mobile_emulator && (
                                <NumberField id="sz-mw" label="Mobile workers" value={input.mobile_workers} onChange={set('mobile_workers')} />
                            )}
                        </RowGroup>
                    </SettingsCard>
                </div>

                <div className="flex min-w-0 flex-[999_1_480px] flex-col gap-5">
                    {estimate.isError && <div className={warnCalloutClass}>{errorText(estimate.error, 'Check the inputs: the estimate could not be calculated.')}</div>}
                    {r && out && (
                        <>
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                                <Stat label="Browser workers" value={r.workers} sub={`${r.worker_cpu} CPU · ${r.worker_ram_gb} GB`} />
                                <Stat label="Object store" value={`${r.object_store_gb} GB`} sub={`${r.artifact_kb_per_job} KB per job · ${input.retention_days} days`} />
                                <Stat label="Postgres" value={`${r.postgres.size_gb} GB`} sub={`${r.postgres.cpu} CPU · ${r.postgres.ram_gb} GB RAM`} />
                                <Stat label="Whole deployment" value={`${r.total_cpu} CPU`} sub={`${r.total_ram_gb} GB RAM${r.single_host ? ' · fits one host' : ''}`} />
                            </div>
                            {r.recommended_host && (r.recommended_host.cpu > r.total_cpu || r.recommended_host.ram_gb > r.total_ram_gb) && (
                                <div className={warnCalloutClass}>
                                    A single-host install still wants at least {r.recommended_host.cpu} CPU / {r.recommended_host.ram_gb} GB (SELF_HOSTING.md).
                                </div>
                            )}

                            <SettingsCard title="Components" labelledBy="sizing-components" description="Per-replica figures; totals above.">
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader><TableRow><TableHead>Component</TableHead><TableHead className="text-right">Replicas</TableHead><TableHead className="text-right">CPU each</TableHead><TableHead className="text-right">RAM each (GB)</TableHead></TableRow></TableHeader>
                                        <TableBody>
                                            {r.components.map((c) => (
                                                <TableRow key={c.name}>
                                                    <TableCell className="font-medium">{c.name}</TableCell>
                                                    <TableCell className="text-right tabular-nums">{c.replicas}</TableCell>
                                                    <TableCell className="text-right tabular-nums">{c.cpu}</TableCell>
                                                    <TableCell className="text-right tabular-nums">{c.ram_gb}</TableCell>
                                                </TableRow>
                                            ))}
                                            <TableRow>
                                                <TableCell className="font-medium">Redis memory</TableCell>
                                                <TableCell colSpan={3} className="text-right tabular-nums">{r.redis_mb} MB</TableCell>
                                            </TableRow>
                                        </TableBody>
                                    </Table>
                                </div>
                                {r.mobile && <p className="text-xs text-ink-soft">{r.mobile.note}</p>}
                            </SettingsCard>

                            <SettingsCard title="Deploy it" labelledBy="sizing-deploy" description="Docker Compose and Helm values for this estimate.">
                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                    <Snippet title="Compose (.env)" text={out.compose.env} />
                                    <Snippet title="Helm values (with KEDA)" text={out.helm.values_yaml} />
                                </div>
                                <p className="text-xs leading-relaxed text-ink-soft">
                                    KEDA scales workers on the Redis stream backlog: lagCount {out.helm.lag_count} means one more replica per {out.helm.lag_count} undelivered jobs, so the last job of a backlog starts within the target wait. Without KEDA, set <code className="font-mono">executionWorker.replicas: {out.helm.replicas}</code>.
                                </p>
                            </SettingsCard>

                            <SettingsCard title="How each number is calculated" labelledBy="sizing-formulas">
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader><TableRow><TableHead>Quantity</TableHead><TableHead>Formula</TableHead><TableHead>With your inputs</TableHead><TableHead className="text-right">Result</TableHead></TableRow></TableHeader>
                                        <TableBody>
                                            {out.formulas.map((f) => (
                                                <TableRow key={f.key}>
                                                    <TableCell className="min-w-[180px] font-medium">{f.label}</TableCell>
                                                    <TableCell className="min-w-[200px] font-mono text-xs">{f.formula}</TableCell>
                                                    <TableCell className="min-w-[180px] font-mono text-xs text-ink-soft">{f.substituted}</TableCell>
                                                    <TableCell className="whitespace-nowrap text-right tabular-nums">{f.value} {f.unit}</TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            </SettingsCard>

                            <SettingsCard title="Assumptions" labelledBy="sizing-assumptions" description="Replace the declared figures with your own measurements where you have them.">
                                <ul className="flex flex-col divide-y divide-border">
                                    {out.assumptions.map((a) => (
                                        <li key={a.key} className="flex flex-col gap-0.5 py-2.5">
                                            <span className="text-sm text-ink">{a.text}</span>
                                            <span className="text-xs text-ink-soft">Source: {a.source}</span>
                                        </li>
                                    ))}
                                </ul>
                            </SettingsCard>
                        </>
                    )}
                    {!r && estimate.isLoading && (
                        <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
                    )}
                </div>
            </div>
        </div>
    );
}
