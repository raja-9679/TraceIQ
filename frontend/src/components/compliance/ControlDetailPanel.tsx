import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { downloadFromApi } from '@/lib/download';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DetailCard, Eyebrow, Panel, Pill } from '@/components/redesign/primitives';
import { FieldRow, SettingsCard, inputClass, textareaClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { cn } from '@/lib/utils';
import { complianceApi, type ControlDetail, type ControlException, type EvidenceSourceDef, type Member } from '@/api/compliance';
import { ControlForm } from './ControlForm';
import {
    EXCEPTION_TONE, SOURCE_META, STATUS_META, fmtBytes, fmtDate, fmtDay, historyLabel, maxExceptionDay, minExceptionDay, paramsText,
} from './helpers';

function ExceptionRow({ x, control, onDecide, busy }: {
    x: ControlException;
    control: ControlDetail;
    onDecide: (action: 'approve' | 'reject' | 'revoke', note: string) => void;
    busy: boolean;
}) {
    const [note, setNote] = useState('');
    const own = x.requested_by_id === control.me;
    return (
        <li className="flex flex-col gap-2 py-3">
            <div className="flex flex-wrap items-center gap-2">
                <Pill tone={EXCEPTION_TONE[x.status] ?? 'neutral'}>{x.status}</Pill>
                <span className="text-xs text-ink-soft">
                    requested by {x.requested_by ?? 'unknown'} · {fmtDate(x.requested_at)} · expires {fmtDay(x.expires_at)}
                    {x.approved_by && <> · {x.status === 'rejected' ? 'rejected' : 'approved'} by {x.approved_by}</>}
                </span>
            </div>
            <p className="whitespace-pre-wrap text-sm text-ink">{x.reason}</p>
            {x.compensating_control && <p className="text-sm text-ink-soft"><span className="font-medium text-ink">Compensating control:</span> {x.compensating_control}</p>}
            {x.decided_note && <p className="text-xs text-ink-soft">Note: {x.decided_note}</p>}
            {control.can_edit && (x.status === 'pending' || x.status === 'approved') && (
                <div className="flex flex-wrap items-center gap-2">
                    <Input aria-label="Decision note" placeholder="Note (optional)" className="h-9 max-w-xs rounded-full" value={note} onChange={(e) => setNote(e.target.value)} />
                    {x.status === 'pending' ? (
                        <>
                            <Button size="sm" disabled={busy || own} onClick={() => onDecide('approve', note)}>Approve</Button>
                            <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide('reject', note)}>Reject</Button>
                            {own && <span className="text-xs text-ink-soft">You requested this — another admin must approve it.</span>}
                        </>
                    ) : (
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide('revoke', note)}>Revoke exception</Button>
                    )}
                </div>
            )}
        </li>
    );
}

