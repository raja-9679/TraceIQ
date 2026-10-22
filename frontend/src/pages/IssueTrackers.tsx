import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Ticket, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces, Workspace } from '@/lib/api';
import { ticketsApi, IssueTrackerConfig, IssueTrackerConfigCreate } from '@/api/tickets';
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspace';
import { usePermission } from '@/hooks/usePermission';
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const PROVIDER_HINTS: Record<string, { user: string; secret: string; settings: string }> = {
    jira: { user: 'Jira email', secret: 'API token', settings: 'project_key, issue_type' },
    itop: { user: 'iTop username', secret: 'Password', settings: 'class (UserRequest), org_id' },
    github: { user: '(unused)', secret: 'Personal access token', settings: 'repo (owner/name)' },
    linear: { user: '(unused)', secret: 'Linear API key', settings: 'none — import-only (Requirements page)' },
};
const PROVIDER_LABELS: Record<string, string> = { jira: 'Jira', itop: 'iTop', github: 'GitHub Issues', linear: 'Linear (import only)' };

const fieldLabel = 'px-1 text-[13px] font-semibold text-ink';
const fieldInput = 'mt-1.5 h-11 rounded-full bg-white px-4';

function ProviderChip() {
    return (
        <span className="flex h-7 w-7 flex-none items-center justify-center rounded-[9px] bg-pastel-lavender text-pastel-ink-lavender" aria-hidden="true">
            <Ticket className="h-[15px] w-[15px]" strokeWidth={2.2} />
        </span>
    );
}

