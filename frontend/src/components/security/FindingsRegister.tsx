import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Download, ListChecks, RefreshCw, Save, Timer } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { securityApi, type FindingStatus, type FindingUpdate, type OwnerCandidate, type SecurityFinding } from '@/api/security';
import { downloadFromApi } from '@/lib/download';
import {
    FINDING_STATUS_LABEL, FINDING_STATUS_TONE, FINDING_TRANSITIONS, fromDateInput, isClosed,
    normalizeStatus, toDateInput,
} from './findingStatus';


const SEV_TONE: Record<string, string> = {
    critical: 'bg-rose-100 text-rose-800 border-rose-300',
    high: 'bg-rose-50 text-rose-700 border-rose-200',
    medium: 'bg-amber-50 text-amber-700 border-amber-200',
    low: 'bg-sky-50 text-sky-700 border-sky-200',
    info: 'bg-slate-50 text-slate-600 border-slate-200',
};

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
                className={`text-[11px] font-semibold bg-transparent border border-slate-200 rounded-md px-1.5 py-0.5 ${FINDING_STATUS_TONE[current]}`}
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
                <div className="w-64 space-y-1.5 rounded-lg border border-violet-200 bg-violet-50/60 p-2">
                    <label className="block text-[10px] font-semibold uppercase text-violet-700">
                        Justification
                        <textarea className="mt-0.5 w-full rounded border border-violet-200 bg-white p-1 text-xs font-normal normal-case text-slate-700"
                            rows={2} value={why} onChange={(e) => setWhy(e.target.value)} />
                    </label>
                    <label className="block text-[10px] font-semibold uppercase text-violet-700">
                        Accepted until
                        <Input type="date" className="mt-0.5 h-7 text-xs" value={until} onChange={(e) => setUntil(e.target.value)} />
                    </label>
                    <div className="flex justify-end gap-1.5">
                        <Button size="sm" variant="ghost" className="h-6 text-xs" onClick={() => setAccepting(false)}>Cancel</Button>
                        <Button size="sm" className="h-6 text-xs" disabled={!why.trim() || !until || busy}
                            onClick={() => {
                                onChange({ status: 'accepted_risk', risk_justification: why.trim(), risk_expires_at: fromDateInput(until)! });
                                setAccepting(false);
                            }}>
                            Accept risk
                        </Button>
                    </div>
                    <p className="text-[10px] text-violet-700/80">Needs a project admin, who is recorded as the approver. Re-opens automatically at expiry.</p>
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
        <tr className="border-t border-slate-100 align-top">
            <td className="py-2 pr-2">
                <Badge variant="outline" className={`rounded-md text-[9px] font-bold uppercase ${SEV_TONE[f.severity] || ''}`}>{f.severity}</Badge>
            </td>
            <td className="py-2 pr-2 min-w-0">
                <div className={`text-sm font-medium ${muted ? 'text-slate-400' : 'text-slate-700'}`}>{f.title}</div>
                <div className="text-[11px] text-slate-400 break-all">
                    {f.scan_type} · {f.location?.field ? `${f.location.field} · ` : ''}{f.target_url || '—'}
                </div>
                {status === 'accepted_risk' && f.risk_justification && (
                    <div className="text-[11px] text-violet-600 mt-0.5">
                        Accepted until {toDateInput(f.risk_expires_at)}: {f.risk_justification}
                    </div>
                )}
                {status === 'fixed_pending_verification' && (
                    <div className="text-[11px] text-sky-600 mt-0.5">
                        {f.retest_scan_id ? `Retest scan #${f.retest_scan_id} queued` : 'Verified by the next scan or run that covers it'}
                    </div>
                )}
            </td>
            <td className="py-2 pr-2">
                <select aria-label={`Owner of finding ${f.id}`}
                    className="text-xs border border-slate-200 rounded-md px-1.5 py-0.5 bg-white max-w-[160px]"
                    value={f.assignee_id ?? ''} disabled={busy}
                    onChange={(e) => onUpdate(f.id, { owner_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">Unowned</option>
                    {owners.map((o) => <option key={o.id} value={o.id}>{o.full_name || o.email}</option>)}
                </select>
            </td>
            <td className="py-2 pr-2 whitespace-nowrap">
                <Input type="date" aria-label={`Due date of finding ${f.id}`} className="h-7 w-[132px] text-xs"
                    value={toDateInput(f.due_at)} disabled={busy}
                    onChange={(e) => onUpdate(f.id, { due_at: fromDateInput(e.target.value) })} />
                {f.overdue && (
                    <Badge variant="outline" className="mt-1 rounded text-[9px] bg-rose-50 text-rose-700 border-rose-200">
                        <Timer className="w-3 h-3 mr-0.5" /> overdue
                    </Badge>
                )}
            </td>
            <td className="py-2 text-right">
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
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <div className="flex flex-wrap items-center gap-3 mb-4">
                <h3 className="font-bold text-slate-800 flex items-center gap-2">
                    <ListChecks className="w-4 h-4 text-indigo-500" /> Findings register
                </h3>
                {data && (
                    <span className="text-xs text-slate-500">
                        {data.actionable} actionable
                        {data.overdue > 0 && <span className="text-rose-600 font-semibold"> · {data.overdue} overdue</span>}
                    </span>
                )}
                <div className="ml-auto flex flex-wrap items-center gap-2">
                    <select aria-label="Filter findings by status" className="text-xs border border-slate-200 rounded-md px-2 py-1 bg-white"
                        value={filter} onChange={(e) => setFilter(e.target.value)}>
                        {FILTERS.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                    </select>
                    <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
                        <input type="checkbox" className="w-3.5 h-3.5" checked={overdueOnly} onChange={(e) => setOverdueOnly(e.target.checked)} />
                        Overdue only
                    </label>
                    <Button size="sm" variant="outline" className="h-8" onClick={sarif} disabled={downloading}>
                        <Download className="w-3.5 h-3.5 mr-1.5" /> SARIF
                    </Button>
                </div>
            </div>
            <p className="text-[11px] text-slate-400 mb-3">
                The latest occurrence of each finding across DAST scans, passive analysis and leak detection.
                Marking one fixed queues a retest; it becomes verified only when a later scan no longer finds it.
            </p>
            {register.isLoading ? <div className="p-6 flex justify-center"><RefreshCw className="animate-spin w-4 h-4 text-slate-400" /></div>
                : register.error ? <p className="text-sm text-rose-600 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />{errDetail(register.error)}</p>
                    : !data || data.findings.length === 0 ? <p className="text-sm text-slate-400">No findings match.</p>
                        : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-left">
                                    <thead>
                                        <tr className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
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
        </div>
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
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <h3 className="font-bold text-slate-800 mb-1 flex items-center gap-2"><Timer className="w-4 h-4 text-indigo-500" /> Remediation SLA</h3>
            <p className="text-xs text-slate-400 mb-3">Days to fix, by severity, counted from first detection. Workspace-wide.</p>
            <div className="grid grid-cols-4 gap-2 mb-3">
                {SLA_SEVERITIES.map((sev) => (
                    <label key={sev} className="text-[10px] font-bold uppercase text-slate-400">
                        {sev}
                        <Input type="number" min={1} className="h-8 mt-1 text-sm" disabled={!sla.data.can_edit}
                            value={draft[sev] ?? String(sla.data.sla_days[sev] ?? '')}
                            onChange={(e) => setDraft((d) => ({ ...d, [sev]: e.target.value }))} />
                    </label>
                ))}
            </div>
            {sla.data.can_edit ? (
                <Button size="sm" className="h-8" onClick={() => save.mutate()} disabled={save.isPending || Object.keys(draft).length === 0}>
                    <Save className="w-3.5 h-3.5 mr-1.5" /> Save SLA
                </Button>
            ) : <p className="text-[11px] text-slate-400">Only a workspace admin can change the SLA.</p>}
        </div>
    );
}
