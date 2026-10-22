import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { downloadFromApi } from '@/lib/download';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { auditApi, complianceApi, type AuditFilters, type AuditRow, type ChainVerdict } from '@/api/compliance';
import { WorkspacePicker } from '@/components/compliance/WorkspacePicker';
import { useComplianceWorkspace } from '@/components/compliance/useComplianceWorkspace';
import { dayEnd, dayStart, fmtDate } from '@/components/compliance/helpers';
import { selectClass } from '@/components/compliance/selectClass';

const PAGE = 50;
const ENTITY_TYPES = ['auth', 'user', 'workspace', 'team', 'project', 'suite', 'case', 'test_run', 'api_key',
    'access_review', 'compliance_control', 'evidence_pack', 'restore_drill', 'security_finding'];

interface Draft { entity_type: string; action: string; user_id: string; since: string; until: string }
const EMPTY: Draft = { entity_type: '', action: '', user_id: '', since: '', until: '' };

function toFilters(d: Draft): AuditFilters {
    return {
        entity_type: d.entity_type.trim() || undefined,
        action: d.action.trim() || undefined,
        user_id: d.user_id ? Number(d.user_id) : undefined,
        since: d.since ? dayStart(d.since) : undefined,
        until: d.until ? dayEnd(d.until) : undefined,
    };
}

function Row({ row, people }: { row: AuditRow; people: Record<number, string> }) {
    const [open, setOpen] = useState(false);
    const actor = row.actor_label || (row.user_id ? people[row.user_id] ?? `user ${row.user_id}` : row.actor_type ?? 'system');
    const hasChanges = row.changes && Object.keys(row.changes).length > 0;
    return (
        <>
            <tr className="border-t border-border align-top">
                <td className="whitespace-nowrap py-2 pr-3 text-xs text-ink-soft">{fmtDate(row.timestamp)}</td>
                <td className="py-2 pr-3 text-sm text-ink">{actor}{row.actor_type && row.actor_type !== 'user' && <span className="block text-xs text-ink-soft">{row.actor_type}</span>}</td>
                <td className="py-2 pr-3 text-sm font-medium text-ink">{row.action}</td>
                <td className="py-2 pr-3 text-sm text-ink">{row.entity_type} <span className="font-mono text-xs text-ink-soft">#{row.entity_id}</span></td>
                <td className="py-2 pr-3 font-mono text-xs text-ink-soft">{row.ip_address ?? '—'}</td>
                <td className="py-2 text-right">
                    {hasChanges && <Button size="sm" variant="outline" onClick={() => setOpen((o) => !o)}>{open ? 'Hide details' : 'Details'}</Button>}
                </td>
            </tr>
            {open && hasChanges && (
                <tr><td colSpan={6} className="pb-3">
                    <pre className="max-h-64 overflow-auto rounded-xl bg-background p-3 text-xs text-ink">{JSON.stringify(row.changes, null, 2)}</pre>
                </td></tr>
            )}
        </>
    );
}

/**
 * Workspace audit log (WP13a): the existing admin-only audit endpoints —
 * filtered list, CSV export and hash-chain verification — as a page.
 */
