import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Button } from '@/components/ui/button';
import { EmptyState, ListCard, ListRow, PageHeader, Pill, SplitView } from '@/components/redesign/primitives';
import { complianceApi, type ControlStatus } from '@/api/compliance';
import { ControlForm } from '@/components/compliance/ControlForm';
import { ControlDetailPanel } from '@/components/compliance/ControlDetailPanel';
import { WorkspacePicker } from '@/components/compliance/WorkspacePicker';
import { useComplianceWorkspace } from '@/components/compliance/useComplianceWorkspace';
import { NO_FILTERS, STATUS_META, STATUS_ORDER, filterControls, sortControls, type ControlFilters } from '@/components/compliance/helpers';
import { selectClass } from '@/components/compliance/selectClass';

/**
 * Controls (WP13a): what the workspace claims, who owns it, and whether the
 * evidence still holds. Members read; workspace admins edit and approve.
 */
export default function ComplianceControls() {
    const { controlId } = useParams();
    const navigate = useNavigate();
    const qc = useQueryClient();
    const { workspaces, workspaceId, setWorkspaceId } = useComplianceWorkspace();
    const [filters, setFilters] = useState<ControlFilters>(NO_FILTERS);
    const [creating, setCreating] = useState(false);
    const selected = controlId ? Number(controlId) : null;

    const list = useQuery({
        queryKey: ['compliance-controls', workspaceId],
        queryFn: () => complianceApi.list(workspaceId!),
        enabled: workspaceId !== null,
    });
    const sources = useQuery({ queryKey: ['compliance-sources'], queryFn: complianceApi.sources });
    const members = useQuery({
        queryKey: ['compliance-members', workspaceId],
        queryFn: () => complianceApi.members(workspaceId!),
        enabled: workspaceId !== null,
    });

    const seed = useMutation({
        mutationFn: () => complianceApi.loadSoc2(workspaceId!),
        onSuccess: (r) => {
            toast.success(r.created ? `Added ${r.created} SOC 2 controls` : 'The SOC 2 starter set is already loaded');
            qc.invalidateQueries({ queryKey: ['compliance-controls', workspaceId] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const create = useMutation({
        mutationFn: (body: Parameters<typeof complianceApi.create>[1]) => complianceApi.create(workspaceId!, body),
        onSuccess: (c) => {
            toast.success('Control created');
            setCreating(false);
            qc.invalidateQueries({ queryKey: ['compliance-controls', workspaceId] });
            navigate(`/compliance/controls/${c.id}`);
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    const data = list.data;
    const shown = data ? sortControls(filterControls(data.controls, filters)) : [];
    const select = (id: number | null) => navigate(id ? `/compliance/controls/${id}` : '/compliance/controls');
    const setFilter = (k: keyof ControlFilters, v: string) => setFilters((f) => ({ ...f, [k]: v }));
    const owners = members.data ?? [];

    const toolbar = data && data.controls.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 py-1">
            <label className="flex items-center gap-2 text-sm font-medium text-ink">
                Framework
                <select className={selectClass} value={filters.framework} onChange={(e) => setFilter('framework', e.target.value)}>
                    <option value="">All</option>
                    {data.frameworks.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
            </label>
            <label className="flex items-center gap-2 text-sm font-medium text-ink">
                Status
                <select className={selectClass} value={filters.status} onChange={(e) => setFilter('status', e.target.value)}>
                    <option value="">All</option>
                    {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_META[s].label} ({data.counts[s] ?? 0})</option>)}
                </select>
            </label>
            <label className="flex items-center gap-2 text-sm font-medium text-ink">
                Owner
                <select className={selectClass} value={filters.owner} onChange={(e) => setFilter('owner', e.target.value)}>
                    <option value="">Anyone</option>
                    <option value="none">No owner</option>
                    {owners.map((m) => <option key={m.id} value={m.id}>{m.email}</option>)}
                </select>
            </label>
            {(filters.framework || filters.status || filters.owner) && (
                <Button size="sm" variant="outline" onClick={() => setFilters(NO_FILTERS)}>Clear filters</Button>
            )}
        </div>
    ) : undefined;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Controls"
                subtitle="Each control's status is worked out live from TraceIQ's own records (access reviews, the audit chain, data policies, findings, restore drills) plus the evidence people add."
                badge={data && (
                    <span className="flex flex-wrap gap-1.5">
                        {STATUS_ORDER.filter((s) => data.counts[s]).map((s) => (
                            <Pill key={s} tone={STATUS_META[s].tone}>{data.counts[s]} {STATUS_META[s].label.toLowerCase()}</Pill>
                        ))}
                    </span>
                )}
                actions={
                    <>
                        <WorkspacePicker workspaces={workspaces} value={workspaceId} onChange={(id) => { setWorkspaceId(id); select(null); }} />
                        {data?.can_edit && (
                            <>
                                <Button variant="outline" disabled={seed.isPending} onClick={() => seed.mutate()}>
                                    {seed.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Load SOC 2 starter set
                                </Button>
                                <Button onClick={() => { setCreating(true); select(null); }}>New control</Button>
                            </>
                        )}
                    </>
                }
                toolbar={toolbar}
            />

            {creating && sources.data && (
                <ControlForm
                    title="New control" submitLabel="Create control"
                    initial={{ framework: 'SOC 2', reference: '', title: '', description: '', owner_id: null, evidence_sources: [], freshness_days: 90 }}
                    sources={sources.data} members={owners} busy={create.isPending}
                    onSubmit={(v) => create.mutate(v)} onCancel={() => setCreating(false)}
                />
            )}

            {list.isLoading || workspaceId === null ? (
                <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>
            ) : list.isError ? (
                <EmptyState title="Controls are not available">{errDetail(list.error)}</EmptyState>
            ) : data && data.controls.length === 0 ? (
                <EmptyState title="No controls yet">
                    {data.can_edit
                        ? 'Load the SOC 2 starter set to begin from docs/SOC2_CONTROLS.md — every control is editable — or create your own.'
                        : 'A workspace admin has not defined any controls yet.'}
                </EmptyState>
            ) : (
                <SplitView
                    list={
                        <ListCard title="Controls" count={shown.length}>
                            {shown.length === 0 ? (
                                <p className="px-4 py-6 text-sm text-ink-soft">No control matches these filters.</p>
                            ) : shown.map((c) => (
                                <ListRow
                                    key={c.id}
                                    selected={c.id === selected}
                                    onSelect={() => { setCreating(false); select(c.id); }}
                                    title={<>{c.reference} · {c.title}</>}
                                    meta={<>{c.framework} · {c.owner ?? 'no owner'}{c.pending_exceptions ? ` · ${c.pending_exceptions} exception pending` : ''}</>}
                                    trailing={<Pill tone={STATUS_META[c.status as ControlStatus].tone}>{STATUS_META[c.status as ControlStatus].label}</Pill>}
                                />
                            ))}
                        </ListCard>
                    }
                    detail={selected && sources.data ? (
                        <ControlDetailPanel key={selected} controlId={selected} sources={sources.data} members={owners}
                            onDeleted={() => select(null)} />
                    ) : (
                        <EmptyState title="Pick a control">Its automated checks, manual evidence, exceptions and history open here.</EmptyState>
                    )}
                />
            )}
        </div>
    );
}
