import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Send, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    DetailCard, EmptyState, Eyebrow, ListCard, ListRow, PageHeader, PanelTabs, Pill, SplitView,
} from '@/components/redesign/primitives';
import { Fact, FactGrid, PanelNote, SectionPanel } from '@/components/redesign-local/ops/bits';
import { dangerOutline, tbl } from '@/components/redesign-local/ops/look';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    oncallApi, MaintenanceWindow, MaintenanceWindowInput, PagingChannel, PagingChannelInput,
    NetworkPolicyView, PagingKind, Severity,
} from '@/api/oncall';
import type { Tone } from '@/components/redesign/kinds';
import { isoToLocalInput, localInputToIso, parseHostList, WEEKDAYS } from '@/lib/fleet';

type ApiErr = { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
const errDetail = (err: unknown): string => {
    const e = err as ApiErr | null;
    const d = e?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (d) return JSON.stringify(d);
    return e?.message || 'Unknown error';
};
const fmt = (iso: string | null) => iso ? new Date(/[zZ]$/.test(iso) ? iso : `${iso}Z`).toLocaleString() : '—';

const Loading = () => <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />;

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
        <DetailCard label={editing ? `Edit channel ${editing.name}` : 'New paging channel'}>
            <h2 className="text-[22px] font-bold leading-tight tracking-[-0.02em] text-ink">{editing ? `Edit ${editing.name}` : 'New paging channel'}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="ch-name">Name</Label>
                    <Input id="ch-name" value={v.name} onChange={(e) => set({ name: e.target.value })} placeholder="Checkout on-call" />
                </div>
                <div className="flex flex-col gap-1.5">
                    <Label>Service</Label>
                    <Select value={v.kind} onValueChange={(k) => set({ kind: k as PagingKind })}>
                        <SelectTrigger aria-label="Paging service"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="pagerduty">PagerDuty (Events API v2)</SelectItem>
                            <SelectItem value="opsgenie">Opsgenie (Alerts API)</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="ch-secret">{v.kind === 'pagerduty' ? 'Routing key' : 'API key'}</Label>
                    <Input id="ch-secret" type="password" autoComplete="new-password" value={v.secret || ''}
                        onChange={(e) => set({ secret: e.target.value })}
                        placeholder={editing?.secret_set ? '•••••• key set — type to replace' : 'required'} />
                </div>
                <div className="flex flex-col gap-1.5">
                    <Label>Severity</Label>
                    <Select value={v.severity} onValueChange={(s) => set({ severity: s as Severity })}>
                        <SelectTrigger aria-label="Severity"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            {(['critical', 'error', 'warning', 'info'] as Severity[]).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                        </SelectContent>
                    </Select>
                </div>
                {v.kind === 'opsgenie' && (
                    <div className="flex flex-col gap-1.5">
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
            <div className="flex flex-col gap-2.5 rounded-2xl bg-background p-4 text-sm text-ink">
                <label className="flex items-center gap-2"><Checkbox checked={v.on_monitor} onCheckedChange={(c) => set({ on_monitor: c === true })} /> Page on monitor DOWN (resolve on recovery)</label>
                <label className="flex items-center gap-2"><Checkbox checked={v.on_run_failure} onCheckedChange={(c) => set({ on_run_failure: c === true })} /> Page on failed scheduled / CI runs</label>
                <label className="flex items-center gap-2"><Checkbox checked={v.enabled} onCheckedChange={(c) => set({ enabled: c === true })} /> Enabled</label>
            </div>
            <div className="flex flex-wrap gap-2">
                <Button disabled={busy} onClick={() => {
                    if (!v.name.trim()) { toast.error('Name is required'); return; }
                    if (!editing && !v.secret?.trim()) { toast.error('The routing / API key is required'); return; }
                    onSubmit({ ...v, secret: v.secret?.trim() || null });
                }}>{editing ? 'Save channel' : 'Add channel'}</Button>
                <Button variant="outline" onClick={onCancel} disabled={busy}>Cancel</Button>
            </div>
        </DetailCard>
    );
}

function ChannelDetail({ c, onEdit, onTest, onDelete, testing, deleting }: {
    c: PagingChannel; onEdit: () => void; onTest: () => void; onDelete: () => void; testing: boolean; deleting: boolean;
}) {
    return (
        <DetailCard label={`Paging channel ${c.name}`}>
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={c.enabled ? 'mint' : 'neutral'}>{c.enabled ? 'Enabled' : 'Disabled'}</Pill>
                    <span className="text-[13px] text-ink-soft">{c.kind === 'pagerduty' ? 'PagerDuty' : 'Opsgenie'}</span>
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{c.name}</h2>
            </div>
            <div className="flex flex-wrap gap-2">
                <Button disabled={testing} onClick={onTest}><Send /> Send test page</Button>
                <Button variant="outline" onClick={onEdit}><Pencil /> Edit</Button>
                <Button variant="outline" className={dangerOutline} disabled={deleting} onClick={onDelete}><Trash2 /> Delete</Button>
            </div>
            <FactGrid>
                <Fact label="Service">{c.kind}{c.api_region ? ` (${c.api_region})` : ''}</Fact>
                <Fact label="Severity">{c.severity}</Fact>
                <Fact label="Key">{c.secret_set ? 'key set' : 'no key'}</Fact>
                <Fact label="Pages on">{[c.on_monitor && 'monitors', c.on_run_failure && 'run failures'].filter(Boolean).join(', ') || 'nothing'}</Fact>
                <Fact label="Last sent">{fmt(c.last_sent_at)}</Fact>
            </FactGrid>
            {c.last_error && (
                <div className="flex flex-col gap-2 rounded-2xl border border-border p-4 shadow-[inset_4px_0_0_#E0603A]">
                    <Eyebrow>Last error</Eyebrow>
                    <p className="break-words text-sm text-ink">{c.last_error}</p>
                </div>
            )}
        </DetailCard>
    );
}

function ChannelsCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [form, setForm] = useState<null | { editing: PagingChannel | null }>(null);
    const [selected, setSelected] = useState<number | null>(null);
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

    if (list.isLoading) return <Loading />;
    if (list.error) return <EmptyState title="Couldn't load paging channels">{errDetail(list.error)}</EmptyState>;
    const channels = list.data ?? [];
    const current = channels.find((c) => c.id === selected) ?? channels[0] ?? null;

    const detail = form ? (
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
    ) : current ? (
        <ChannelDetail
            key={current.id}
            c={current}
            testing={test.isPending}
            deleting={remove.isPending}
            onTest={() => test.mutate(current.id)}
            onEdit={() => setForm({ editing: current })}
            onDelete={() => window.confirm(`Delete paging channel "${current.name}"?`) && remove.mutate(current.id)}
        />
    ) : (
        <EmptyState title="No paging channels">
            Monitors still alert Slack/Teams/email; add PagerDuty or Opsgenie to wake someone up.
        </EmptyState>
    );

    return (
        <SplitView
            list={
                <ListCard
                    title="Paging channels"
                    right={!form && <Button size="sm" onClick={() => setForm({ editing: null })}><Plus /> Add channel</Button>}
                >
                    {channels.length === 0 ? (
                        <p className="px-4 py-4 text-sm text-ink-soft">No paging channels yet.</p>
                    ) : channels.map((c) => (
                        <ListRow
                            key={c.id}
                            selected={!form && current?.id === c.id}
                            onSelect={() => { setForm(null); setSelected(c.id); }}
                            title={c.name}
                            meta={<span>{c.kind}{c.api_region ? ` (${c.api_region})` : ''} · {c.severity} · {c.secret_set ? 'key set' : 'no key'}</span>}
                            trailing={c.last_error
                                ? <Pill tone="peach">Error</Pill>
                                : !c.enabled ? <Pill tone="neutral">disabled</Pill> : undefined}
                        />
                    ))}
                </ListCard>
            }
            detail={detail}
        />
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

function WindowForm({ title, initial, busy, onSubmit, onCancel }: {
    title: string; initial: WindowDraft; busy: boolean; onSubmit: (v: MaintenanceWindowInput) => void; onCancel: () => void;
}) {
    const [d, setD] = useState<WindowDraft>(initial);
    const set = (patch: Partial<WindowDraft>) => setD((p) => ({ ...p, ...patch }));
    return (
        <DetailCard label={title}>
            <h2 className="text-[22px] font-bold leading-tight tracking-[-0.02em] text-ink">{title}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mw-name">Name</Label>
                    <Input id="mw-name" value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="Sunday deploys" />
                </div>
                <div className="flex flex-col gap-1.5">
                    <Label>Type</Label>
                    <Select value={d.mode} onValueChange={(m) => set({ mode: m as WindowDraft['mode'] })}>
                        <SelectTrigger aria-label="Window type"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="once">One-off</SelectItem>
                            <SelectItem value="weekly">Weekly recurring</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <label className="flex items-center gap-2 text-sm text-ink sm:col-span-2"><Checkbox checked={d.enabled} onCheckedChange={(c) => set({ enabled: c === true })} /> Enabled</label>
            </div>
            {d.mode === 'once' ? (
                <div className="grid gap-3 sm:grid-cols-2">
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="mw-start">Starts (your local time)</Label>
                        <Input id="mw-start" type="datetime-local" value={d.starts} onChange={(e) => set({ starts: e.target.value })} />
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="mw-end">Ends (your local time)</Label>
                        <Input id="mw-end" type="datetime-local" value={d.ends} onChange={(e) => set({ ends: e.target.value })} />
                    </div>
                </div>
            ) : (
                <>
                    <fieldset className="flex flex-wrap gap-4 rounded-2xl bg-background p-4 text-sm text-ink">
                        <legend className="sr-only">Days</legend>
                        <span aria-hidden="true" className="w-full text-[13px] font-semibold">Days</span>
                        {WEEKDAYS.map((label, i) => (
                            <label key={label} className="flex items-center gap-1.5">
                                <Checkbox checked={d.weekdays.includes(i)} onCheckedChange={(c) => set({
                                    weekdays: c === true ? [...d.weekdays, i].sort() : d.weekdays.filter((x) => x !== i),
                                })} /> {label}
                            </label>
                        ))}
                    </fieldset>
                    <div className="grid gap-3 sm:grid-cols-3">
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor="mw-st">From</Label>
                            <Input id="mw-st" type="time" value={d.start_time} onChange={(e) => set({ start_time: e.target.value })} />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor="mw-et">Until</Label>
                            <Input id="mw-et" type="time" value={d.end_time} onChange={(e) => set({ end_time: e.target.value })} />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor="mw-tz">Timezone</Label>
                            <Input id="mw-tz" value={d.timezone} onChange={(e) => set({ timezone: e.target.value })} placeholder="Europe/Berlin" />
                        </div>
                    </div>
                    <p className="text-xs text-ink-soft">An end before the start wraps past midnight (e.g. Fri 22:00 → Sat 06:00).</p>
                </>
            )}
            <div className="flex flex-wrap gap-2">
                <Button disabled={busy} onClick={() => {
                    if (!d.name.trim()) { toast.error('Name is required'); return; }
                    onSubmit(toInput(d));
                }}>Save window</Button>
                <Button variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>
            </div>
        </DetailCard>
    );
}

function describeWindow(w: MaintenanceWindow): string {
    if (w.weekdays?.length) {
        return `${w.weekdays.map((d) => WEEKDAYS[d]).join(', ')} ${w.start_time}–${w.end_time} (${w.timezone})`;
    }
    return `${fmt(w.starts_at)} → ${fmt(w.ends_at)}`;
}

function WindowDetail({ w, onEdit, onDelete, deleting }: {
    w: MaintenanceWindow; onEdit: () => void; onDelete: () => void; deleting: boolean;
}) {
    const weekly = !!w.weekdays?.length;
    return (
        <DetailCard label={`Maintenance window ${w.name}`}>
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    {w.active_now && <Pill tone="butter">Active now</Pill>}
                    <Pill tone={w.enabled ? 'mint' : 'neutral'}>{w.enabled ? 'Enabled' : 'Disabled'}</Pill>
                    <span className="text-[13px] text-ink-soft">{weekly ? 'Weekly recurring' : 'One-off'}</span>
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{w.name}</h2>
                <span className="text-sm text-ink-soft">{describeWindow(w)}</span>
            </div>
            <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={onEdit}><Pencil /> Edit</Button>
                <Button variant="outline" className={dangerOutline} disabled={deleting} onClick={onDelete}><Trash2 /> Delete</Button>
            </div>
            <FactGrid>
                {weekly ? (
                    <>
                        <Fact label="Days">{w.weekdays!.map((d) => WEEKDAYS[d]).join(', ')}</Fact>
                        <Fact label="Time">{w.start_time}–{w.end_time}</Fact>
                        <Fact label="Timezone">{w.timezone}</Fact>
                    </>
                ) : (
                    <>
                        <Fact label="Starts">{fmt(w.starts_at)}</Fact>
                        <Fact label="Ends">{fmt(w.ends_at)}</Fact>
                    </>
                )}
            </FactGrid>
        </DetailCard>
    );
}

function WindowsCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [form, setForm] = useState<null | { editing: MaintenanceWindow | null }>(null);
    const [selected, setSelected] = useState<number | null>(null);
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

    if (list.isLoading) return <Loading />;
    if (list.error) return <EmptyState title="Couldn't load maintenance windows">{errDetail(list.error)}</EmptyState>;
    const windows = list.data ?? [];
    const current = windows.find((w) => w.id === selected) ?? windows[0] ?? null;

    const detail = form ? (
        <WindowForm key={form.editing?.id ?? 'new'} title={form.editing ? `Edit ${form.editing.name}` : 'New maintenance window'}
            initial={draftFrom(form.editing)} busy={save.isPending}
            onSubmit={(v) => save.mutate(v)} onCancel={() => setForm(null)} />
    ) : current ? (
        <WindowDetail
            key={current.id}
            w={current}
            deleting={remove.isPending}
            onEdit={() => setForm({ editing: current })}
            onDelete={() => window.confirm(`Delete window "${current.name}"?`) && remove.mutate(current.id)}
        />
    ) : (
        <EmptyState title="No maintenance windows">
            Add a window to silence paging during planned work — a one-off date range or a weekly slot.
        </EmptyState>
    );

    return (
        <SplitView
            list={
                <ListCard
                    title="Maintenance windows"
                    right={!form && <Button size="sm" onClick={() => setForm({ editing: null })}><Plus /> Add window</Button>}
                >
                    {windows.map((w) => (
                        <ListRow
                            key={w.id}
                            selected={!form && current?.id === w.id}
                            onSelect={() => { setForm(null); setSelected(w.id); }}
                            title={w.name}
                            meta={describeWindow(w)}
                            trailing={w.active_now
                                ? <Pill tone="butter">active now</Pill>
                                : !w.enabled ? <Pill tone="neutral">disabled</Pill> : undefined}
                        />
                    ))}
                    <p className="px-4 py-3 text-xs leading-relaxed text-ink-soft">
                        {windows.length === 0 && 'No maintenance windows. '}
                        During a window pages are suppressed but still recorded in the paging log. Slack, Teams and email alerts are unaffected.
                    </p>
                </ListCard>
            }
            detail={detail}
        />
    );
}

// ---------------------------------------------------------------------------
// Paging events
// ---------------------------------------------------------------------------

const OUTCOME_TONE: Record<string, Tone> = { sent: 'mint', suppressed: 'butter', failed: 'peach' };

function EventsCard({ projectId }: { projectId: number }) {
    const list = useQuery({ queryKey: ['paging-events', projectId], queryFn: () => oncallApi.events(projectId), refetchInterval: 30000 });
    return (
        <SectionPanel flush title="Recent paging decisions"
            description="Every page TraceIQ sent, suppressed during a maintenance window, or failed to deliver.">
            {list.isLoading ? <PanelNote>Loading…</PanelNote> : !list.data?.length ? (
                <PanelNote>Nothing paged yet.</PanelNote>
            ) : (
                <div className="overflow-x-auto">
                    <table className={tbl.table}>
                        <thead>
                            <tr className={tbl.headRow}>
                                <th className={tbl.th}>When</th>
                                <th className={tbl.th}>Source</th>
                                <th className={tbl.th}>Action</th>
                                <th className={tbl.th}>Dedup key</th>
                                <th className={tbl.th}>Outcome</th>
                            </tr>
                        </thead>
                        <tbody>
                            {list.data.map((e) => (
                                <tr key={e.id} className={tbl.row}>
                                    <td className={`${tbl.td} whitespace-nowrap`}>{fmt(e.created_at)}</td>
                                    <td className={tbl.td}>{e.source}{e.run_id ? ` · run #${e.run_id}` : ''}</td>
                                    <td className={tbl.td}>{e.action}</td>
                                    <td className={tbl.td}><code className="font-mono text-[11px]">{e.dedup_key}</code></td>
                                    <td className={tbl.td}>
                                        <Pill tone={OUTCOME_TONE[e.outcome] ?? 'neutral'}>{e.outcome}</Pill>
                                        {e.detail && <div className="mt-1 max-w-xs break-words text-xs text-ink-soft">{e.detail}</div>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </SectionPanel>
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
        <div className="grid gap-5 lg:grid-cols-2">
            <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-2.5 rounded-2xl bg-background p-4 text-sm text-ink">
                    <label className="flex items-center gap-2"><Checkbox checked={enforce} onCheckedChange={(c) => setEnforce(c === true)} /> Enforce for this project</label>
                    <label className="flex items-center gap-2"><Checkbox checked={allowPrivate} onCheckedChange={(c) => setAllowPrivate(c === true)} /> Allow private / internal addresses</label>
                </div>
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="egress-hosts">Allowed hosts (one per line)</Label>
                    <textarea id="egress-hosts" value={hosts} onChange={(e) => setHosts(e.target.value)}
                        className="min-h-28 w-full rounded-2xl border border-input bg-white px-3 py-2 font-mono text-sm text-ink"
                        placeholder={'shop.example.com\ncdn.example.com'} />
                </div>
                <Button className="self-start" disabled={save.isPending} onClick={() => save.mutate()}>Save egress policy</Button>
            </div>
            <div className="flex flex-col gap-2 rounded-2xl bg-background p-4 text-sm text-ink">
                <Eyebrow>Effective at dispatch</Eyebrow>
                {!eff ? (
                    <p className="text-ink-soft">Not enforced — workers behave as before.</p>
                ) : (
                    <>
                        <p>Enforced{!s?.enforce && ' (forced by the instance setting)'}.</p>
                        <p>
                            Private addresses: {eff.allow_private ? 'allowed' : 'blocked'}
                            {s?.allow_private && !eff.allow_private && ' — capped by the instance (private targets are not allowed on this instance)'}
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                            {eff.allowed_hosts.length ? eff.allowed_hosts.map((h) => (
                                <code key={h} className="rounded-full border border-border bg-white px-2 py-0.5 font-mono text-[11px]">{h}</code>
                            )) : <span className="text-ink-soft">no hosts — every request is blocked</span>}
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
        <SectionPanel
            title="Worker egress allowlist"
            description={<>
                When enforced, the browser running this project's tests may only reach the listed hosts (and their
                subdomains). Everything else is blocked and shows up as a blocked request in the run's network
                evidence. Link-local / cloud-metadata addresses are always blocked. The environment's base URL is
                allowed automatically.
            </>}
        >
            {q.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : q.error || !q.data ? (
                <p className="text-sm text-pastel-ink-peach">{errDetail(q.error)}</p>
            ) : (
                // Keyed on the saved policy so the form re-initialises after a save
                // instead of syncing props into state from an effect.
                <EgressForm key={JSON.stringify(q.data.stored)} projectId={projectId} view={q.data} />
            )}
        </SectionPanel>
    );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type Tab = 'channels' | 'windows' | 'events' | 'egress';

export default function OnCall() {
    const projectId = useActiveProjectId();
    const [tab, setTab] = useState<Tab>('channels');

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                crumbs={<span>Operations</span>}
                title="On-call"
                subtitle={<>
                    Page PagerDuty or Opsgenie when monitors go down or unattended runs fail, silence paging during
                    maintenance, and limit what this project's test browsers may reach. Everyone on the project can
                    view this page; changes need the project admin role.
                </>}
                toolbar={projectId ? (
                    <PanelTabs<Tab>
                        label="On-call sections"
                        value={tab}
                        onChange={setTab}
                        tabs={[
                            { id: 'channels', label: 'Paging channels' },
                            { id: 'windows', label: 'Maintenance windows' },
                            { id: 'events', label: 'Paging log' },
                            { id: 'egress', label: 'Egress allowlist' },
                        ]}
                    />
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to manage its on-call paging.</EmptyState>
            ) : (
                <div key={projectId}>
                    {tab === 'channels' && <ChannelsCard projectId={projectId} />}
                    {tab === 'windows' && <WindowsCard projectId={projectId} />}
                    {tab === 'events' && <EventsCard projectId={projectId} />}
                    {tab === 'egress' && <EgressCard projectId={projectId} />}
                </div>
            )}
        </div>
    );
}
