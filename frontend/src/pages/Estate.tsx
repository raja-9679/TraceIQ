import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { estatesApi } from '@/api/estates';
import { getProjects } from '@/lib/api';
import { errDetail } from '@/lib/errors';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { Button } from '@/components/ui/button';
import {
    DetailCard, EmptyState, ListGroup, ListRow, PageHeader, PanelTabs, Pill, SplitView,
} from '@/components/redesign/primitives';
import CertCalendar from '@/components/estate/CertCalendar';
import EstateForm from '@/components/estate/EstateForm';
import EstateView from '@/components/estate/EstateView';
import { scorePill } from '@/components/estate/helpers';

// Thin shell: estate list and (new | estate | certificates) for the sidebar's
// active project. Selection lives in the URL (?estate=&domain=) so a domain
// can be deep-linked.

const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function EstatePage() {
    const projectId = useActiveProjectId();
    const [params, setParams] = useSearchParams();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const estates = useQuery({
        queryKey: ['estates', projectId], queryFn: () => estatesApi.list(projectId!), enabled: !!projectId,
    });
    const project = projects.data?.find(p => p.id === projectId);

    // A sidebar project switch drops the previous project's selection, as the
    // old in-page picker did; a deep link on first load is kept.
    const lastProject = useRef(projectId);
    useEffect(() => {
        if (lastProject.current !== projectId) {
            lastProject.current = projectId;
            setParams(new URLSearchParams());
        }
    }, [projectId, setParams]);

    const sel = params.get('estate');
    const view = sel === 'new' ? 'new' : sel === 'certs' ? 'certs' : num(sel);
    const list = estates.data ?? [];
    // Nothing picked yet: show the first (worst-first) estate.
    const estateId = typeof view === 'number' ? view : view === null ? list[0]?.id ?? null : null;

    const go = (next: Record<string, string | number | null>) => {
        const p = new URLSearchParams(params);
        for (const [k, v] of Object.entries(next)) {
            if (v === null) p.delete(k); else p.set(k, String(v));
        }
        setParams(p);
    };

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Estate"
                subtitle="Every domain you own, one score each: email authentication, certificates, HSTS, dangling DNS — checked passively, worst first."
                actions={projectId ? (
                    <Button type="button" onClick={() => go({ estate: 'new', domain: null })}>
                        <Plus className="h-4 w-4" /> New estate
                    </Button>
                ) : undefined}
                toolbar={projectId ? (
                    <PanelTabs
                        label="Estate views"
                        value={view === 'certs' ? 'certs' : 'estates'}
                        onChange={(t) => go({ estate: t === 'certs' ? 'certs' : null, domain: null })}
                        tabs={[
                            { id: 'estates', label: 'Estates', count: estates.data ? list.length : undefined },
                            { id: 'certs', label: 'Certificate calendar' },
                        ]}
                    />
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to manage its estates.</EmptyState>
            ) : estates.error ? (
                <EmptyState title="Couldn't load estates">{errDetail(estates.error)}</EmptyState>
            ) : view === 'certs' ? (
                <DetailCard label="Certificate calendar">
                    <div className="flex flex-col gap-1.5">
                        <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Certificate calendar</h2>
                        <span className="text-sm text-ink-soft">Certificate expiries across every estate in this workspace you can read, soonest first.</span>
                    </div>
                    {project ? <CertCalendar workspaceId={project.workspace_id} />
                        : <p className="text-sm text-ink-soft">{projects.isLoading ? 'Loading…' : 'Project not found.'}</p>}
                </DetailCard>
            ) : estates.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : (
                <SplitView
                    list={
                        <nav aria-label="Estates">
                            <ListGroup title="Estates" count={list.length}>
                                {list.length === 0 && <p className="px-4 py-6 text-center text-sm text-ink-soft">No estates in this project yet.</p>}
                                {list.map(e => (
                                    <ListRow
                                        key={e.id}
                                        selected={estateId === e.id}
                                        onSelect={() => go({ estate: e.id, domain: null })}
                                        title={<span className="break-words">{e.name}</span>}
                                        meta={`${e.domain_count ?? 0} domain(s)${e.average_score != null ? ` · average ${e.average_score}` : ''}`}
                                        trailing={
                                            <span title="Worst domain score">
                                                <Pill tone={scorePill(e.worst_score)} className="tabular-nums">
                                                    <span className="sr-only">Worst score </span>{e.worst_score ?? '—'}
                                                </Pill>
                                            </span>
                                        }
                                    />
                                ))}
                            </ListGroup>
                        </nav>
                    }
                    detail={
                        view === 'new' ? (
                            <DetailCard label="New estate">
                                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">New estate</h2>
                                <EstateForm projectId={projectId} estate={null} onSaved={(e) => go({ estate: e.id })} />
                            </DetailCard>
                        ) : estateId === null ? (
                            <EmptyState title="No estates yet">Use “New estate” to group the domains you own and score them.</EmptyState>
                        ) : (
                            <EstateView key={estateId} projectId={projectId} estateId={estateId} domainId={num(params.get('domain'))}
                                onSelectDomain={(id) => go({ estate: estateId, domain: id })} onDeleted={() => go({ estate: null, domain: null })} />
                        )
                    }
                />
            )}
        </div>
    );
}
