import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Plus, KeyRound, Copy, Repeat, ShieldAlert, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getTestSuites } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    incidentsApi, Incident, IncidentCreate, webhookUrl, listProjectCases,
} from '@/api/incidents';
import {
    DetailCard, EmptyState, Eyebrow, ListCard, ListGroup, ListRow, PageHeader, PanelTabs, Pill, SplitView,
} from '@/components/redesign/primitives';
import { toneClasses, type Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

const errDetail = (e: unknown): string => {
    const err = e as { response?: { data?: { detail?: string } }; message?: string } | null;
    return err?.response?.data?.detail || err?.message || 'Unknown error';
};
type SuiteOption = { id: number; name: string };
const fmt = (s: string | null) => (s ? new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString() : '—');
const NONE = '__none__';

const SEV_TONE: Record<string, Tone> = { critical: 'pink', high: 'peach', medium: 'butter', low: 'neutral', unknown: 'neutral' };
const STATUS_TONE: Record<string, Tone> = { open: 'peach', acknowledged: 'lavender', resolved: 'mint' };
const STATUS_GROUPS = [
    { id: 'open', label: 'Open' },
    { id: 'acknowledged', label: 'Acknowledged' },
    { id: 'resolved', label: 'Resolved' },
] as const;

const linkCls = 'font-medium text-ink underline decoration-ink/30 underline-offset-2 hover:decoration-ink';
const fieldLabel = 'text-[13px] font-semibold text-ink';

function SevPill({ severity }: { severity: string }) {
    return <Pill tone={SEV_TONE[severity] ?? 'neutral'} className="min-w-[64px] justify-center capitalize">{severity}</Pill>;
}

function Field({ id, label, className, children }: { id: string; label: string; className?: string; children: ReactNode }) {
    return (
        <div className={cn('flex flex-col gap-1.5', className)}>
            <label htmlFor={id} className={fieldLabel}>{label}</label>
            {children}
        </div>
    );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">{label}</dt>
            <dd className="text-sm text-ink [overflow-wrap:anywhere]">{children}</dd>
        </div>
    );
}

