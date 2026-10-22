import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';
import { SettingsCard, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { WorkspacePicker } from '@/components/compliance/WorkspacePicker';
import { useComplianceWorkspace } from '@/components/compliance/useComplianceWorkspace';
import { AgentList, ControlForm, SessionsCard, TimelineCard } from '@/components/agents/AgentCards';
import { WILDCARD, inheritedLimits, realAgents } from '@/components/agents/agentHelpers';
import { errDetail } from '@/lib/errors';
import { agentsApi } from '@/api/policies';

// Agents (WP14b): what each AI agent did in the workspace, per-session
// budgets and the per-agent kill switch. Workspace admins only.
export default function Agents() {
    const { workspaces, workspaceId, setWorkspaceId, loading } = useComplianceWorkspace();
    const [selected, setSelected] = useState<string | null>(null);
    const [session, setSession] = useState<string | null>(null);
    const [newAgent, setNewAgent] = useState('');
    const overview = useQuery({
        queryKey: ['agents', workspaceId],
        queryFn: () => agentsApi.overview(workspaceId!),
        enabled: !!workspaceId,
        retry: false,
    });
    const data = overview.data;
    const pick = (id: string | null) => { setSelected(id); setSession(null); };
    const changeWorkspace = (id: number) => { setWorkspaceId(id); pick(null); };
    const known = data?.agents.find((a) => a.agent_id === selected);
    const inherited = data ? inheritedLimits(data.workspace_default, data.instance_defaults) : null;

    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="Agents"
                subtitle="AI agents that call TraceIQ with X-Agent-Id: what each session did, its budget, and a kill switch."
                toolbar={workspaces.length > 1 ? <WorkspacePicker workspaces={workspaces} value={workspaceId} onChange={changeWorkspace} /> : undefined}
            />
            {loading || (workspaceId && overview.isLoading) ? <SkeletonRows count={4} /> : !workspaceId ? (
                <EmptyState title="No workspace">Join or create a workspace first.</EmptyState>
            ) : overview.isError || !data || !inherited ? (
                <EmptyState title="Workspace admins only">{errDetail(overview.error)}</EmptyState>
            ) : (
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                    <div className="flex flex-col gap-5">
                        <AgentList agents={realAgents(data.agents)} selected={selected} onSelect={pick} />
                        <SettingsCard
                            labelledBy="add-agent"
                            icon={<Plus className="h-4 w-4" />}
                            title="Set controls for an agent id"
                            description="Block or budget an agent before it has worked here, e.g. one you are about to give an API key."
                        >
                            <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
                                e.preventDefault();
                                if (newAgent.trim()) { pick(newAgent.trim()); setNewAgent(''); }
                            }}>
                                <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm font-medium text-ink">Agent id
                                    <Input className={inputClass} value={newAgent} maxLength={200} placeholder="claude-code"
                                        onChange={(e) => setNewAgent(e.target.value)} />
                                </label>
                                <Button type="submit" disabled={!newAgent.trim() || newAgent.trim() === WILDCARD}>Open</Button>
                            </form>
                        </SettingsCard>
                        <ControlForm key={`${workspaceId}-default-${data.workspace_default?.updated_at ?? 'none'}`}
                            workspaceId={workspaceId} agentId={WILDCARD} control={data.workspace_default}
                            inherited={data.instance_defaults} />
                    </div>
                    <div className="flex flex-col gap-5">
                        {!selected ? (
                            <EmptyState title="Pick an agent">Open an agent to see its sessions and change its controls.</EmptyState>
                        ) : (
                            <>
                                <ControlForm key={`${workspaceId}-${selected}-${known?.control?.updated_at ?? 'none'}`}
                                    workspaceId={workspaceId} agentId={selected} control={known?.control ?? null}
                                    inherited={inherited} />
                                <SessionsCard workspaceId={workspaceId} agentId={selected} selected={session} onSelect={setSession} />
                                {session && <TimelineCard workspaceId={workspaceId} agentId={selected} sessionId={session} />}
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