function NewConfigForm({ workspaceId, onDone }: { workspaceId: number; onDone: () => void }) {
    const qc = useQueryClient();
    const [f, setF] = useState<IssueTrackerConfigCreate>({
        provider: 'jira', name: '', base_url: '', auth_user: '', auth_secret: '', settings: {}, enabled: true,
    });
    const [settingsText, setSettingsText] = useState('{\n  "project_key": "PROJ",\n  "issue_type": "Bug"\n}');

    const create = useMutation({
        mutationFn: () => {
            let settings = {};
            try { settings = settingsText.trim() ? JSON.parse(settingsText) : {}; }
            catch { throw new Error('Settings must be valid JSON'); }
            return ticketsApi.createConfig(workspaceId, { ...f, settings });
        },
        onSuccess: () => { toast.success('Tracker added'); qc.invalidateQueries({ queryKey: ['trackers', workspaceId] }); onDone(); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const hint = PROVIDER_HINTS[f.provider];
    return (
        <DetailCard label="New tracker">
            <div className="flex flex-col gap-1.5">
                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">New tracker</h2>
                <span className="text-sm text-ink-soft">Credentials are stored encrypted and used only to file tickets.</span>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
                <div>
                    <label htmlFor="tracker-provider" className={fieldLabel}>Provider</label>
                    <Select value={f.provider} onValueChange={(v) => setF({ ...f, provider: v })}>
                        <SelectTrigger id="tracker-provider" className="mt-1.5 h-11 rounded-full bg-white px-4"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="jira">Jira</SelectItem>
                            <SelectItem value="itop">iTop</SelectItem>
                            <SelectItem value="github">GitHub Issues</SelectItem>
                            <SelectItem value="linear">Linear (import only)</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div>
                    <label htmlFor="tracker-name" className={fieldLabel}>Name</label>
                    <Input id="tracker-name" className={fieldInput} placeholder="Team Jira" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
                </div>
            </div>
            <div>
                <label htmlFor="tracker-url" className={fieldLabel}>Base URL</label>
                <Input id="tracker-url" type="url" className={fieldInput} placeholder={f.provider === 'github' ? 'https://api.github.com' : f.provider === 'linear' ? 'https://api.linear.app/graphql (default)' : 'https://your.instance'} value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
                <div>
                    <label htmlFor="tracker-user" className={fieldLabel}>{hint.user}</label>
                    <Input id="tracker-user" autoComplete="off" className={fieldInput} value={f.auth_user} onChange={(e) => setF({ ...f, auth_user: e.target.value })} disabled={f.provider === 'github' || f.provider === 'linear'} />
                </div>
                <div>
                    <label htmlFor="tracker-secret" className={fieldLabel}>{hint.secret}</label>
                    <Input id="tracker-secret" autoComplete="new-password" className={fieldInput} type="password" placeholder="stored encrypted" value={f.auth_secret} onChange={(e) => setF({ ...f, auth_secret: e.target.value })} />
                </div>
            </div>
            <div>
                <label htmlFor="tracker-settings" className={fieldLabel}>
                    Settings JSON <span className="font-normal text-ink-soft">({hint.settings})</span>
                </label>
                <textarea
                    id="tracker-settings"
                    className="mt-1.5 h-28 w-full rounded-2xl border border-border bg-white p-3 font-mono text-xs text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/25"
                    value={settingsText}
                    onChange={(e) => setSettingsText(e.target.value)}
                />
            </div>
            <div className="flex flex-wrap gap-2.5">
                <Button size="lg" onClick={() => create.mutate()} disabled={create.isPending || !f.name || (!f.base_url && f.provider !== 'linear') || !f.auth_secret}>Add tracker</Button>
                <Button size="lg" variant="outline" onClick={onDone}>Cancel</Button>
            </div>
        </DetailCard>
    );
}

export default function IssueTrackers() {
    const qc = useQueryClient();
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const [adding, setAdding] = useState(false);
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const activeWs = useActiveWorkspaceId();
    const workspaceId = selectedWs ?? activeWs ?? workspaces?.[0]?.id ?? null;
    // Adding, changing and removing a tracker is workspace-admin only (the
    // connection holds a shared credential); members can only look.
    const { can } = usePermission();
    const isAdmin = !!workspaceId && can('workspace:manage_settings', { workspaceId });

    const configs = useQuery({
        queryKey: ['trackers', workspaceId],
        queryFn: () => ticketsApi.listConfigs(workspaceId!),
        enabled: !!workspaceId,
    });

    const del = useMutation({
        mutationFn: (id: number) => ticketsApi.deleteConfig(workspaceId!, id),
        onSuccess: () => { toast.success('Tracker removed'); qc.invalidateQueries({ queryKey: ['trackers', workspaceId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const toggle = useMutation({
        mutationFn: (c: IssueTrackerConfig) => ticketsApi.updateConfig(workspaceId!, c.id, { enabled: !c.enabled }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['trackers', workspaceId] }),
        onError: (e) => toast.error(errDetail(e)),
    });

    const list = configs.data ?? [];
    const selected = list.find((c) => c.id === selectedId) ?? list[0] ?? null;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Issue trackers"
                subtitle="Connect Jira, iTop or GitHub so you can file a ticket from a failing run, with its trace, video and screenshots attached, in one click."
                actions={
                    <>
                        <Select value={workspaceId?.toString() ?? ''} onValueChange={(v) => { setSelectedWs(parseInt(v)); setSelectedId(null); }}>
                            <SelectTrigger aria-label="Workspace" className="h-10 w-[220px] rounded-full bg-white px-4">
                                <SelectValue placeholder="Workspace" />
                            </SelectTrigger>
                            <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                        </Select>
                        {workspaceId && isAdmin && !adding && (
                            <Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> Add tracker</Button>
                        )}
                    </>
                }
            />

            {!workspaceId ? (
                <EmptyState title="No workspace available">Create or join a workspace to connect an issue tracker.</EmptyState>
            ) : configs.isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : list.length === 0 && !adding ? (
                <EmptyState title="No trackers yet">
                    {isAdmin ? 'Add one to start filing tickets from runs.' : 'A workspace admin can add one to start filing tickets from runs.'}
                </EmptyState>
            ) : (
                <SplitView
                    list={
                        list.length > 0 ? (
                            <ListGroup title="Trackers" count={list.length}>
                                {list.map((c) => (
                                    <ListRow
                                        key={c.id}
                                        selected={!adding && selected?.id === c.id}
                                        onSelect={() => { setSelectedId(c.id); setAdding(false); }}
                                        leading={<ProviderChip />}
                                        title={c.name}
                                        meta={`${PROVIDER_LABELS[c.provider] ?? c.provider} · ${c.base_url}`}
                                        trailing={<Pill tone={c.enabled ? 'mint' : 'neutral'}>{c.enabled ? 'On' : 'Off'}</Pill>}
                                    />
                                ))}
                            </ListGroup>
                        ) : (
                            <p className="px-1 text-sm text-ink-soft">No trackers yet.</p>
                        )
                    }
                    detail={
                        adding ? (
                            <NewConfigForm workspaceId={workspaceId} onDone={() => setAdding(false)} />
                        ) : selected && (
                            <DetailCard label="Selected tracker">
                                <div className="flex flex-col gap-2">
                                    <Pill tone={selected.enabled ? 'mint' : 'neutral'} className="self-start">
                                        {selected.enabled ? 'Enabled' : 'Disabled'}
                                    </Pill>
                                    <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                                    <span className="text-sm text-ink-soft">{PROVIDER_LABELS[selected.provider] ?? selected.provider}</span>
                                </div>

                                <div className="flex flex-col gap-2">
                                    <Eyebrow>Connection</Eyebrow>
                                    <dl className="overflow-hidden rounded-2xl border border-border text-sm">
                                        <div className="flex gap-3 px-4 py-3">
                                            <dt className="w-28 flex-none text-ink-soft">Base URL</dt>
                                            <dd className="min-w-0 break-all font-mono text-xs leading-5 text-ink">{selected.base_url}</dd>
                                        </div>
                                        {selected.auth_user && (
                                            <div className="flex gap-3 border-t border-[#F0EEE8] px-4 py-3">
                                                <dt className="w-28 flex-none text-ink-soft">User</dt>
                                                <dd className="min-w-0 break-all text-ink">{selected.auth_user}</dd>
                                            </div>
                                        )}
                                    </dl>
                                </div>

                                <Panel title="What this does">
                                    {selected.enabled
                                        ? 'Offered under "Create ticket" on runs and in Triage for this workspace. The ticket carries the run’s evidence.'
                                        : 'Turned off. It stays configured but is not offered under "Create ticket" until you enable it again.'}
                                </Panel>

                                {isAdmin ? <div className="flex flex-wrap gap-2.5">
                                    <Button size="lg" variant={selected.enabled ? 'outline' : 'default'} onClick={() => toggle.mutate(selected)} disabled={toggle.isPending}>
                                        {selected.enabled ? 'Disable' : 'Enable'}
                                    </Button>
                                    <Button
                                        size="lg"
                                        variant="outline"
                                        className="text-pastel-ink-peach hover:bg-pastel-peach hover:text-pastel-ink-peach"
                                        onClick={() => { if (window.confirm(`Remove the tracker "${selected.name}"? Tickets already filed stay in the tracker.`)) del.mutate(selected.id); }}
                                        disabled={del.isPending}
                                    >
                                        <Trash2 className="h-4 w-4" /> Remove
                                    </Button>
                                </div> : <p className="text-xs text-ink-soft">Only a workspace admin can change or remove this connection.</p>}
                            </DetailCard>
                        )
                    }
                />
            )}
        </div>
    );
}
