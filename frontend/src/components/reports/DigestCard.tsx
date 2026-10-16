import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Send } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import {
    CheckRow, FieldRow, RowGroup, SettingsCard, checkboxClass, inputClass, textareaClass,
} from '@/components/redesign-local/groupD/settingsForm';
import { getNotificationRecipients } from '@/api/projectNotifications';
import { reportsApi, type Channel, type ReportSchedule, type ReportScheduleInput } from '@/api/reports';
import { emailsFromText } from '@/components/project-settings/notificationHelpers';
import { describeDigestResult } from './reportHelpers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const CHANNEL_LABELS: Record<Channel, string> = { email: 'Email', slack: 'Slack', teams: 'Microsoft Teams' };

interface Draft {
    name: string;
    cron_expression: string;
    window_days: number;
    channels: Channel[];
    recipient_user_ids: number[];
    extraText: string;
    is_active: boolean;
}

const NEW: Draft = {
    name: 'Weekly digest', cron_expression: '0 9 * * 1', window_days: 7, channels: ['email'],
    recipient_user_ids: [], extraText: '', is_active: true,
};

const draftFrom = (r: ReportSchedule): Draft => ({
    name: r.name, cron_expression: r.cron_expression, window_days: r.window_days,
    channels: (r.channels as Channel[]) || ['email'], recipient_user_ids: r.recipient_user_ids || [],
    extraText: (r.recipients || []).join('\n'), is_active: r.is_active,
});

const toInput = (d: Draft): ReportScheduleInput => ({
    name: d.name.trim(), cron_expression: d.cron_expression.trim(), window_days: d.window_days,
    channels: d.channels, recipient_user_ids: d.recipient_user_ids, recipients: emailsFromText(d.extraText),
    is_active: d.is_active,
});