/** Everything about one control: live evidence, manual evidence, exceptions, history. */
export function ControlDetailPanel({ controlId, sources, members, onDeleted }: {
    controlId: number;
    sources: EvidenceSourceDef[];
    members: Member[];
    onDeleted: () => void;
}) {
    const qc = useQueryClient();
    const detail = useQuery({ queryKey: ['compliance-control', controlId], queryFn: () => complianceApi.get(controlId) });
    const [editing, setEditing] = useState(false);
    const [evNote, setEvNote] = useState('');
    const [evOutcome, setEvOutcome] = useState<'pass' | 'fail'>('pass');
    const [evFile, setEvFile] = useState<File | null>(null);
    const [fileKey, setFileKey] = useState(0);
    const [xReason, setXReason] = useState('');
    const [xComp, setXComp] = useState('');
    const [xUntil, setXUntil] = useState('');

    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['compliance-control', controlId] });
        qc.invalidateQueries({ queryKey: ['compliance-controls'] });
    };
    const fail = (e: unknown) => toast.error(errDetail(e));

    const update = useMutation({
        mutationFn: (body: Parameters<typeof complianceApi.update>[1]) => complianceApi.update(controlId, body),
        onSuccess: () => { toast.success('Control saved'); setEditing(false); refresh(); },
        onError: fail,
    });
    const remove = useMutation({
        mutationFn: () => complianceApi.remove(controlId),
        onSuccess: () => { toast.success('Control deleted'); qc.invalidateQueries({ queryKey: ['compliance-controls'] }); onDeleted(); },
        onError: fail,
    });
    const addEvidence = useMutation({
        mutationFn: () => complianceApi.addEvidence(controlId, { note: evNote.trim() || undefined, outcome: evOutcome, file: evFile }),
        onSuccess: () => { toast.success('Evidence recorded'); setEvNote(''); setEvFile(null); setFileKey((k) => k + 1); refresh(); },
        onError: fail,
    });
    const removeEvidence = useMutation({ mutationFn: (id: number) => complianceApi.removeEvidence(id), onSuccess: refresh, onError: fail });
    const requestException = useMutation({
        mutationFn: () => complianceApi.requestException(controlId, {
            reason: xReason.trim(), compensating_control: xComp.trim() || undefined, expires_at: `${xUntil}T23:59:59Z`,
        }),
        onSuccess: () => { toast.success('Exception requested — another admin must approve it'); setXReason(''); setXComp(''); setXUntil(''); refresh(); },
        onError: fail,
    });
    const decide = useMutation({
        mutationFn: ({ id, action, note }: { id: number; action: 'approve' | 'reject' | 'revoke'; note: string }) =>
            complianceApi.decideException(id, action, note.trim() || undefined),
        onSuccess: refresh,
        onError: fail,
    });

    const c = detail.data;
    if (detail.isError) return <DetailCard label="Control"><p className="text-sm text-ink-soft">{errDetail(detail.error)}</p></DetailCard>;
    if (!c) return <DetailCard label="Control"><Loader2 className="h-5 w-5 animate-spin text-ink-soft" /></DetailCard>;
    const meta = STATUS_META[c.status];

    if (editing) {
        return (
            <ControlForm
                title={`Edit ${c.reference}`}
                submitLabel="Save control"
                initial={{ framework: c.framework, reference: c.reference, title: c.title, description: c.description,
                    owner_id: c.owner_id, evidence_sources: c.evidence_sources, freshness_days: c.freshness_days }}
                sources={sources} members={members} busy={update.isPending}
                onSubmit={(v) => update.mutate(v)} onCancel={() => setEditing(false)}
            />
        );
    }

    return (
        <div className="flex flex-col gap-5">
            <DetailCard label={`Control ${c.reference}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex min-w-0 flex-col gap-1">
                        <Eyebrow>{c.framework} · {c.reference}</Eyebrow>
                        <h2 className="text-xl font-bold text-ink">{c.title}</h2>
                        <p className="text-xs text-ink-soft">
                            Owner {c.owner ?? 'not assigned'} · freshness SLA {c.freshness_days} days
                            {c.newest_pass_at && <> · newest passing evidence {fmtDate(c.newest_pass_at)}</>}
                        </p>
                    </div>
                    {c.can_edit && (
                        <div className="flex flex-wrap gap-2">
                            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>Edit control</Button>
                            <Button size="sm" variant="outline" disabled={remove.isPending}
                                onClick={() => { if (window.confirm(`Delete ${c.reference} ${c.title}, its evidence and exceptions?`)) remove.mutate(); }}>
                                Delete control
                            </Button>
                        </div>
                    )}
                </div>
                <Panel title="Status" right={<Pill tone={meta.tone}>{meta.label}</Pill>}>{c.status_reason}</Panel>
                {c.description && <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{c.description}</p>}
                {c.active_exception && (
                    <div className={warnCalloutClass}>
                        Exception in force until {fmtDay(c.active_exception.expires_at)}, approved by {c.active_exception.approved_by}: {c.active_exception.reason}
                    </div>
                )}
                <div className="flex flex-col gap-2">
                    <Eyebrow>Automated evidence</Eyebrow>
                    {c.results.length === 0 ? (
                        <p className="text-sm text-ink-soft">No automated sources — this control relies on manual evidence.</p>
                    ) : (
                        <ul className="overflow-hidden rounded-2xl border border-border">
                            {c.results.map((r, i) => (
                                <li key={r.type} className={cn('flex flex-wrap items-start gap-3 px-4 py-3 text-sm', i > 0 && 'border-t border-[#F0EEE8]')}>
                                    <Pill tone={SOURCE_META[r.status].tone}>{SOURCE_META[r.status].label}</Pill>
                                    <span className="min-w-0 flex-1">
                                        <span className="block font-medium text-ink">{r.label}</span>
                                        <span className="block text-xs text-ink-soft">{r.reason}{r.observed_at && ` · as of ${fmtDate(r.observed_at)}`}</span>
                                        {paramsText(c.evidence_sources.find((s) => s.type === r.type)?.params ?? {}) && (
                                            <span className="block font-mono text-[11px] text-ink-soft">{paramsText(c.evidence_sources.find((s) => s.type === r.type)?.params ?? {})}</span>
                                        )}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </DetailCard>

            <SettingsCard labelledBy={`ev-${c.id}`} title="Manual evidence"
                description="Notes and files (PDF, images, text, CSV, JSON, Office; up to 10 MB). Each is kept with who added it and its SHA-256.">
                {c.evidence.length === 0 ? (
                    <p className="text-sm text-ink-soft">No manual evidence yet.</p>
                ) : (
                    <ul className="flex flex-col divide-y divide-border">
                        {c.evidence.map((e) => (
                            <li key={e.id} className="flex flex-wrap items-start gap-3 py-3">
                                <Pill tone={e.outcome === 'pass' ? 'mint' : 'peach'}>{e.outcome === 'pass' ? 'Pass' : 'Fail'}</Pill>
                                <div className="min-w-0 flex-1 text-sm">
                                    {e.note && <p className="whitespace-pre-wrap text-ink">{e.note}</p>}
                                    {e.file_name && <p className="text-ink">{e.file_name} <span className="text-xs text-ink-soft">({fmtBytes(e.size_bytes)})</span></p>}
                                    <p className="text-xs text-ink-soft">{e.created_by ?? 'unknown'} · {fmtDate(e.created_at)}{e.sha256 && <> · <span className="font-mono">sha256 {e.sha256.slice(0, 16)}…</span></>}</p>
                                </div>
                                <div className="flex gap-2">
                                    {e.has_file && (
                                        <Button size="sm" variant="outline"
                                            onClick={() => downloadFromApi(complianceApi.evidenceFilePath(e.id), e.file_name || 'evidence').catch(fail)}>
                                            <Download className="h-3.5 w-3.5" /> Download file
                                        </Button>
                                    )}
                                    {c.can_edit && (
                                        <Button size="sm" variant="outline" disabled={removeEvidence.isPending}
                                            onClick={() => { if (window.confirm('Remove this evidence?')) removeEvidence.mutate(e.id); }}>Remove</Button>
                                    )}
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
                {c.can_contribute && (
                    <div className="flex flex-col gap-3 border-t border-border pt-4">
                        <FieldRow label="Outcome">
                            <div role="radiogroup" aria-label="Outcome" className="flex gap-2">
                                {(['pass', 'fail'] as const).map((o) => (
                                    <button key={o} type="button" role="radio" aria-checked={evOutcome === o} onClick={() => setEvOutcome(o)}
                                        className={cn('h-10 rounded-full border px-4 text-sm font-medium',
                                            evOutcome === o ? 'border-ink bg-ink text-white' : 'border-border bg-white text-ink hover:bg-background')}>
                                        {o === 'pass' ? 'Shows it works' : 'Shows a failure'}
                                    </button>
                                ))}
                            </div>
                        </FieldRow>
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                            Note
                            <textarea className={`${textareaClass} font-sans`} rows={2} maxLength={4000} value={evNote}
                                onChange={(e) => setEvNote(e.target.value)} placeholder="What this shows, and where it came from" />
                        </label>
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                            File (optional)
                            <input key={fileKey} type="file" className="text-sm"
                                accept=".pdf,.png,.jpg,.jpeg,.txt,.log,.md,.csv,.json,.docx,.xlsx"
                                onChange={(e) => setEvFile(e.target.files?.[0] ?? null)} />
                        </label>
                        <div>
                            <Button disabled={addEvidence.isPending || (!evNote.trim() && !evFile)} onClick={() => addEvidence.mutate()}>
                                {addEvidence.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Add evidence
                            </Button>
                        </div>
                    </div>
                )}
            </SettingsCard>

            <SettingsCard labelledBy={`ex-${c.id}`} title="Exceptions"
                description="A time-boxed waiver (at most 365 days). A second admin — not the requester — must approve it; once it expires the control is judged on its evidence again.">
                {c.exceptions.length === 0 ? (
                    <p className="text-sm text-ink-soft">No exceptions.</p>
                ) : (
                    <ul className="flex flex-col divide-y divide-border">
                        {c.exceptions.map((x) => (
                            <ExceptionRow key={x.id} x={x} control={c} busy={decide.isPending}
                                onDecide={(action, note) => decide.mutate({ id: x.id, action, note })} />
                        ))}
                    </ul>
                )}
                {c.can_contribute && (
                    <div className="flex flex-col gap-3 border-t border-border pt-4">
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                            Reason
                            <textarea className={`${textareaClass} font-sans`} rows={2} maxLength={4000} value={xReason}
                                onChange={(e) => setXReason(e.target.value)} placeholder="Why the control cannot be met right now" />
                        </label>
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                            Compensating control (optional)
                            <textarea className={`${textareaClass} font-sans`} rows={2} maxLength={4000} value={xComp}
                                onChange={(e) => setXComp(e.target.value)} placeholder="What reduces the risk in the meantime" />
                        </label>
                        <FieldRow label="Expires on" htmlFor={`ex-until-${c.id}`} hint="At most 365 days from today.">
                            <Input id={`ex-until-${c.id}`} type="date" className={inputClass} value={xUntil}
                                min={minExceptionDay()} max={maxExceptionDay()}
                                onChange={(e) => setXUntil(e.target.value)} />
                        </FieldRow>
                        <div>
                            <Button variant="outline" disabled={requestException.isPending || !xReason.trim() || !xUntil}
                                onClick={() => requestException.mutate()}>
                                {requestException.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Request exception
                            </Button>
                        </div>
                    </div>
                )}
            </SettingsCard>

            <SettingsCard labelledBy={`hist-${c.id}`} title="History" description="Every change to this control, its evidence and exceptions, from the audit trail.">
                {c.history.length === 0 ? <p className="text-sm text-ink-soft">No history.</p> : (
                    <ol className="flex flex-col divide-y divide-border">
                        {c.history.map((h) => (
                            <li key={h.id} className="flex flex-wrap gap-x-3 py-2 text-sm">
                                <span className="font-medium text-ink">{historyLabel(h.action)}</span>
                                <span className="text-ink-soft">{h.by ?? 'system'} · {fmtDate(h.at)}</span>
                            </li>
                        ))}
                    </ol>
                )}
            </SettingsCard>
        </div>
    );
}
