import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link2, Printer } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getWorkspaces, type Workspace } from '@/lib/api';
import { pocApi, type PocQuery } from '@/api/pocReport';
import { shareLinksApi, type CreatedShareLink } from '@/api/reports';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';
import { SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { CheckRow, FieldRow, SettingsCard, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { CreatedLink, ExpirySelect, LinkRows } from '@/components/reports/ShareLinks';
import { PocReportView } from '@/components/poc/PocReportView';
import { assumptionError, defaultRange, normaliseCurrency } from '@/components/poc/pocHelpers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const selectClass = 'h-10 w-full rounded-full border border-input bg-white px-4 text-sm text-ink';

// A proof-of-concept / trial report: setup, activity, what was found and a
// labelled time-saved estimate. Printable (the app shell hides under print
// CSS) and shareable as a read-only link.
export default function PocReport() {
    const qc = useQueryClient();
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const wsId = selectedWs ?? workspaces?.[0]?.id ?? null;

    const initial = defaultRange();
    const [start, setStart] = useState(initial.start);
    const [end, setEnd] = useState(initial.end);
    const [picked, setPicked] = useState<number[]>([]);
    const [minutes, setMinutes] = useState('5');
    const [rate, setRate] = useState('');
    const [currency, setCurrency] = useState('');
    // The query only changes on "Update report", so typing does not refetch.
    const [applied, setApplied] = useState<PocQuery | null>(null);

    const m = parseFloat(minutes);
    const r = rate.trim() === '' ? null : parseFloat(rate);
    const invalid = assumptionError(Number.isFinite(m) ? m : 0, r != null && !Number.isFinite(r) ? -1 : r, currency);
    const draft: PocQuery = { start, end, projectIds: picked, minutes: m, hourlyRate: r, currency };
    const query = applied ?? { ...initial, projectIds: [], minutes: 5, hourlyRate: null, currency: '' };

    const report = useQuery({
        queryKey: ['poc-report', wsId, query],
        queryFn: () => pocApi.get(wsId!, query),
        enabled: !!wsId,
    });
    const data = report.data;

    const [expires, setExpires] = useState(30);
    const [label, setLabel] = useState('');
    const [created, setCreated] = useState<CreatedShareLink | null>(null);
    const linksKey = ['share-links', 'poc', wsId];
    const links = useQuery({ queryKey: linksKey, queryFn: () => shareLinksApi.listWorkspace(wsId!, 'poc_report'), enabled: !!wsId });
    const share = useMutation({
        mutationFn: () => shareLinksApi.createWorkspace(wsId!, {
            kind: 'poc_report', expires_in_days: expires, label: label.trim() || undefined,
            params: {
                start: query.start, end: query.end, project_ids: query.projectIds,
                minutes_per_execution: query.minutes, hourly_rate: query.hourlyRate,
                currency: query.currency || undefined,
            },
        }),
        onSuccess: (l) => { setCreated(l); setLabel(''); qc.invalidateQueries({ queryKey: linksKey }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const revoke = useMutation({
        mutationFn: (id: number) => shareLinksApi.revoke(id),
        onSuccess: () => { toast.success('Link revoked'); qc.invalidateQueries({ queryKey: linksKey }); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const toggle = (id: number, on: boolean) => setPicked((p) => (on ? [...p, id] : p.filter((x) => x !== id)));

    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="PoC report"
                subtitle={data
                    ? `${data.workspace_name} · ${data.start} to ${data.end}`
                    : 'What a trial set up, ran and found, with a labelled estimate of the manual effort it replaced.'}
                actions={<Button variant="outline" onClick={() => window.print()} disabled={!data}><Printer className="h-4 w-4" /> Print or save as PDF</Button>}
            />

            <div className="print:hidden">
                <SettingsCard
                    labelledBy="poc-inputs"
                    title="Scope and assumptions"
                    description="The estimate is arithmetic on your assumptions: automated case executions × minutes a person would need for each. Manual-executor runs are not counted."
                    footer={<>
                        <Button onClick={() => setApplied(draft)} disabled={!!invalid || !wsId}>Update report</Button>
                        {invalid && <span className="text-sm text-ink-soft" role="alert">{invalid}</span>}
                    </>}
                >
                    <div className="flex flex-col">
                        {workspaces && workspaces.length > 1 && (
                            <FieldRow label="Workspace" htmlFor="poc-ws">
                                <select id="poc-ws" className={selectClass} value={wsId ?? ''}
                                    onChange={(e) => { setSelectedWs(parseInt(e.target.value)); setPicked([]); }}>
                                    {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                                </select>
                            </FieldRow>
                        )}
                        <FieldRow label="From" htmlFor="poc-start">
                            <Input id="poc-start" type="date" className={inputClass} value={start} onChange={(e) => setStart(e.target.value)} />
                        </FieldRow>
                        <FieldRow label="To" htmlFor="poc-end" hint="Inclusive. At most 366 days.">
                            <Input id="poc-end" type="date" className={inputClass} value={end} onChange={(e) => setEnd(e.target.value)} />
                        </FieldRow>
                        <FieldRow label="Minutes per manual execution" htmlFor="poc-min" hint="How long a person takes to run one case by hand. Default 5.">
                            <Input id="poc-min" type="number" min={0.5} max={480} step={0.5} className={inputClass} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
                        </FieldRow>
                        <FieldRow label="Hourly rate (optional)" htmlFor="poc-rate" hint="Adds an estimate in money.">
                            <Input id="poc-rate" type="number" min={0} step={1} className={inputClass} value={rate} onChange={(e) => setRate(e.target.value)} />
                        </FieldRow>
                        <FieldRow label="Currency code" htmlFor="poc-cur" hint="Three letters, for example EUR. Needed with a rate.">
                            <Input id="poc-cur" className={inputClass} maxLength={3} value={currency} placeholder="EUR"
                                onChange={(e) => setCurrency(normaliseCurrency(e.target.value))} />
                        </FieldRow>
                    </div>
                    {data && data.available_projects.length > 0 && (
                        <fieldset className="flex flex-col border-t border-border pt-3">
                            <legend className="mb-1 text-sm font-semibold text-ink">Projects</legend>
                            <p className="mb-1 text-xs text-ink-soft">None ticked = every project you can access in this workspace.</p>
                            {data.available_projects.map((p) => (
                                <CheckRow key={p.id} id={`poc-p-${p.id}`} label={p.name} checked={picked.includes(p.id)} onChange={(v) => toggle(p.id, v)} />
                            ))}
                        </fieldset>
                    )}
                </SettingsCard>
            </div>

            {!wsId ? (
                <EmptyState title="No workspace">Join or create a workspace first.</EmptyState>
            ) : report.isLoading ? (
                <SkeletonRows count={4} />
            ) : report.isError || !data ? (
                <EmptyState title="Could not load the report">{errDetail(report.error)}</EmptyState>
            ) : (
                <>
                    <PocReportView data={{ ...data, kind: 'poc_report' }} />
                    <p className="text-xs text-ink-soft">Covers the {data.project_ids.length} project(s) you have access to{query.projectIds.length ? ' among those selected' : ''}.</p>
                    <div className="print:hidden">
                        <SettingsCard
                            labelledBy="poc-share"
                            icon={<Link2 className="h-4 w-4" />}
                            title="Share a read-only link"
                            description="Freezes this range, project selection and assumptions; the numbers are recomputed when the link is opened, over projects where you are an editor. Counts and names only: no error messages, URLs or email addresses."
                            footer={<Button onClick={() => share.mutate()} disabled={share.isPending}>Create link</Button>}
                        >
                            <div className="flex flex-col">
                                <FieldRow label="Expires after" htmlFor="poc-expiry" hint="At most 90 days. You can revoke it any time.">
                                    <ExpirySelect id="poc-expiry" value={expires} onChange={setExpires} />
                                </FieldRow>
                                <FieldRow label="Label" htmlFor="poc-label" hint="Optional, for example who it is for." wide>
                                    <Input id="poc-label" className={inputClass} maxLength={120} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Trial review with Bigco" />
                                </FieldRow>
                            </div>
                            {created && <CreatedLink link={created} />}
                            <div className="flex flex-col border-t border-border pt-3">
                                <h3 className="mb-1 text-sm font-semibold text-ink">PoC report links</h3>
                                {links.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                                    : <LinkRows links={links.data || []} onRevoke={(id) => revoke.mutate(id)} pending={revoke.isPending} />}
                            </div>
                        </SettingsCard>
                    </div>
                </>
            )}
        </div>
    );
}