export default function AuditLog() {
    const { workspaces, workspaceId, setWorkspaceId } = useComplianceWorkspace();
    const [draft, setDraft] = useState<Draft>(EMPTY);
    const [applied, setApplied] = useState<Draft>(EMPTY);
    const [offset, setOffset] = useState(0);
    const [verdict, setVerdict] = useState<ChainVerdict | null>(null);
    const filters = toFilters(applied);

    const rows = useQuery({
        queryKey: ['audit-log', workspaceId, applied, offset],
        queryFn: () => auditApi.list(workspaceId!, filters, PAGE, offset),
        enabled: workspaceId !== null,
    });
    const members = useQuery({
        queryKey: ['compliance-members', workspaceId],
        queryFn: () => complianceApi.members(workspaceId!),
        enabled: workspaceId !== null,
    });
    const people = Object.fromEntries((members.data ?? []).map((m) => [m.id, m.email]));
    const verify = useMutation({
        mutationFn: () => auditApi.verify(workspaceId!),
        onSuccess: setVerdict,
        onError: (e) => toast.error(errDetail(e)),
    });
    const exportCsv = () => downloadFromApi(auditApi.exportPath(workspaceId!, filters),
        `traceiq-audit-ws${workspaceId}.csv`).catch((e) => toast.error(errDetail(e)));

    const set = (k: keyof Draft, v: string) => setDraft((d) => ({ ...d, [k]: v }));
    const apply = () => { setApplied(draft); setOffset(0); };
    const clear = () => { setDraft(EMPTY); setApplied(EMPTY); setOffset(0); };
    const list = rows.data ?? [];

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Audit log"
                subtitle="Who did what in this workspace, newest first. Rows are append-only and hash-chained; Verify chain checks no row was changed after it was written."
                actions={
                    <>
                        <WorkspacePicker workspaces={workspaces} value={workspaceId} onChange={(id) => { setWorkspaceId(id); setOffset(0); setVerdict(null); }} />
                        <Button variant="outline" disabled={workspaceId === null} onClick={exportCsv}>Export CSV</Button>
                        <Button disabled={workspaceId === null || verify.isPending} onClick={() => verify.mutate()}>
                            {verify.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Verify chain
                        </Button>
                    </>
                }
                toolbar={
                    <form className="flex flex-wrap items-end gap-3 py-1" onSubmit={(e) => { e.preventDefault(); apply(); }}>
                        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
                            Entity type
                            <Input list="audit-entity-types" className="h-10 w-44 rounded-full" value={draft.entity_type} onChange={(e) => set('entity_type', e.target.value)} />
                            <datalist id="audit-entity-types">{ENTITY_TYPES.map((t) => <option key={t} value={t} />)}</datalist>
                        </label>
                        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
                            Action
                            <Input className="h-10 w-40 rounded-full" placeholder="e.g. update" value={draft.action} onChange={(e) => set('action', e.target.value)} />
                        </label>
                        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
                            User
                            <select className={selectClass} value={draft.user_id} onChange={(e) => set('user_id', e.target.value)}>
                                <option value="">Anyone</option>
                                {(members.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.email}</option>)}
                            </select>
                        </label>
                        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
                            From
                            <Input type="date" className="h-10 rounded-full" value={draft.since} onChange={(e) => set('since', e.target.value)} />
                        </label>
                        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
                            To
                            <Input type="date" className="h-10 rounded-full" value={draft.until} onChange={(e) => set('until', e.target.value)} />
                        </label>
                        <Button type="submit">Apply filters</Button>
                        <Button type="button" variant="outline" onClick={clear}>Clear</Button>
                    </form>
                }
            />

            {verdict && (
                <div className={verdict.verified ? 'rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink' : warnCalloutClass} role="status">
                    <Pill tone={verdict.verified ? 'mint' : 'peach'}>{!verdict.verified ? 'Chain broken' : verdict.status === 'partial' ? 'Verified since chaining began' : 'Chain verified'}</Pill>
                    <span className="ml-2">{verdict.rows_checked} rows checked. {verdict.detail}</span>
                    {verdict.first_bad_row_id && <span className="block text-xs text-ink-soft">First bad row #{verdict.first_bad_row_id} ({fmtDate(verdict.first_bad_row_timestamp)}){verdict.in_this_workspace ? ', in this workspace' : ''}</span>}
                </div>
            )}

            <div className="overflow-hidden rounded-3xl border border-border bg-white">
                {rows.isLoading || workspaceId === null ? (
                    <p className="flex items-center gap-2 p-6 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>
                ) : rows.isError ? (
                    <div className="p-6"><EmptyState title="The audit log is not available">{errDetail(rows.error)}</EmptyState></div>
                ) : list.length === 0 ? (
                    <div className="p-6"><EmptyState title={offset ? 'No more entries' : 'No entries match'} /></div>
                ) : (
                    <div className="overflow-x-auto px-5 py-3">
                        <table className="w-full min-w-[720px] text-left">
                            <thead>
                                <tr className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                                    <th className="py-2 pr-3">When</th><th className="py-2 pr-3">Who</th><th className="py-2 pr-3">Action</th>
                                    <th className="py-2 pr-3">Entity</th><th className="py-2 pr-3">IP</th><th className="py-2"><span className="sr-only">Details</span></th>
                                </tr>
                            </thead>
                            <tbody>{list.map((r) => <Row key={r.id} row={r} people={people} />)}</tbody>
                        </table>
                    </div>
                )}
                <div className="flex flex-wrap items-center gap-3 border-t border-border px-5 py-3 text-sm">
                    <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE))}>Previous page</Button>
                    <Button size="sm" variant="outline" disabled={list.length < PAGE} onClick={() => setOffset((o) => o + PAGE)}>Next page</Button>
                    <span className="text-ink-soft">Entries {list.length ? offset + 1 : 0}–{offset + list.length}</span>
                </div>
            </div>
        </div>
    );
}
