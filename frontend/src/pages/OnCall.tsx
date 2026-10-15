import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    AlertCircle, BellRing, CalendarOff, Globe2, Pencil, Plus, RefreshCw, Send, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
    oncallApi, MaintenanceWindow, MaintenanceWindowInput, PagingChannel, PagingChannelInput,
    NetworkPolicyView, PagingKind, Severity,
} from '@/api/oncall';
import { isoToLocalInput, localInputToIso, parseHostList, WEEKDAYS } from '@/lib/fleet';

type ApiErr = { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
const errDetail = (err: unknown): string => {
    const e = err as ApiErr | null;
    const d = e?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (d) return JSON.stringify(d);
    return e?.message || 'Unknown error';
};
const TH = 'font-bold text-slate-500 uppercase tracking-widest text-xs h-11';
const fmt = (iso: string | null) => iso ? new Date(/[zZ]$/.test(iso) ? iso : `${iso}Z`).toLocaleString() : '—';

function Card({ title, icon: Icon, children, action }: { title: string; icon: React.ComponentType<{ className?: string }>; children: React.ReactNode; action?: React.ReactNode }) {
    return (
        <section className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm mb-6">
            <div className="flex items-center justify-between mb-4">
                <h2 className="font-bold text-slate-800 flex items-center gap-2"><Icon className="w-4 h-4 text-indigo-500" /> {title}</h2>
                {action}
            </div>
            {children}
        </section>
    );
}

// ---------------------------------------------------------------------------
// Paging channels
// ---------------------------------------------------------------------------

const EMPTY_CHANNEL: PagingChannelInput = {
    name: '', kind: 'pagerduty', secret: '', api_region: 'us', severity: 'error',
    on_monitor: true, on_run_failure: false, enabled: true,
};

function ChannelForm({ initial, editing, onSubmit, onCancel, busy }: {
    initial: PagingChannelInput; editing: PagingChannel | null; busy: boolean;
    onSubmit: (v: PagingChannelInput) => void; onCancel: () => void;
}) {
    const [v, setV] = useState<PagingChannelInput>(initial);
    const set = (patch: Partial<PagingChannelInput>) => setV((p) => ({ ...p, ...patch }));
    return (
        <div className="border border-indigo-200 rounded-xl p-4 mb-4 space-y-3">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
                <div>
                    <Label htmlFor="ch-name">Name</Label>
                    <Input id="ch-name" value={v.name} onChange={(e) => set({ name: e.target.value })} placeholder="Checkout on-call" />
                </div>
                <div>
                    <Label>Service</Label>
                    <Select value={v.kind} onValueChange={(k) => set({ kind: k as PagingKind })}>
                        <SelectTrigger aria-label="Paging service"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="pagerduty">PagerDuty (Events API v2)</SelectItem>
                            <SelectItem value="opsgenie">Opsgenie (Alerts API)</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div>
                    <Label htmlFor="ch-secret">{v.kind === 'pagerduty' ? 'Routing key' : 'API key'}</Label>
                    <Input id="ch-secret" type="password" autoComplete="new-password" value={v.secret || ''}
                        onChange={(e) => set({ secret: e.target.value })}
                        placeholder={editing?.secret_set ? '•••••• key set — type to replace' : 'required'} />
                </div>
                <div>
                    <Label>Severity</Label>
                    <Select value={v.severity} onValueChange={(s) => set({ severity: s as Severity })}>
                        <SelectTrigger aria-label="Severity"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            {(['critical', 'error', 'warning', 'info'] as Severity[]).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                        </SelectContent>
                    </Select>
                </div>
                {v.kind === 'opsgenie' && (
                    <div>
                        <Label>Opsgenie region</Label>
                        <Select value={v.api_region || 'us'} onValueChange={(r) => set({ api_region: r })}>
                            <SelectTrigger aria-label="Opsgenie region"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="us">US (api.opsgenie.com)</SelectItem>
                                <SelectItem value="eu">EU (api.eu.opsgenie.com)</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                )}
            </div>
            <div className="flex flex-wrap gap-5 text-sm">
                <label className="flex items-center gap-2"><Checkbox checked={v.on_monitor} onCheckedChange={(c) => set({ on_monitor: c === true })} /> Page on monitor DOWN (resolve on recovery)</label>
                <label className="flex items-center gap-2"><Checkbox checked={v.on_run_failure} onCheckedChange={(c) => set({ on_run_failure: c === true })} /> Page on failed scheduled / CI runs</label>
                <label className="flex items-center gap-2"><Checkbox checked={v.enabled} onCheckedChange={(c) => set({ enabled: c === true })} /> Enabled</label>
            </div>
            <div className="flex gap-2">
                <Button size="sm" disabled={busy} onClick={() => {
                    if (!v.name.trim()) { toast.error('Name is required'); return; }
                    if (!editing && !v.secret?.trim()) { toast.error('The routing / API key is required'); return; }
                    onSubmit({ ...v, secret: v.secret?.trim() || null });
                }}>{editing ? 'Save channel' : 'Add channel'}</Button>
                <Button size="sm" variant="outline" onClick={onCancel} disabled={busy}>Cancel</Button>
            </div>
        </div>
    );
}

function ChannelsCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [form, setForm] = useState<null | { editing: PagingChannel | null }>(null);
    const list = useQuery({ queryKey: ['paging-channels', projectId], queryFn: () => oncallApi.channels(projectId) });
    const refresh = () => qc.invalidateQueries({ queryKey: ['paging-channels', projectId] });
    const save = useMutation({
        mutationFn: (v: PagingChannelInput) => form?.editing
            ? oncallApi.updateChannel(projectId, form.editing.id, v)
            : oncallApi.createChannel(projectId, v),
        onSuccess: () => { toast.success('Paging channel saved'); setForm(null); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: (id: number) => oncallApi.deleteChannel(projectId, id),
        onSuccess: () => { toast.success('Channel deleted'); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const test = useMutation({
        mutationFn: (id: number) => oncallApi.testChannel(projectId, id),
        onSuccess: (r) => r.ok ? toast.success('Test page sent and resolved') : toast.error(`Test failed: ${r.detail}`),
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <Card title="Paging channels" icon={BellRing} action={!form && (
            <Button size="sm" onClick={() => setForm({ editing: null })}><Plus className="w-4 h-4 mr-1" /> Add channel</Button>
        )}>
            {form && (
                <ChannelForm
                    key={form.editing?.id ?? 'new'}
                    editing={form.editing}
                    busy={save.isPending}
                    initial={form.editing ? {
                        name: form.editing.name, kind: form.editing.kind, secret: '',
                        api_region: form.editing.api_region || 'us', severity: form.editing.severity,
                        on_monitor: form.editing.on_monitor, on_run_failure: form.editing.on_run_failure,
                        enabled: form.editing.enabled,
                    } : EMPTY_CHANNEL}
                    onSubmit={(v) => save.mutate(v)}
                    onCancel={() => setForm(null)}
                />
            )}
            {list.isLoading ? <RefreshCw className="animate-spin w-5 h-5 text-slate-400" /> : list.error ? (
                <p className="text-sm text-rose-600">{errDetail(list.error)}</p>
            ) : !list.data?.length ? (
                <p className="text-sm text-slate-500">No paging channels. Monitors still alert Slack/Teams/email; add PagerDuty or Opsgenie to wake someone up.</p>
            ) : (
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader><TableRow className="hover:bg-transparent">
                            <TableHead className={TH}>Channel</TableHead>
                            <TableHead className={TH}>Pages on</TableHead>
                            <TableHead className={TH}>Last sent</TableHead>
                            <TableHead className={`${TH} text-right`}>Actions</TableHead>
                        </TableRow></TableHeader>
                        <TableBody>
                            {list.data.map((c) => (
                                <TableRow key={c.id}>
                                    <TableCell className="py-3">
                                        <div className="font-semibold text-slate-800 text-sm">{c.name}
                                            {!c.enabled && <Badge variant="outline" className="ml-2 text-[10px]">disabled</Badge>}</div>
                                        <div className="text-xs text-slate-400">{c.kind}{c.api_region ? ` (${c.api_region})` : ''} · {c.severity} · {c.secret_set ? 'key set' : 'no key'}</div>
                                    </TableCell>
                                    <TableCell className="py-3 text-xs text-slate-600">
                                        {[c.on_monitor && 'monitors', c.on_run_failure && 'run failures'].filter(Boolean).join(', ') || 'nothing'}
                                    </TableCell>
                                    <TableCell className="py-3 text-xs">
                                        <div className="text-slate-500">{fmt(c.last_sent_at)}</div>
                                        {c.last_error && <div className="text-rose-600 max-w-xs break-words">{c.last_error}</div>}
                                    </TableCell>
                                    <TableCell className="py-3 text-right whitespace-nowrap">
                                        <Button size="sm" variant="outline" className="h-8 mr-1" disabled={test.isPending} onClick={() => test.mutate(c.id)}>
                                            <Send className="w-3.5 h-3.5 mr-1" /> Test
                                        </Button>
                                        <Button size="sm" variant="outline" className="h-8 mr-1" onClick={() => setForm({ editing: c })}>
                                            <Pencil className="w-3.5 h-3.5 mr-1" /> Edit
                                        </Button>
                                        <Button size="sm" variant="outline" className="h-8 text-rose-600" disabled={remove.isPending}
                                            onClick={() => window.confirm(`Delete paging channel "${c.name}"?`) && remove.mutate(c.id)}>
                                            <Trash2 className="w-3.5 h-3.5 mr-1" /> Delete
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Maintenance windows
// ---------------------------------------------------------------------------

const browserTz = (() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
})();

interface WindowDraft {
    name: string; mode: 'once' | 'weekly'; starts: string; ends: string;
    weekdays: number[]; start_time: string; end_time: string; timezone: string; enabled: boolean;
}

function draftFrom(w: MaintenanceWindow | null): WindowDraft {
    if (!w) return { name: '', mode: 'once', starts: '', ends: '', weekdays: [], start_time: '02:00', end_time: '04:00', timezone: browserTz, enabled: true };
    return {
        name: w.name, mode: w.weekdays?.length ? 'weekly' : 'once',
        starts: isoToLocalInput(w.starts_at), ends: isoToLocalInput(w.ends_at),
        weekdays: w.weekdays || [], start_time: w.start_time || '02:00', end_time: w.end_time || '04:00',
        timezone: w.timezone || 'UTC', enabled: w.enabled,
    };
}

function toInput(d: WindowDraft): MaintenanceWindowInput {
    if (d.mode === 'once') {
        return { name: d.name, starts_at: localInputToIso(d.starts), ends_at: localInputToIso(d.ends), timezone: 'UTC', enabled: d.enabled };
    }
    return { name: d.name, weekdays: d.weekdays, start_time: d.start_time, end_time: d.end_time, timezone: d.timezone, enabled: d.enabled };
}

function WindowForm({ initial, busy, onSubmit, onCancel }: {
    initial: WindowDraft; busy: boolean; onSubmit: (v: MaintenanceWindowInput) => void; onCancel: () => void;
}) {
    const [d, setD] = useState<WindowDraft>(initial);
    const set = (patch: Partial<WindowDraft>) => setD((p) => ({ ...p, ...patch }));
    return (
        <div className="border border-indigo-200 rounded-xl p-4 mb-4 space-y-3">
            <div className="grid sm:grid-cols-3 gap-3">
                <div>
                    <Label htmlFor="mw-name">Name</Label>
                    <Input id="mw-name" value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="Sunday deploys" />
                </div>
                <div>
                    <Label>Type</Label>
                    <Select value={d.mode} onValueChange={(m) => set({ mode: m as WindowDraft['mode'] })}>
                        <SelectTrigger aria-label="Window type"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="once">One-off</SelectItem>
                            <SelectItem value="weekly">Weekly recurring</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <label className="flex items-center gap-2 text-sm mt-6"><Checkbox checked={d.enabled} onCheckedChange={(c) => set({ enabled: c === true })} /> Enabled</label>
            </div>
            {d.mode === 'once' ? (
                <div className="grid sm:grid-cols-2 gap-3">
                    <div>
                        <Label htmlFor="mw-start">Starts (your local time)</Label>
                        <Input id="mw-start" type="datetime-local" value={d.starts} onChange={(e) => set({ starts: e.target.value })} />
                    </div>
                    <div>
                        <Label htmlFor="mw-end">Ends (your local time)</Label>
                        <Input id="mw-end" type="datetime-local" value={d.ends} onChange={(e) => set({ ends: e.target.value })} />
                    </div>
                </div>
            ) : (
                <>
                    <fieldset className="flex flex-wrap gap-4 text-sm">
                        <legend className="text-sm font-medium mb-1">Days</legend>
                        {WEEKDAYS.map((label, i) => (
                            <label key={label} className="flex items-center gap-1.5">
                                <Checkbox checked={d.weekdays.includes(i)} onCheckedChange={(c) => set({
                                    weekdays: c === true ? [...d.weekdays, i].sort() : d.weekdays.filter((x) => x !== i),
                                })} /> {label}
                            </label>
                        ))}
                    </fieldset>
                    <div className="grid sm:grid-cols-3 gap-3">
                        <div>
                            <Label htmlFor="mw-st">From</Label>
                            <Input id="mw-st" type="time" value={d.start_time} onChange={(e) => set({ start_time: e.target.value })} />
                        </div>
                        <div>
                            <Label htmlFor="mw-et">Until</Label>
                            <Input id="mw-et" type="time" value={d.end_time} onChange={(e) => set({ end_time: e.target.value })} />
                        </div>
                        <div>
                            <Label htmlFor="mw-tz">Timezone</Label>
                            <Input id="mw-tz" value={d.timezone} onChange={(e) => set({ timezone: e.target.value })} placeholder="Europe/Berlin" />
                        </div>
                    </div>
                    <p className="text-xs text-slate-400">An end before the start wraps past midnight (e.g. Fri 22:00 → Sat 06:00).</p>
                </>
            )}
            <div className="flex gap-2">
                <Button size="sm" disabled={busy} onClick={() => {
                    if (!d.name.trim()) { toast.error('Name is required'); return; }
                    onSubmit(toInput(d));
                }}>Save window</Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>
            </div>
        </div>
    );
}

function describeWindow(w: MaintenanceWindow): string {
    if (w.weekdays?.length) {
        return `${w.weekdays.map((d) => WEEKDAYS[d]).join(', ')} ${w.start_time}–${w.end_time} (${w.timezone})`;
    }
    return `${fmt(w.starts_at)} → ${fmt(w.ends_at)}`;
}

function WindowsCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [form, setForm] = useState<null | { editing: MaintenanceWindow | null }>(null);
    const list = useQuery({ queryKey: ['maintenance-windows', projectId], queryFn: () => oncallApi.windows(projectId), refetchInterval: 60000 });
    const refresh = () => qc.invalidateQueries({ queryKey: ['maintenance-windows', projectId] });
    const save = useMutation({
        mutationFn: (v: MaintenanceWindowInput) => form?.editing
            ? oncallApi.updateWindow(projectId, form.editing.id, v)
            : oncallApi.createWindow(projectId, v),
        onSuccess: () => { toast.success('Maintenance window saved'); setForm(null); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: (id: number) => oncallApi.deleteWindow(projectId, id),
        onSuccess: () => { toast.success('Window deleted'); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <Card title="Maintenance windows" icon={CalendarOff} action={!form && (
            <Button size="sm" onClick={() => setForm({ editing: null })}><Plus className="w-4 h-4 mr-1" /> Add window</Button>
        )}>
            <p className="text-xs text-slate-500 mb-3">During a window pages are suppressed but still recorded below. Slack, Teams and email alerts are unaffected.</p>
            {form && (
                <WindowForm key={form.editing?.id ?? 'new'} initial={draftFrom(form.editing)} busy={save.isPending}
                    onSubmit={(v) => save.mutate(v)} onCancel={() => setForm(null)} />
            )}
            {list.isLoading ? <RefreshCw className="animate-spin w-5 h-5 text-slate-400" /> : list.error ? (
                <p className="text-sm text-rose-600">{errDetail(list.error)}</p>
            ) : !list.data?.length ? (
                <p className="text-sm text-slate-500">No maintenance windows.</p>
            ) : (
                <ul className="divide-y divide-slate-100">
                    {list.data.map((w) => (
                        <li key={w.id} className="py-2.5 flex items-center gap-3">
                            <div className="flex-1 min-w-0">
                                <div className="font-semibold text-sm text-slate-800">
                                    {w.name}
                                    {w.active_now && <Badge variant="outline" className="ml-2 text-[10px] bg-amber-50 text-amber-700 border-amber-200">active now</Badge>}
                                    {!w.enabled && <Badge variant="outline" className="ml-2 text-[10px]">disabled</Badge>}
                                </div>
                                <div className="text-xs text-slate-400">{describeWindow(w)}</div>
                            </div>
                            <Button size="sm" variant="outline" className="h-8" onClick={() => setForm({ editing: w })}><Pencil className="w-3.5 h-3.5 mr-1" /> Edit</Button>
                            <Button size="sm" variant="outline" className="h-8 text-rose-600" disabled={remove.isPending}
                                onClick={() => window.confirm(`Delete window "${w.name}"?`) && remove.mutate(w.id)}>
                                <Trash2 className="w-3.5 h-3.5 mr-1" /> Delete
                            </Button>
                        </li>
                    ))}
                </ul>
            )}
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Paging events
// ---------------------------------------------------------------------------

function EventsCard({ projectId }: { projectId: number }) {
    const list = useQuery({ queryKey: ['paging-events', projectId], queryFn: () => oncallApi.events(projectId), refetchInterval: 30000 });
    const tone: Record<string, string> = {
        sent: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        suppressed: 'bg-amber-50 text-amber-700 border-amber-200',
        failed: 'bg-rose-50 text-rose-700 border-rose-200',
    };
    return (
        <Card title="Recent paging decisions" icon={BellRing}>
            {list.isLoading ? <RefreshCw className="animate-spin w-5 h-5 text-slate-400" /> : !list.data?.length ? (
                <p className="text-sm text-slate-500">Nothing paged yet.</p>
            ) : (
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader><TableRow className="hover:bg-transparent">
                            <TableHead className={TH}>When</TableHead>
                            <TableHead className={TH}>Source</TableHead>
                            <TableHead className={TH}>Action</TableHead>
                            <TableHead className={TH}>Dedup key</TableHead>
                            <TableHead className={TH}>Outcome</TableHead>
                        </TableRow></TableHeader>
                        <TableBody>
                            {list.data.map((e) => (
                                <TableRow key={e.id}>
                                    <TableCell className="py-2 text-xs whitespace-nowrap">{fmt(e.created_at)}</TableCell>
                                    <TableCell className="py-2 text-xs">{e.source}{e.run_id ? ` · run #${e.run_id}` : ''}</TableCell>
                                    <TableCell className="py-2 text-xs">{e.action}</TableCell>
                                    <TableCell className="py-2"><code className="text-[11px]">{e.dedup_key}</code></TableCell>
                                    <TableCell className="py-2 text-xs">
                                        <Badge variant="outline" className={`text-[10px] ${tone[e.outcome] || ''}`}>{e.outcome}</Badge>
                                        {e.detail && <div className="text-slate-400 mt-0.5 max-w-xs break-words">{e.detail}</div>}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Egress allowlist
// ---------------------------------------------------------------------------

function EgressForm({ projectId, view }: { projectId: number; view: NetworkPolicyView }) {
    const qc = useQueryClient();
    const s = view.stored;
    const [enforce, setEnforce] = useState(!!s?.enforce);
    const [allowPrivate, setAllowPrivate] = useState(!!s?.allow_private);
    const [hosts, setHosts] = useState((s?.allowed_hosts || []).join('\n'));
    const save = useMutation({
        mutationFn: () => oncallApi.setNetworkPolicy(projectId, {
            enforce, allow_private: allowPrivate, allowed_hosts: parseHostList(hosts),
        }),
        onSuccess: (data) => { qc.setQueryData(['network-policy', projectId], data); toast.success('Egress policy saved'); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const eff = view.effective;
    return (
        <div className="grid lg:grid-cols-2 gap-6">
            <div className="space-y-3">
                <label className="flex items-center gap-2 text-sm"><Checkbox checked={enforce} onCheckedChange={(c) => setEnforce(c === true)} /> Enforce for this project</label>
                <label className="flex items-center gap-2 text-sm"><Checkbox checked={allowPrivate} onCheckedChange={(c) => setAllowPrivate(c === true)} /> Allow private / internal addresses</label>
                <div>
                    <Label htmlFor="egress-hosts">Allowed hosts (one per line)</Label>
                    <textarea id="egress-hosts" value={hosts} onChange={(e) => setHosts(e.target.value)}
                        className="w-full min-h-28 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                        placeholder={'shop.example.com\ncdn.example.com'} />
                </div>
                <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save egress policy</Button>
            </div>
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-sm">
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Effective at dispatch</div>
                {!eff ? (
                    <p className="text-slate-500">Not enforced — workers behave as before.</p>
                ) : (
                    <>
                        <p className="text-slate-700 mb-1">Enforced{!s?.enforce && ' (forced by the instance setting)'}.</p>
                        <p className="text-slate-700 mb-2">
                            Private addresses: {eff.allow_private ? 'allowed' : 'blocked'}
                            {s?.allow_private && !eff.allow_private && ' — capped by the instance (private targets are not allowed on this instance)'}
                        </p>
                        <div className="flex flex-wrap gap-1">
                            {eff.allowed_hosts.length ? eff.allowed_hosts.map((h) => (
                                <code key={h} className="text-[11px] bg-white border border-slate-200 px-1.5 py-0.5 rounded">{h}</code>
                            )) : <span className="text-slate-500">no hosts — every request is blocked</span>}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

function EgressCard({ projectId }: { projectId: number }) {
    const q = useQuery({ queryKey: ['network-policy', projectId], queryFn: () => oncallApi.networkPolicy(projectId) });
    return (
        <Card title="Worker egress allowlist" icon={Globe2}>
            <p className="text-xs text-slate-500 mb-3">
                When enforced, the browser running this project's tests may only reach the listed hosts (and their
                subdomains). Everything else is blocked and shows up as a blocked request in the run's network
                evidence. Link-local / cloud-metadata addresses are always blocked. The environment's base URL is
                allowed automatically.
            </p>
            {q.isLoading ? <RefreshCw className="animate-spin w-5 h-5 text-slate-400" /> : q.error || !q.data ? (
                <p className="text-sm text-rose-600">{errDetail(q.error)}</p>
            ) : (
                // Keyed on the saved policy so the form re-initialises after a save
                // instead of syncing props into state from an effect.
                <EgressForm key={JSON.stringify(q.data.stored)} projectId={projectId} view={q.data} />
            )}
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OnCall() {
    const [projectId, setProjectId] = useState<number | null>(() => {
        const s = localStorage.getItem('activeProjectId');
        return s ? parseInt(s) : null;
    });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Operations</span>
                </div>
                <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">On-call</h1>
                <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                    Page PagerDuty or Opsgenie when monitors go down or unattended runs fail, silence paging during
                    maintenance, and limit what this project's test browsers may reach. Everyone on the project can
                    view this page; changes need the project admin role.
                </p>
            </div>
            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
                    <p className="text-slate-500 text-sm">Pick a project in the sidebar.</p>
                </div>
            ) : (
                <>
                    <ChannelsCard projectId={projectId} />
                    <WindowsCard projectId={projectId} />
                    <EventsCard projectId={projectId} />
                    <EgressCard projectId={projectId} />
                </>
            )}
        </div>
    );
}
