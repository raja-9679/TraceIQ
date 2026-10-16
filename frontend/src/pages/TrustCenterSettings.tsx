import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getWorkspaces, type Workspace } from '@/lib/api';
import { trustApi } from '@/api/trustCenter';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';
import { SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { TrustGeneralCard, TrustPostureCard, TrustStatusCard } from '@/components/trust/TrustConfigCards';
import { TrustIncidentsCard } from '@/components/trust/TrustIncidentsCard';
import { TrustDocumentsCard } from '@/components/trust/TrustDocumentsCard';
import { errDetail } from '@/components/trust/trustHelpers';

// Workspace admins configure the public trust center (/trust/:slug):
// uptime, incident posts, aggregate posture and gated documents.
export default function TrustCenterSettings() {
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const wsId = selectedWs ?? workspaces?.[0]?.id ?? null;
    const cfg = useQuery({ queryKey: ['trust-center', wsId], queryFn: () => trustApi.get(wsId!), enabled: !!wsId, retry: false });
    const c = cfg.data;

    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="Trust center"
                subtitle="A public page for customers and prospects: uptime, incident updates, an aggregate security posture and documents you share on request."
                actions={c?.enabled && c.path
                    ? <Button variant="outline" asChild><a href={c.path} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" /> Open public page</a></Button>
                    : undefined}
                toolbar={workspaces && workspaces.length > 1 ? (
                    <label className="flex items-center gap-2 text-[13px] text-ink-soft">Workspace
                        <select className="h-10 rounded-full border border-input bg-white px-4 text-sm text-ink" value={wsId ?? ''}
                            onChange={(e) => setSelectedWs(parseInt(e.target.value))}>
                            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                    </label>
                ) : undefined}
            />
            {!wsId ? (
                <EmptyState title="No workspace">Join or create a workspace first.</EmptyState>
            ) : cfg.isLoading ? (
                <SkeletonRows count={4} />
            ) : cfg.isError || !c ? (
                <EmptyState title="Workspace admins only">{errDetail(cfg.error)}</EmptyState>
            ) : (
                // Keyed by workspace + save state so each card's form starts from the saved values.
                <div key={`${wsId}-${c.configured}`} className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                    <TrustGeneralCard cfg={c} />
                    <TrustPostureCard cfg={c} />
                    <TrustStatusCard cfg={c} />
                    <TrustDocumentsCard workspaceId={wsId} configured={c.configured} />
                    <div className="xl:col-span-2"><TrustIncidentsCard workspaceId={wsId} /></div>
                </div>
            )}
        </div>
    );
}
