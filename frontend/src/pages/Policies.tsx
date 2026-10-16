import { useQuery } from '@tanstack/react-query';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';
import { SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { WorkspacePicker } from '@/components/compliance/WorkspacePicker';
import { useComplianceWorkspace } from '@/components/compliance/useComplianceWorkspace';
import { AiPolicyCard, DenialsCard, RulesCard } from '@/components/agents/PolicyCards';
import { errDetail } from '@/lib/errors';
import { policiesApi } from '@/api/policies';

// Policies (WP14b): the workspace AI switch, every rule the policy engine
// evaluates, and what it refused recently.
export default function Policies() {
    const { workspaces, workspaceId, setWorkspaceId, loading } = useComplianceWorkspace();
    const ai = useQuery({
        queryKey: ['ai-policy', workspaceId],
        queryFn: () => policiesApi.aiPolicy(workspaceId!),
        enabled: !!workspaceId,
        retry: false,
    });
    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="Policies"
                subtitle="One place for the checks that can refuse an action: plan quotas, capture and network limits, AI use, agent budgets, proposal review and scan authorization."
                toolbar={workspaces.length > 1 ? <WorkspacePicker workspaces={workspaces} value={workspaceId} onChange={setWorkspaceId} /> : undefined}
            />
            {loading ? <SkeletonRows count={3} /> : !workspaceId ? (
                <EmptyState title="No workspace">Join or create a workspace first.</EmptyState>
            ) : (
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                    <div className="flex flex-col gap-5">
                        {ai.data ? <AiPolicyCard key={`${workspaceId}-${ai.data.ai_enabled}`} policy={ai.data} />
                            : ai.isError ? <EmptyState title="AI policy unavailable">{errDetail(ai.error)}</EmptyState>
                                : <SkeletonRows count={2} />}
                        <RulesCard />
                    </div>
                    <DenialsCard key={workspaceId} workspaceId={workspaceId} />
                </div>
            )}
        </div>
    );
}
