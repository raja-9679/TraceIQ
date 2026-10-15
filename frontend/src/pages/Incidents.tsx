import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    Siren, RefreshCw, AlertCircle, Layers, ExternalLink, Plus, KeyRound, Copy, Repeat, ShieldAlert, Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects, getTestSuites } from '@/lib/api';
import {
    incidentsApi, Incident, IncidentCreate, SEVERITY_TONE, webhookUrl, listProjectCases,
} from '@/api/incidents';

const errDetail = (e: unknown): string => {
    const err = e as { response?: { data?: { detail?: string } }; message?: string } | null;
    return err?.response?.data?.detail || err?.message || 'Unknown error';
};
type SuiteOption = { id: number; name: string };
const fmt = (s: string | null) => (s ? new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString() : '—');
const NONE = '__none__';

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
    if (cfg.error) return <p className="text-sm text-slate-500">{errDetail(cfg.error)}</p>;
    if (!cfg.data) return null;
    const c = cfg.data;
    const url = webhookUrl(c.path);
    const copy = (t: string) => { navigator.clipboard?.writeText(t); toast.success('Copied'); };

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-3">
            <h3 className="font-bold text-slate-800 flex items-center gap-2"><KeyRound className="w-4 h-4 text-slate-500" /> Inbound webhook</h3>
            <div className="flex flex-wrap items-center gap-2 text-sm">
                <code className="bg-slate-100 rounded px-2 py-1 text-xs break-all">POST {url}</code>
                <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => copy(url)}><Copy className="w-3 h-3 mr-1" /> Copy URL</Button>
            </div>
            <p className="text-xs text-slate-500">
                Authenticate with one of: {c.signature_headers.join(' · ')}. Generic JSON, PagerDuty v3 and Opsgenie payloads are
                detected automatically (or add <code>?source=pagerduty|opsgenie|generic</code>).
            </p>
            {secret && (
                <div className="border border-amber-200 bg-amber-50 rounded-lg p-3 text-sm">
                    <div className="font-semibold text-amber-800 mb-1">Copy this secret now — it will not be shown again.</div>
                    <div className="flex items-center gap-2">
                        <code className="text-xs break-all flex-1">{secret}</code>
                        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => copy(secret)}><Copy className="w-3 h-3 mr-1" /> Copy secret</Button>
                    </div>
                </div>
            )}
            <div className="flex flex-wrap items-end gap-3">
                <Button size="sm" className="h-9 rounded-lg" onClick={() => { if (!c.configured || confirm('Rotate the secret? The old one stops working immediately.')) rotate.mutate(); }} disabled={rotate.isPending}>
                    {c.configured ? 'Rotate secret' : 'Create webhook secret'}
                </Button>
                {c.configured && (
                    <>
                        <div>
                            <label htmlFor="wh-suite" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Regression proposals go to</label>
                            <Select value={c.default_suite_id?.toString() ?? NONE} onValueChange={(v) => update.mutate({ default_suite_id: v === NONE ? null : parseInt(v) })}>
                                <SelectTrigger id="wh-suite" className="h-9 mt-1 w-[220px]"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={NONE}>Matched case's suite / first suite</SelectItem>
                                    {((suites.data || []) as SuiteOption[]).map((s) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <label className="flex items-center gap-2 text-xs text-slate-600 h-9">
                            <Checkbox checked={c.enabled} onCheckedChange={(v) => update.mutate({ enabled: !!v })} /> Enabled
                        </label>
                        <Button size="sm" variant="outline" className="h-9 text-rose-600" onClick={() => { if (confirm('Delete the webhook?')) remove.mutate(); }}>
                            <Trash2 className="w-4 h-4 mr-1" /> Delete webhook
                        </Button>
                        <span className="text-xs text-slate-400">Last received: {fmt(c.last_received_at)}</span>
                    </>
                )}
            </div>
        </div>
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
        <div>
            <label htmlFor={`inc-${id}`} className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</label>
            <Input id={`inc-${id}`} className="h-9 mt-1" placeholder={placeholder} value={(f[id] as string) || ''} onChange={set(id)} />
        </div>
    );
    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-3">
            <h3 className="font-bold text-slate-800">New incident</h3>
            {field('title', 'Title', 'Checkout returns 500 for guest users')}
            <div className="grid sm:grid-cols-3 gap-3">
                <div>
                    <label htmlFor="inc-sev" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Severity</label>
                    <Select value={f.severity} onValueChange={(v) => setF({ ...f, severity: v })}>
                        <SelectTrigger id="inc-sev" className="h-9 mt-1"><SelectValue /></SelectTrigger>
                        <SelectContent>{['critical', 'high', 'medium', 'low'].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                    </Select>
                </div>
                {field('service', 'Service', 'checkout-api')}
                {field('affected_url', 'Affected URL', 'https://shop.example.com/checkout')}
            </div>
            <div className="grid sm:grid-cols-4 gap-3">
                {field('url', 'Incident link')}
                {field('git_commit', 'Commit')}
                {field('git_branch', 'Branch')}
                {field('release', 'Release')}
            </div>
            <div className="flex flex-wrap items-end gap-3">
                <label className="flex items-center gap-2 text-xs text-slate-600 h-9">
                    <Checkbox checked={!!f.propose_regression} onCheckedChange={(v) => setF({ ...f, propose_regression: !!v })} /> Propose a regression test
                </label>
                {f.propose_regression && (
                    <div>
                        <label htmlFor="inc-suite" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Into suite</label>
                        <Select value={f.test_suite_id?.toString() ?? NONE} onValueChange={(v) => setF({ ...f, test_suite_id: v === NONE ? undefined : parseInt(v) })}>
                            <SelectTrigger id="inc-suite" className="h-9 mt-1 w-[220px]"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value={NONE}>Matched case's suite / first suite</SelectItem>
                                {((suites.data || []) as SuiteOption[]).map((s) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                )}
                <span className="flex-1" />
                <Button size="sm" className="h-9 rounded-lg" disabled={!f.title.trim() || create.isPending} onClick={() => create.mutate()}>Record incident</Button>
                <Button size="sm" variant="outline" className="h-9 rounded-lg" onClick={onDone}>Cancel</Button>
            </div>
        </div>
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
        <div className="bg-white border border-slate-200 rounded-lg p-3 flex flex-wrap items-end gap-3">
            <div>
                <label htmlFor={`esc-${inc.id}`} className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Escaped defect — should have been caught by</label>
                <Select value={choice} onValueChange={setChoice}>
                    <SelectTrigger id={`esc-${inc.id}`} className="h-9 mt-1 w-[260px]"><SelectValue placeholder="Choose…" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="none">No test existed</SelectItem>
                        {(cases.data || []).map((c) => <SelectItem key={c.id} value={c.id.toString()}>{c.name}</SelectItem>)}
                    </SelectContent>
                </Select>
            </div>
            <div className="flex-1 min-w-[180px]">
                <label htmlFor={`esc-note-${inc.id}`} className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Note</label>
                <Input id={`esc-note-${inc.id}`} className="h-9 mt-1" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why it slipped through" />
            </div>
            <Button size="sm" className="h-9" disabled={!choice || mark.isPending} onClick={() => mark.mutate(true)}>Mark escaped</Button>
            {inc.escaped_defect && <Button size="sm" variant="outline" className="h-9" onClick={() => mark.mutate(false)}>Clear</Button>}
        </div>
    );
}

function IncidentRow({ inc, projectId }: { inc: Incident; projectId: number }) {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
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
        <div className="border border-slate-200 rounded-xl overflow-hidden bg-white">
            <button className="w-full flex items-center gap-3 p-4 hover:bg-slate-50/50 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
                <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase shrink-0 ${SEVERITY_TONE[inc.severity]}`}>{inc.severity}</Badge>
                <div className="min-w-0 flex-1">
                    <div className="font-semibold text-slate-800 text-sm truncate">{inc.title}</div>
                    <div className="text-xs text-slate-400 mt-0.5">
                        {inc.service || 'no service'} · {inc.source} · {fmt(inc.started_at)} · {inc.status}
                        {inc.regression_status && <> · regression: {inc.regression_status}</>}
                    </div>
                </div>
                {inc.recurrence_count > 0 && <Badge variant="outline" className="text-[10px] bg-violet-50 text-violet-700 border-violet-200 shrink-0"><Repeat className="w-3 h-3 mr-1" /> Recurrence #{inc.recurrence_count}</Badge>}
                {inc.escaped_defect && <Badge variant="outline" className="text-[10px] bg-rose-50 text-rose-700 border-rose-200 shrink-0"><ShieldAlert className="w-3 h-3 mr-1" /> Escaped</Badge>}
            </button>
            {open && (
                <div className="border-t border-slate-100 p-4 bg-slate-50/30 space-y-3 text-sm">
                    <div className="grid sm:grid-cols-2 gap-2 text-xs text-slate-600">
                        {inc.url && <div>Incident: <a className="text-indigo-600 hover:underline" href={inc.url} target="_blank" rel="noreferrer">open <ExternalLink className="w-3 h-3 inline" /></a></div>}
                        {inc.affected_url && <div className="truncate">Affected: {inc.affected_url}</div>}
                        {(inc.git_commit || inc.release) && <div>Release: {inc.release || '—'} {inc.git_commit && <code>{inc.git_commit.slice(0, 10)}</code>} {inc.git_branch}</div>}
                        {inc.previous_incident_id && <div>Previous occurrence: incident #{inc.previous_incident_id}</div>}
                        {inc.match_reason && <div>Matched: {inc.matched_case_name && <>case <b>{inc.matched_case_name}</b> </>}{inc.matched_cluster_title && <>cluster <Link className="text-indigo-600 hover:underline" to="/triage">{inc.matched_cluster_title}</Link></>} ({inc.match_reason})</div>}
                        <div>
                            Regression test: {inc.regression_case_name ? <b>{inc.regression_case_name}</b>
                                : inc.regression_proposal_id ? <Link className="text-indigo-600 hover:underline" to="/proposals">proposal #{inc.regression_proposal_id} ({inc.regression_proposal_status})</Link>
                                    : inc.regression_status || 'none'}
                        </div>
                        {inc.escaped_defect && <div>Escaped: {inc.escaped_no_test ? 'no test existed' : <>missed by <b>{inc.escaped_case_name}</b></>}{inc.escaped_note && ` — ${inc.escaped_note}`}</div>}
                    </div>
                    {inc.description && <p className="text-xs text-slate-500 whitespace-pre-wrap">{inc.description}</p>}
                    <div className="flex flex-wrap items-center gap-2">
                        <label htmlFor={`st-${inc.id}`} className="text-xs text-slate-500">Status</label>
                        <Select value={inc.status} onValueChange={(v) => setStatus.mutate(v)}>
                            <SelectTrigger id={`st-${inc.id}`} className="h-8 w-[150px]"><SelectValue /></SelectTrigger>
                            <SelectContent>{['open', 'acknowledged', 'resolved'].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                        </Select>
                        {!inc.regression_case_id && inc.regression_proposal_status !== 'pending' && (
                            <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => propose.mutate()} disabled={propose.isPending}>Propose regression test</Button>
                        )}
                        <span className="flex-1" />
                        <Button size="sm" variant="outline" className="h-8 text-xs text-rose-600" onClick={() => { if (confirm('Delete this incident?')) remove.mutate(); }}><Trash2 className="w-3.5 h-3.5 mr-1" /> Delete</Button>
                    </div>
                    <EscapedForm inc={inc} projectId={projectId} />
                </div>
            )}
        </div>
    );
}

export default function Incidents() {
    const [projectId, setProjectId] = useState<number | null>(() => { const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null; });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => { const id = parseInt(idStr); setProjectId(id); localStorage.setItem('activeProjectId', id.toString()); window.dispatchEvent(new Event('projectChanged')); };
    const [filter, setFilter] = useState<'all' | 'open' | 'escaped'>('all');
    const [creating, setCreating] = useState(false);
    const [showWebhook, setShowWebhook] = useState(false);

    const params = filter === 'open' ? { status: 'open' } : filter === 'escaped' ? { escaped: true } : {};
    const list = useQuery({ queryKey: ['incidents', projectId, filter], queryFn: () => incidentsApi.list(projectId!, params), enabled: !!projectId });
    const report = useQuery({ queryKey: ['escaped-report', projectId], queryFn: () => incidentsApi.report(projectId!, 180), enabled: !!projectId });
    const t = report.data?.totals;

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Incidents</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Incidents</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Every production incident becomes a regression-test proposal. Recurrences are linked to the
                            earlier occurrence, and escaped defects are tracked against the test that should have caught them.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                        <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                            <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200" aria-label="Project"><div className="flex items-center gap-2 min-w-0"><Layers className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Select a project" /></div></SelectTrigger>
                            <SelectContent>{(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl"><AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" /><p className="text-slate-500 text-sm">Select a project.</p></div>
            ) : (
                <div className="space-y-6">
                    {t && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            {[
                                ['Escaped defects (180d)', t.escaped],
                                ['Test added afterwards', t.pct_test_added_after == null ? '—' : `${t.pct_test_added_after}%`],
                                ['Missed by an existing test', t.missed_by_existing_test],
                                ['No test existed', t.no_test_existed],
                            ].map(([label, v]) => (
                                <div key={label as string} className="bg-white border border-slate-200 rounded-2xl p-4">
                                    <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</div>
                                    <div className="text-2xl font-extrabold text-slate-800 tabular-nums mt-1">{v}</div>
                                </div>
                            ))}
                        </div>
                    )}
                    {report.data && report.data.periods.length > 0 && (
                        <div className="bg-white border border-slate-200 rounded-2xl p-4 overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead><tr className="text-left text-[10px] uppercase tracking-widest text-slate-400">
                                    <th className="py-1">Month</th><th>Escaped</th><th>Missed by a test</th><th>No test</th><th>Test added after</th>
                                </tr></thead>
                                <tbody>{report.data.periods.map((p) => (
                                    <tr key={p.period} className="border-t border-slate-100 tabular-nums">
                                        <td className="py-1.5">{p.period}</td><td>{p.escaped}</td><td>{p.missed_by_existing_test}</td><td>{p.no_test_existed}</td>
                                        <td>{p.test_added_after} ({p.pct_test_added_after}%)</td>
                                    </tr>
                                ))}</tbody>
                            </table>
                        </div>
                    )}

                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-bold text-slate-800 flex items-center gap-2"><Siren className="w-4 h-4 text-slate-500" /> Incidents</h3>
                        <div className="flex gap-1 ml-2" role="group" aria-label="Filter incidents">
                            {(['all', 'open', 'escaped'] as const).map((k) => (
                                <Button key={k} size="sm" variant={filter === k ? 'default' : 'outline'} className="h-8 text-xs capitalize" onClick={() => setFilter(k)}>{k}</Button>
                            ))}
                        </div>
                        <span className="flex-1" />
                        <Button size="sm" variant="outline" className="h-9 rounded-lg" onClick={() => setShowWebhook(!showWebhook)}>
                            <KeyRound className="w-4 h-4 mr-1" /> {showWebhook ? 'Hide webhook setup' : 'Webhook setup'}
                        </Button>
                        <Button size="sm" className="h-9 rounded-lg" onClick={() => setCreating(!creating)}><Plus className="w-4 h-4 mr-1" /> New incident</Button>
                    </div>
                    {showWebhook && <WebhookPanel projectId={projectId} />}
                    {creating && <NewIncidentForm projectId={projectId} onDone={() => setCreating(false)} />}

                    {list.isLoading ? <div className="p-8 flex justify-center"><RefreshCw className="animate-spin w-5 h-5 text-slate-400" /></div>
                        : list.error ? <p className="text-sm text-rose-600">{errDetail(list.error)}</p>
                            : !list.data?.length ? (
                                <div className="p-10 text-center bg-white border border-slate-200 rounded-2xl">
                                    <Siren className="w-9 h-9 text-slate-300 mx-auto mb-3" />
                                    <p className="text-slate-500 text-sm">No incidents{filter !== 'all' ? ' match this filter' : ' yet'}.</p>
                                </div>
                            ) : (
                                <div className="space-y-2">{list.data.map((i) => <IncidentRow key={i.id} inc={i} projectId={projectId} />)}</div>
                            )}
                </div>
            )}
        </div>
    );
}