function DigestForm({ projectId, initial, editingId, onDone }: {
    projectId: number; initial: Draft; editingId: number | null; onDone: () => void;
}) {
    const qc = useQueryClient();
    const [d, setD] = useState<Draft>(initial);
    const set = (p: Partial<Draft>) => setD((x) => ({ ...x, ...p }));
    const people = useQuery({ queryKey: ['notification-recipients', projectId], queryFn: () => getNotificationRecipients(projectId) });
    const save = useMutation({
        mutationFn: () => (editingId ? reportsApi.update(editingId, toInput(d)) : reportsApi.create(projectId, toInput(d))),
        onSuccess: () => { toast.success(editingId ? 'Digest updated' : 'Digest scheduled'); qc.invalidateQueries({ queryKey: ['reports', projectId] }); onDone(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const toggleChannel = (c: Channel, on: boolean) => set({ channels: on ? [...d.channels, c] : d.channels.filter((x) => x !== c) });
    const togglePerson = (id: number, on: boolean) => set({
        recipient_user_ids: on ? [...d.recipient_user_ids, id] : d.recipient_user_ids.filter((x) => x !== id),
    });
    const id = editingId ?? 'new';
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white p-4">
            <FieldRow label="Name" htmlFor={`dg-name-${id}`} wide>
                <Input id={`dg-name-${id}`} className={inputClass} value={d.name} onChange={(e) => set({ name: e.target.value })} />
            </FieldRow>
            <FieldRow label="When" htmlFor={`dg-cron-${id}`} hint="Cron, in UTC. 0 9 * * 1 is Mondays at 09:00." wide>
                <Input id={`dg-cron-${id}`} className={`${inputClass} font-mono`} value={d.cron_expression} onChange={(e) => set({ cron_expression: e.target.value })} />
            </FieldRow>
            <FieldRow label="Period covered" htmlFor={`dg-days-${id}`} hint="Compared with the period of the same length before it.">
                <select id={`dg-days-${id}`} className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm text-ink"
                    value={d.window_days} onChange={(e) => set({ window_days: parseInt(e.target.value) })}>
                    {[7, 14, 30].map((n) => <option key={n} value={n}>Last {n} days</option>)}
                </select>
            </FieldRow>
            <RowGroup title="Send to">
                {(Object.keys(CHANNEL_LABELS) as Channel[]).map((c) => (
                    <CheckRow key={c} id={`dg-ch-${c}-${id}`} label={CHANNEL_LABELS[c]} checked={d.channels.includes(c)} onChange={(v) => toggleChannel(c, v)}
                        hint={c === 'email' ? undefined : "Posts to this project's own webhook (Project settings → Notifications)."} />
                ))}
            </RowGroup>
            {d.channels.includes('email') && (
                <>
                    <RowGroup title="People">
                        <p className="mb-1 text-xs leading-relaxed text-ink-soft">
                            Only people with access to this project can be picked. Anyone who loses access, or turns this project off for themselves, stops getting it.
                        </p>
                        {people.isLoading && <p className="text-xs text-ink-soft">Loading members…</p>}
                        {people.data?.length === 0 && <p className="text-xs text-ink-soft">No members yet.</p>}
                        {people.data?.map((u) => (
                            <label key={u.id} htmlFor={`dg-person-${u.id}-${id}`} className="flex min-h-10 cursor-pointer items-center justify-between gap-4 py-1 text-sm">
                                <span className="min-w-0">
                                    <span className="block font-medium text-ink">{u.full_name || u.email}</span>
                                    {u.full_name && <span className="block text-xs text-ink-soft">{u.email}</span>}
                                </span>
                                <input id={`dg-person-${u.id}-${id}`} type="checkbox" className={checkboxClass}
                                    checked={d.recipient_user_ids.includes(u.id)} onChange={(e) => togglePerson(u.id, e.target.checked)} />
                            </label>
                        ))}
                    </RowGroup>
                    <RowGroup title="Other addresses">
                        <label htmlFor={`dg-extra-${id}`} className="mb-1 text-xs leading-relaxed text-ink-soft">Lists or people outside TraceIQ, one per line (up to 20).</label>
                        <textarea id={`dg-extra-${id}`} rows={3} className={textareaClass} value={d.extraText}
                            onChange={(e) => set({ extraText: e.target.value })} placeholder="qa-team@yourcompany.com" />
                    </RowGroup>
                </>
            )}
            <RowGroup>
                <CheckRow id={`dg-active-${id}`} label="Active" hint="Paused digests are kept but not sent." checked={d.is_active} onChange={(v) => set({ is_active: v })} />
            </RowGroup>
            <div className="flex flex-wrap gap-2 border-t border-border pt-3">
                <Button onClick={() => save.mutate()} disabled={save.isPending || !d.name.trim() || !d.cron_expression.trim() || d.channels.length === 0}>
                    {editingId ? 'Save changes' : 'Create digest'}
                </Button>
                <Button variant="outline" onClick={onDone}>Cancel</Button>
            </div>
        </div>
    );
}

export function DigestCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [editing, setEditing] = useState<number | 'new' | null>(null);
    const reports = useQuery({ queryKey: ['reports', projectId], queryFn: () => reportsApi.list(projectId) });
    const sendNow = useMutation({
        mutationFn: (id: number) => reportsApi.sendNow(id),
        onSuccess: () => { toast.success('Digest queued'); setTimeout(() => qc.invalidateQueries({ queryKey: ['reports', projectId] }), 4000); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const del = useMutation({
        mutationFn: (id: number) => reportsApi.remove(id),
        onSuccess: () => { toast.success('Removed'); qc.invalidateQueries({ queryKey: ['reports', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const list = reports.data || [];
    return (
        <SettingsCard
            labelledBy="digest"
            title="Quality digest"
            description="A recurring summary by email or to this project's chat: pass rate, runs and failures against the period before, top failing tests, open failure groups, flaky tests and release readiness. Editors can change it."
            actions={editing === null ? <Button variant="outline" onClick={() => setEditing('new')}><Plus className="h-4 w-4" /> New digest</Button> : undefined}
        >
            {editing === 'new' && <DigestForm projectId={projectId} initial={NEW} editingId={null} onDone={() => setEditing(null)} />}
            {list.length === 0 && editing !== 'new' ? (
                <p className="text-sm text-ink-soft">No digest yet.</p>
            ) : (
                <ul className="flex flex-col">
                    {list.map((r, i) => {
                        const last = describeDigestResult(r.last_result);
                        const people = r.recipient_user_ids?.length ?? 0;
                        const extra = r.recipients?.length ?? 0;
                        return (
                            <li key={r.id} className={`flex flex-col gap-2 py-3 ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div className="flex min-w-0 flex-col">
                                        <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                                            {r.name}{!r.is_active && <Pill tone="neutral">Paused</Pill>}
                                        </span>
                                        <span className="text-xs text-ink-soft">
                                            <span className="font-mono">{r.cron_expression}</span> · last {r.window_days} days · {(r.channels || ['email']).map((c) => CHANNEL_LABELS[c as Channel] ?? c).join(', ')}
                                            {(r.channels || ['email']).includes('email') ? ` · ${people} people, ${extra} other address${extra === 1 ? '' : 'es'}` : ''}
                                        </span>
                                        {r.last_run_at && <span className="text-xs text-ink-soft">Last sent {new Date(r.last_run_at).toLocaleString()}{last ? `: ${last}` : ''}</span>}
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Button size="sm" variant="outline" className="h-10 px-4" onClick={() => sendNow.mutate(r.id)}><Send className="h-4 w-4" /> Send now</Button>
                                        <Button size="sm" variant="outline" className="h-10 px-4" onClick={() => setEditing(r.id)}>Edit</Button>
                                        <Button size="sm" variant="outline" className="h-10 px-4" onClick={() => del.mutate(r.id)}>Delete</Button>
                                    </div>
                                </div>
                                {editing === r.id && <DigestForm projectId={projectId} initial={draftFrom(r)} editingId={r.id} onDone={() => setEditing(null)} />}
                            </li>
                        );
                    })}
                </ul>
            )}
        </SettingsCard>
    );
}