function WebhookPanel({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const cfg = useQuery({ queryKey: ['incident-webhook', projectId], queryFn: () => incidentsApi.webhook(projectId), retry: false });
    const suites = useQuery({ queryKey: ['suites', projectId], queryFn: () => getTestSuites(projectId) });
    const [secret, setSecret] = useState<string | null>(null);
    const invalidate = () => qc.invalidateQueries({ queryKey: ['incident-webhook', projectId] });
    const rotate = useMutation({
        mutationFn: () => incidentsApi.rotateSecret(projectId),
        onSuccess: (d) => { setSecret(d.secret || null); invalidate(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const update = useMutation({
        mutationFn: (body: { default_suite_id?: number | null; enabled?: boolean }) => incidentsApi.updateWebhook(projectId, body),
        onSuccess: () => { toast.success('Saved'); invalidate(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => incidentsApi.deleteWebhook(projectId),
        onSuccess: () => { toast.success('Webhook removed'); setSecret(null); invalidate(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const shell = (body: ReactNode) => (
        <section aria-label="Inbound webhook" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <h2 className="flex items-center gap-2 text-base font-semibold text-ink"><KeyRound className="h-4 w-4 text-ink-soft" aria-hidden /> Inbound webhook</h2>
            {body}
        </section>
    );
    if (cfg.error) return shell(<p className="text-sm text-ink-soft">{errDetail(cfg.error)}</p>);
    if (!cfg.data) return null;
    const c = cfg.data;
    const url = webhookUrl(c.path);
    const copy = (t: string) => { navigator.clipboard?.writeText(t); toast.success('Copied'); };

    return shell(
        <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
                <code className="break-all rounded-xl bg-secondary px-3 py-1.5 text-xs text-ink">POST {url}</code>
                <Button size="sm" variant="outline" onClick={() => copy(url)}><Copy className="h-3 w-3" aria-hidden /> Copy URL</Button>
            </div>
            <p className="text-sm text-ink-soft">
                Authenticate with one of: {c.signature_headers.join(' · ')}. Generic JSON, PagerDuty v3 and Opsgenie payloads are
                detected automatically (or add <code>?source=pagerduty|opsgenie|generic</code>).
            </p>
            {secret && (
                <div className="flex flex-col gap-2 rounded-2xl border border-border p-4 text-sm shadow-[inset_4px_0_0_#F2C94C]">
                    <div className="font-semibold text-ink">Copy this secret now — it will not be shown again.</div>
                    <div className="flex items-center gap-2">
                        <code className="flex-1 break-all text-xs text-ink">{secret}</code>
                        <Button size="sm" variant="outline" onClick={() => copy(secret)}><Copy className="h-3 w-3" aria-hidden /> Copy secret</Button>
                    </div>
                </div>
            )}
            <div className="flex flex-wrap items-end gap-3">
                <Button onClick={() => { if (!c.configured || confirm('Rotate the secret? The old one stops working immediately.')) rotate.mutate(); }} disabled={rotate.isPending}>
                    {c.configured ? 'Rotate secret' : 'Create webhook secret'}
                </Button>
                {c.configured && (
                    <>
                        <Field id="wh-suite" label="Regression proposals go to">
                            <Select value={c.default_suite_id?.toString() ?? NONE} onValueChange={(v) => update.mutate({ default_suite_id: v === NONE ? null : parseInt(v) })}>
                                <SelectTrigger id="wh-suite" className="h-10 w-[240px] rounded-full"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={NONE}>Matched case's suite / first suite</SelectItem>
                                    {((suites.data || []) as SuiteOption[]).map((s) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </Field>
                        <label className="flex h-10 items-center gap-2 text-sm text-ink">
                            <Checkbox checked={c.enabled} onCheckedChange={(v) => update.mutate({ enabled: !!v })} /> Enabled
                        </label>
                        <Button variant="outline" className={toneClasses.peach.ink} onClick={() => { if (confirm('Delete the webhook?')) remove.mutate(); }}>
                            <Trash2 className="h-4 w-4" aria-hidden /> Delete webhook
                        </Button>
                        <span className="flex h-10 items-center text-xs text-ink-soft">Last received: {fmt(c.last_received_at)}</span>
                    </>
                )}
            </div>
        </>
    );
}

function NewIncidentForm({ projectId, onDone }: { projectId: number; onDone: () => void }) {
    const qc = useQueryClient();
    const suites = useQuery({ queryKey: ['suites', projectId], queryFn: () => getTestSuites(projectId) });
    const [f, setF] = useState<IncidentCreate>({ title: '', severity: 'high', propose_regression: true });
    const set = (k: keyof IncidentCreate) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
    const create = useMutation({
        mutationFn: () => incidentsApi.create(projectId, f),
        onSuccess: (d) => {
            toast.success(d.recurrence_count ? `Recorded — recurrence #${d.recurrence_count}` : 'Incident recorded');
            if (d.regression?.proposal_id) toast.info(`Regression proposal #${d.regression.proposal_id} queued for review`);
            qc.invalidateQueries({ queryKey: ['incidents', projectId] });
            onDone();
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const field = (id: keyof IncidentCreate, label: string, placeholder = '') => (
        <Field id={`inc-${id}`} label={label}>
            <Input id={`inc-${id}`} className="h-10" placeholder={placeholder} value={(f[id] as string) || ''} onChange={set(id)} />
        </Field>
    );
    return (
        <section aria-label="New incident" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <h2 className="text-base font-semibold text-ink">New incident</h2>
            {field('title', 'Title', 'Checkout returns 500 for guest users')}
            <div className="grid gap-3 sm:grid-cols-3">
                <Field id="inc-sev" label="Severity">
                    <Select value={f.severity ?? undefined} onValueChange={(v) => setF({ ...f, severity: v })}>
                        <SelectTrigger id="inc-sev" className="h-10 rounded-full capitalize"><SelectValue /></SelectTrigger>
                        <SelectContent>{['critical', 'high', 'medium', 'low'].map((s) => <SelectItem key={s} value={s} className="capitalize">{s}</SelectItem>)}</SelectContent>
                    </Select>
                </Field>
                {field('service', 'Service', 'checkout-api')}
                {field('affected_url', 'Affected URL', 'https://shop.example.com/checkout')}
            </div>
            <div className="grid gap-3 sm:grid-cols-4">
                {field('url', 'Incident link')}
                {field('git_commit', 'Commit')}
                {field('git_branch', 'Branch')}
                {field('release', 'Release')}
            </div>
            <div className="flex flex-wrap items-end gap-3">
                <label className="flex h-10 items-center gap-2 text-sm text-ink">
                    <Checkbox checked={!!f.propose_regression} onCheckedChange={(v) => setF({ ...f, propose_regression: !!v })} /> Propose a regression test
                </label>
                {f.propose_regression && (
                    <Field id="inc-suite" label="Into suite">
                        <Select value={f.test_suite_id?.toString() ?? NONE} onValueChange={(v) => setF({ ...f, test_suite_id: v === NONE ? undefined : parseInt(v) })}>
                            <SelectTrigger id="inc-suite" className="h-10 w-[240px] rounded-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value={NONE}>Matched case's suite / first suite</SelectItem>
                                {((suites.data || []) as SuiteOption[]).map((s) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </Field>
                )}
                <span className="flex-1" />
                <Button variant="outline" onClick={onDone}>Cancel</Button>
                <Button disabled={!f.title.trim() || create.isPending} onClick={() => create.mutate()}>Record incident</Button>
            </div>
        </section>
    );
}

function EscapedForm({ inc, projectId }: { inc: Incident; projectId: number }) {
    const qc = useQueryClient();
    const cases = useQuery({ queryKey: ['project-cases', projectId], queryFn: () => listProjectCases(projectId) });
    const [choice, setChoice] = useState<string>(inc.escaped_case_id ? inc.escaped_case_id.toString() : inc.escaped_no_test ? 'none' : '');
    const [note, setNote] = useState(inc.escaped_note || '');
    const mark = useMutation({
        mutationFn: (escaped: boolean) => incidentsApi.markEscaped(inc.id, escaped
            ? { escaped, case_id: choice !== 'none' ? parseInt(choice) : undefined, no_test_existed: choice === 'none', note: note || undefined }
            : { escaped: false }),
        onSuccess: () => { toast.success('Saved'); qc.invalidateQueries({ queryKey: ['incidents', projectId] }); qc.invalidateQueries({ queryKey: ['escaped-report', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <div className="flex flex-col gap-3 rounded-2xl border border-border p-4">
            <Eyebrow>Escaped defect</Eyebrow>
            <div className="flex flex-wrap items-end gap-3">
                <Field id={`esc-${inc.id}`} label="Should have been caught by">
                    <Select value={choice} onValueChange={setChoice}>
                        <SelectTrigger id={`esc-${inc.id}`} className="h-10 w-[260px] rounded-full"><SelectValue placeholder="Choose…" /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="none">No test existed</SelectItem>
                            {(cases.data || []).map((c) => <SelectItem key={c.id} value={c.id.toString()}>{c.name}</SelectItem>)}
                        </SelectContent>
                    </Select>
                </Field>
                <Field id={`esc-note-${inc.id}`} label="Note" className="min-w-[180px] flex-1">
                    <Input id={`esc-note-${inc.id}`} className="h-10" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why it slipped through" />
                </Field>
            </div>
            <div className="flex flex-wrap gap-2">
                <Button disabled={!choice || mark.isPending} onClick={() => mark.mutate(true)}>Mark escaped</Button>
                {inc.escaped_defect && <Button variant="outline" onClick={() => mark.mutate(false)}>Clear</Button>}
            </div>
        </div>
    );
}

function IncidentDetail({ inc, projectId }: { inc: Incident; projectId: number }) {
    const qc = useQueryClient();
    const invalidate = () => qc.invalidateQueries({ queryKey: ['incidents', projectId] });
    const setStatus = useMutation({
        mutationFn: (status: string) => incidentsApi.update(inc.id, { status }),
        onSuccess: invalidate, onError: (e) => toast.error(errDetail(e)),
    });
    const propose = useMutation({
        mutationFn: () => incidentsApi.propose(inc.id),
        onSuccess: (d: { proposal_id?: number; status: string }) => { toast.success(d.proposal_id ? `Proposal #${d.proposal_id} queued` : `Not proposed: ${d.status}`); invalidate(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({ mutationFn: () => incidentsApi.remove(inc.id), onSuccess: invalidate, onError: (e) => toast.error(errDetail(e)) });

    return (
        <DetailCard label={`Incident ${inc.title}`}>
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <SevPill severity={inc.severity} />
                    <Pill tone={STATUS_TONE[inc.status] ?? 'neutral'} className="capitalize">{inc.status}</Pill>
                    {inc.recurrence_count > 0 && <Pill tone="lavender"><Repeat className="h-3 w-3" aria-hidden /> Recurrence #{inc.recurrence_count}</Pill>}
                    {inc.escaped_defect && <Pill tone="pink"><ShieldAlert className="h-3 w-3" aria-hidden /> Escaped</Pill>}
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{inc.title}</h2>
                <p className="text-sm text-ink-soft">
                    {inc.service || 'no service'} · {inc.source} · started {fmt(inc.started_at)}
                </p>
            </div>

            <div className="flex flex-wrap items-end gap-2">
                <Field id={`st-${inc.id}`} label="Status">
                    <Select value={inc.status} onValueChange={(v) => setStatus.mutate(v)}>
                        <SelectTrigger id={`st-${inc.id}`} className="h-10 w-[170px] rounded-full capitalize"><SelectValue /></SelectTrigger>
                        <SelectContent>{['open', 'acknowledged', 'resolved'].map((s) => <SelectItem key={s} value={s} className="capitalize">{s}</SelectItem>)}</SelectContent>
                    </Select>
                </Field>
                {!inc.regression_case_id && inc.regression_proposal_status !== 'pending' && (
                    <Button variant="outline" onClick={() => propose.mutate()} disabled={propose.isPending}>Propose regression test</Button>
                )}
                <span className="flex-1" />
                <Button variant="outline" className={toneClasses.peach.ink} onClick={() => { if (confirm('Delete this incident?')) remove.mutate(); }}>
                    <Trash2 className="h-4 w-4" aria-hidden /> Delete
                </Button>
            </div>

            <dl className="grid gap-4 rounded-2xl border border-border p-4 sm:grid-cols-2">
                {inc.url && <Fact label="Incident"><a className={linkCls} href={inc.url} target="_blank" rel="noreferrer">open <ExternalLink className="inline h-3 w-3" aria-hidden /></a></Fact>}
                {inc.affected_url && <Fact label="Affected">{inc.affected_url}</Fact>}
                {(inc.git_commit || inc.release) && (
                    <Fact label="Release">{inc.release || '—'} {inc.git_commit && <code className="font-mono text-xs">{inc.git_commit.slice(0, 10)}</code>} {inc.git_branch}</Fact>
                )}
                {inc.previous_incident_id && <Fact label="Previous occurrence">incident #{inc.previous_incident_id}</Fact>}
                {inc.match_reason && (
                    <Fact label="Matched">
                        {inc.matched_case_name && <>case <b>{inc.matched_case_name}</b> </>}
                        {inc.matched_cluster_title && <>cluster <Link className={linkCls} to="/triage">{inc.matched_cluster_title}</Link></>} ({inc.match_reason})
                    </Fact>
                )}
                <Fact label="Regression test">
                    {inc.regression_case_name ? <b>{inc.regression_case_name}</b>
                        : inc.regression_proposal_id ? <Link className={linkCls} to="/proposals">proposal #{inc.regression_proposal_id} ({inc.regression_proposal_status})</Link>
                            : inc.regression_status || 'none'}
                </Fact>
                {inc.escaped_defect && (
                    <Fact label="Escaped">{inc.escaped_no_test ? 'no test existed' : <>missed by <b>{inc.escaped_case_name}</b></>}{inc.escaped_note && ` — ${inc.escaped_note}`}</Fact>
                )}
            </dl>

            {inc.description && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Description</Eyebrow>
                    <p className="whitespace-pre-wrap text-sm text-ink">{inc.description}</p>
                </div>
            )}

            <EscapedForm inc={inc} projectId={projectId} />
        </DetailCard>
    );
}

function Tile({ label, value }: { label: string; value: ReactNode }) {
    return (
        <div className="flex flex-col gap-2 rounded-3xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[28px] font-bold leading-none tracking-tight tabular-nums text-ink">{value}</span>
        </div>
    );
}

export default function Incidents() {
    const projectId = useActiveProjectId();
    const [filter, setFilter] = useState<'all' | 'open' | 'escaped'>('all');
    const [creating, setCreating] = useState(false);
    const [showWebhook, setShowWebhook] = useState(false);
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const params = filter === 'open' ? { status: 'open' } : filter === 'escaped' ? { escaped: true } : {};
    const list = useQuery({ queryKey: ['incidents', projectId, filter], queryFn: () => incidentsApi.list(projectId!, params), enabled: !!projectId });
    const report = useQuery({ queryKey: ['escaped-report', projectId], queryFn: () => incidentsApi.report(projectId!, 180), enabled: !!projectId });
    const t = report.data?.totals;
    const incidents = list.data ?? [];
    const current = incidents.find((i) => i.id === selectedId) ?? incidents[0] ?? null;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Incidents"
                subtitle={
                    'Every production incident becomes a regression-test proposal. Recurrences are linked to the ' +
                    'earlier occurrence, and escaped defects are tracked against the test that should have caught them.'
                }
                actions={projectId ? (
                    <>
                        <Button variant="outline" onClick={() => setShowWebhook(!showWebhook)} aria-expanded={showWebhook}>
                            <KeyRound className="h-4 w-4" aria-hidden /> {showWebhook ? 'Hide webhook setup' : 'Webhook setup'}
                        </Button>
                        <Button onClick={() => setCreating(!creating)} aria-expanded={creating}>
                            <Plus className="h-4 w-4" aria-hidden /> New incident
                        </Button>
                    </>
                ) : undefined}
                toolbar={projectId ? (
                    <PanelTabs
                        label="Filter incidents"
                        value={filter}
                        onChange={(id) => { setFilter(id); setSelectedId(null); }}
                        tabs={[{ id: 'all', label: 'All' }, { id: 'open', label: 'Open' }, { id: 'escaped', label: 'Escaped' }]}
                    />
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its incidents.</EmptyState>
            ) : (
                <>
                    {showWebhook && <WebhookPanel projectId={projectId} />}
                    {creating && <NewIncidentForm projectId={projectId} onDone={() => setCreating(false)} />}

                    {t && (
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <Tile label="Escaped defects (180d)" value={t.escaped} />
                            <Tile label="Test added afterwards" value={t.pct_test_added_after == null ? '—' : `${t.pct_test_added_after}%`} />
                            <Tile label="Missed by an existing test" value={t.missed_by_existing_test} />
                            <Tile label="No test existed" value={t.no_test_existed} />
                        </div>
                    )}

                    {list.isLoading ? (
                        <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
                    ) : list.error ? (
                        <EmptyState title="Couldn't load incidents">{errDetail(list.error)}</EmptyState>
                    ) : !incidents.length ? (
                        <EmptyState title={`No incidents${filter !== 'all' ? ' match this filter' : ' yet'}`}>
                            {filter === 'all' ? 'Record one with New incident, or connect a paging tool with Webhook setup.' : undefined}
                        </EmptyState>
                    ) : (
                        <SplitView
                            list={STATUS_GROUPS
                                .map((g) => ({ ...g, items: incidents.filter((i) => i.status === g.id) }))
                                .filter((g) => g.items.length > 0)
                                .map((g) => (
                                    <ListGroup key={g.id} title={g.label} count={g.items.length}>
                                        {g.items.map((inc) => (
                                            <ListRow
                                                key={inc.id}
                                                selected={current?.id === inc.id}
                                                onSelect={() => setSelectedId(inc.id)}
                                                leading={<SevPill severity={inc.severity} />}
                                                title={<span className="break-words">{inc.title}</span>}
                                                meta={
                                                    <span>
                                                        {inc.service || 'no service'} · {inc.source} · {fmt(inc.started_at)}
                                                        {inc.regression_status && <> · regression: {inc.regression_status}</>}
                                                    </span>
                                                }
                                                trailing={(inc.recurrence_count > 0 || inc.escaped_defect) ? (
                                                    <span className="flex flex-col items-end gap-1">
                                                        {inc.recurrence_count > 0 && <Pill tone="lavender"><Repeat className="h-3 w-3" aria-hidden /> #{inc.recurrence_count}<span className="sr-only"> recurrence</span></Pill>}
                                                        {inc.escaped_defect && <Pill tone="pink"><ShieldAlert className="h-3 w-3" aria-hidden /> Escaped</Pill>}
                                                    </span>
                                                ) : undefined}
                                            />
                                        ))}
                                    </ListGroup>
                                ))}
                            detail={current && <IncidentDetail key={current.id} inc={current} projectId={projectId} />}
                        />
                    )}

                    {report.data && report.data.periods.length > 0 && (
                        <ListCard title="Escaped defects by month">
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-left text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                                            <th className="px-4 py-2.5">Month</th><th className="py-2.5 pr-3">Escaped</th><th className="py-2.5 pr-3">Missed by a test</th>
                                            <th className="py-2.5 pr-3">No test</th><th className="py-2.5 pr-4">Test added after</th>
                                        </tr>
                                    </thead>
                                    <tbody>{report.data.periods.map((p) => (
                                        <tr key={p.period} className="border-t border-[#F0EEE8] tabular-nums text-ink">
                                            <td className="px-4 py-2">{p.period}</td><td className="py-2 pr-3">{p.escaped}</td><td className="py-2 pr-3">{p.missed_by_existing_test}</td>
                                            <td className="py-2 pr-3">{p.no_test_existed}</td>
                                            <td className="py-2 pr-4">{p.test_added_after} ({p.pct_test_added_after}%)</td>
                                        </tr>
                                    ))}</tbody>
                                </table>
                            </div>
                        </ListCard>
                    )}
                </>
            )}
        </div>
    );
}
