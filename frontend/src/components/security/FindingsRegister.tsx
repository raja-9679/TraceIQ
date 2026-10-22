import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Download, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { securityApi, type FindingStatus, type FindingUpdate, type OwnerCandidate, type SecurityFinding } from '@/api/security';
import { downloadFromApi } from '@/lib/download';
import { Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import {
    FINDING_STATUS_LABEL, FINDING_STATUS_TONE, FINDING_TRANSITIONS, fromDateInput, isClosed,
    normalizeStatus, toDateInput,
} from './findingStatus';


const SEV_TONE: Record<string, Tone> = { critical: 'pink', high: 'peach', medium: 'butter', low: 'neutral', info: 'neutral' };
const selectClass = 'h-9 rounded-full border border-border bg-white px-3 text-sm text-ink';

/** Status picker limited to the transitions the workflow allows; accepting a
 * risk asks for the justification and expiry the backend requires. */
export function FindingStatusControl({ finding, onChange, busy }: {
    finding: SecurityFinding;
    onChange: (body: FindingUpdate) => void;
    busy?: boolean;
}) {
    const current = normalizeStatus(finding.status);
    const [accepting, setAccepting] = useState(false);
    const [why, setWhy] = useState('');
    const [until, setUntil] = useState('');
    const options = [current, ...FINDING_TRANSITIONS[current]];

    return (
        <div className="flex flex-col items-end gap-1.5">
            <select
                aria-label={`Status of finding ${finding.id}`}
                className={`${selectClass} font-semibold ${FINDING_STATUS_TONE[current]}`}
                value={accepting ? 'accepted_risk' : current}
                disabled={busy || !!finding.superseded_by_id}
                onChange={(e) => {
                    const next = e.target.value as FindingStatus;
                    if (next === current) { setAccepting(false); return; }
                    if (next === 'accepted_risk') { setAccepting(true); return; }
                    setAccepting(false);
                    onChange({ status: next });
                }}
            >
                {options.map((s) => <option key={s} value={s}>{FINDING_STATUS_LABEL[s]}</option>)}
            </select>
            {accepting && (
                <div className="flex w-72 flex-col gap-2 rounded-2xl border border-border bg-white p-3 text-left shadow-[inset_4px_0_0_#F2C94C]">
                    <label className="flex flex-col gap-1 text-[13px] font-semibold text-ink">
                        Justification
                        <textarea className="rounded-xl border border-border bg-white p-2 text-sm font-normal"
                            rows={2} value={why} onChange={(e) => setWhy(e.target.value)} />
                    </label>
                    <label className="flex flex-col gap-1 text-[13px] font-semibold text-ink">
                        Accepted until
                        <Input type="date" className="h-9" value={until} onChange={(e) => setUntil(e.target.value)} />
                    </label>
                    <div className="flex justify-end gap-2">
                        <Button size="sm" variant="outline" onClick={() => setAccepting(false)}>Cancel</Button>
                        <Button size="sm" disabled={!why.trim() || !until || busy}
                            onClick={() => {
                                onChange({ status: 'accepted_risk', risk_justification: why.trim(), risk_expires_at: fromDateInput(until)! });
                                setAccepting(false);
                            }}>
                            Accept risk
                        </Button>
                    </div>
                    <p className="text-xs text-ink-soft">Needs a project admin, who is recorded as the approver. Re-opens automatically at expiry.</p>
                </div>
            )}
        </div>
    );
}

function RegisterRow({ f, owners, onUpdate, busy }: {
    f: SecurityFinding;
    owners: OwnerCandidate[];
    onUpdate: (id: number, body: FindingUpdate) => void;
    busy: boolean;
}) {
    const status = normalizeStatus(f.status);
    const muted = isClosed(status);
    return (
        <tr className="border-t border-[#F0EEE8] align-top">
            <td className="py-3 pr-3">
                <Pill tone={SEV_TONE[f.severity] ?? 'neutral'} className="capitalize">{f.severity}</Pill>
            </td>
            <td className="min-w-0 py-3 pr-3">
                {/* overflow-wrap:anywhere, not break-words: only "anywhere" lowers the
                    cell's min-content width, so a 64-char script hash in a title
                    wraps instead of pushing Owner/Due/Status off screen. */}
                <div title={f.title} className={`line-clamp-3 text-sm font-medium [overflow-wrap:anywhere] ${muted ? 'text-ink-soft' : 'text-ink'}`}>{f.title}</div>
                <div className="text-xs text-ink-soft [overflow-wrap:anywhere]">
                    {f.scan_type} · {f.location?.field ? `${f.location.field} · ` : ''}{f.target_url || '—'}
                </div>
                {status === 'accepted_risk' && f.risk_justification && (
                    <div className="mt-0.5 text-xs text-ink-soft">
                        Accepted until {toDateInput(f.risk_expires_at)}: {f.risk_justification}
                    </div>
                )}
                {status === 'fixed_pending_verification' && (
                    <div className="mt-0.5 text-xs text-ink-soft">
                        {f.retest_scan_id ? `Retest scan #${f.retest_scan_id} queued` : 'Verified by the next scan or run that covers it'}
                    </div>
                )}
            </td>
            <td className="py-3 pr-3">
                <select aria-label={`Owner of finding ${f.id}`}
                    className={`${selectClass} max-w-[180px]`}
                    value={f.assignee_id ?? ''} disabled={busy}
                    onChange={(e) => onUpdate(f.id, { owner_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">Unowned</option>
                    {owners.map((o) => <option key={o.id} value={o.id}>{o.full_name || o.email}</option>)}
                </select>
            </td>
            <td className="whitespace-nowrap py-3 pr-3">
                <Input type="date" aria-label={`Due date of finding ${f.id}`} className="h-9 w-[150px]"
                    value={toDateInput(f.due_at)} disabled={busy}
                    onChange={(e) => onUpdate(f.id, { due_at: fromDateInput(e.target.value) })} />
                {f.overdue && (
                    <Pill tone="peach" className="mt-1">Overdue</Pill>
                )}
            </td>
            <td className="py-3 text-right">
                <FindingStatusControl finding={f} busy={busy} onChange={(body) => onUpdate(f.id, body)} />
            </td>
        </tr>
    );
}

const FILTERS: { id: string; label: string; status?: string }[] = [
    { id: 'actionable', label: 'Open & in progress', status: 'open,in_progress' },
    { id: 'pending', label: 'Awaiting retest', status: 'fixed_pending_verification' },
    { id: 'waived', label: 'Accepted / false positive', status: 'accepted_risk,false_positive' },
    { id: 'fixed', label: 'Verified fixed', status: 'verified_fixed' },
    { id: 'all', label: 'All' },
];

export function FindingsRegister({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [filter, setFilter] = useState('actionable');
    const [overdueOnly, setOverdueOnly] = useState(false);
    const status = FILTERS.find((x) => x.id === filter)?.status;

    const register = useQuery({
        queryKey: ['sec-findings', projectId, status, overdueOnly],
        queryFn: () => securityApi.listFindings(projectId, { status, overdue: overdueOnly || undefined }),
    });
    const owners = useQuery({
        queryKey: ['sec-finding-owners', projectId],
        queryFn: () => securityApi.ownerCandidates(projectId),
    });
    const update = useMutation({
        mutationFn: ({ id, body }: { id: number; body: FindingUpdate }) => securityApi.updateFinding(id, body),
        onSuccess: (f) => {
            if (f.retest) {
                if (f.retest.queued) toast.success(`Marked fixed — retest scan #${f.retest.scan_id} ${f.retest.reused ? 'already running' : 'queued'}`);
                else toast.message(`Marked fixed — ${f.retest.reason}`);
            } else {
                toast.success('Finding updated');
            }
            qc.invalidateQueries({ queryKey: ['sec-findings', projectId] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const [downloading, setDownloading] = useState(false);
    const sarif = async () => {
        setDownloading(true);
        try {
            await downloadFromApi(securityApi.projectSarifPath(projectId), `traceiq-project-${projectId}.sarif`);
        } catch (e) {
            toast.error(errDetail(e));
        } finally {
            setDownloading(false);
        }
    };

    const data = register.data;
    return (
        <section className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-base font-semibold text-ink">Findings register</h2>
                {data && (
                    <span className="text-[13px] text-ink-soft">
                        {data.actionable} actionable
                        {data.overdue > 0 && <span className="font-semibold text-pastel-ink-peach"> · {data.overdue} overdue</span>}
                    </span>
                )}
                <div className="ml-auto flex flex-wrap items-center gap-2">
                    <select aria-label="Filter findings by status" className={selectClass}
                        value={filter} onChange={(e) => setFilter(e.target.value)}>
                        {FILTERS.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                    </select>
                    <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm text-ink">
                        <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={overdueOnly} onChange={(e) => setOverdueOnly(e.target.checked)} />
                        Overdue only
                    </label>
                    <Button variant="outline" onClick={sarif} disabled={downloading}>
                        <Download className="h-4 w-4" /> Download SARIF
                    </Button>
                </div>
            </div>
            <p className="text-[13px] text-ink-soft">
                The latest occurrence of each finding across DAST scans, passive analysis and leak detection.
                Marking one fixed queues a retest; it becomes verified only when a later scan no longer finds it.
            </p>
            {register.isLoading ? <div className="flex justify-center p-6"><RefreshCw className="h-4 w-4 animate-spin text-ink-soft" /></div>
                : register.error ? <p className="flex items-center gap-1.5 text-sm text-pastel-ink-peach"><AlertTriangle className="h-4 w-4" />{errDetail(register.error)}</p>
                    : !data || data.findings.length === 0 ? <p className="py-6 text-center text-sm text-ink-soft">No findings match.</p>
                        : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-left">
                                    <thead>
                                        <tr className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                                            <th className="pb-2 pr-2">Severity</th>
                                            <th className="pb-2 pr-2">Finding</th>
                                            <th className="pb-2 pr-2">Owner</th>
                                            <th className="pb-2 pr-2">Due</th>
                                            <th className="pb-2 text-right">Status</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.findings.map((f) => (
                                            <RegisterRow key={f.id} f={f} owners={owners.data || []} busy={update.isPending}
                                                onUpdate={(id, body) => update.mutate({ id, body })} />
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
        </section>
    );
}

const SLA_SEVERITIES = ['critical', 'high', 'medium', 'low'] as const;

export function FindingSlaCard({ workspaceId }: { workspaceId: number }) {
    const qc = useQueryClient();
    const sla = useQuery({ queryKey: ['finding-sla', workspaceId], queryFn: () => securityApi.getSla(workspaceId) });
    const [draft, setDraft] = useState<Record<string, string>>({});
    const save = useMutation({
        mutationFn: () => {
            const body: Record<string, number> = {};
            for (const [k, v] of Object.entries(draft)) if (v.trim()) body[k] = parseInt(v, 10);
            return securityApi.setSla(workspaceId, body);
        },
        onSuccess: () => { toast.success('SLA saved — applies to findings detected from now on'); setDraft({}); qc.invalidateQueries({ queryKey: ['finding-sla', workspaceId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    if (!sla.data) return null;
    return (
        <section className="flex max-w-3xl flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <div>
                <h2 className="text-base font-semibold text-ink">Remediation SLA</h2>
                <p className="mt-1 text-[13px] text-ink-soft">Days to fix, by severity, counted from first detection. Workspace-wide.</p>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {SLA_SEVERITIES.map((sev) => (
                    <label key={sev} className="flex flex-col gap-1.5 text-[13px] font-semibold capitalize text-ink">
                        {sev}
                        <Input type="number" min={1} disabled={!sla.data.can_edit}
                            value={draft[sev] ?? String(sla.data.sla_days[sev] ?? '')}
                            onChange={(e) => setDraft((d) => ({ ...d, [sev]: e.target.value }))} />
                    </label>
                ))}
            </div>
            {sla.data.can_edit ? (
                <Button className="self-start" onClick={() => save.mutate()} disabled={save.isPending || Object.keys(draft).length === 0}>
                    Save SLA
                </Button>
            ) : <p className="text-xs text-ink-soft">Only a workspace admin can change the SLA.</p>}
        </section>
    );
}
