import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { pagesApi } from '@/api/estates';
import { errDetail } from '@/lib/errors';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { Button } from '@/components/ui/button';
import {
    DetailCard, EmptyState, ListGroup, ListRow, PageHeader, Pill, SplitView,
} from '@/components/redesign/primitives';
import { statusPill } from '@/components/estate/helpers';
import PageForm from '@/components/scripts/PageForm';
import PageView from '@/components/scripts/PageView';

const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function ScriptInventoryPage() {
    const projectId = useActiveProjectId();
    const [params, setParams] = useSearchParams();
    const pages = useQuery({
        queryKey: ['monitored-pages', projectId], queryFn: () => pagesApi.list(projectId!), enabled: !!projectId,
    });
    const go = (v: string | number | null) => setParams(v === null ? new URLSearchParams() : new URLSearchParams({ page: String(v) }));

    // A sidebar project switch drops the previous project's selection, as the
    // old in-page picker did; a deep link on first load is kept.
    const lastProject = useRef(projectId);
    useEffect(() => {
        if (lastProject.current !== projectId) {
            lastProject.current = projectId;
            setParams(new URLSearchParams());
        }
    }, [projectId, setParams]);

    const list = pages.data ?? [];
    const sel = params.get('page');
    const view = sel === 'new' ? 'new' : num(sel);
    // Nothing picked yet: show the first page in the list.
    const page = typeof view === 'number' ? list.find(p => p.id === view) ?? null : view === null ? list[0] ?? null : null;
    const unapproved = list.reduce((n, p) => n + (p.unapproved_count ?? 0), 0);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Script inventory"
                subtitle={projectId && list.length
                    ? `${list.length} monitored page${list.length === 1 ? '' : 's'} · ${unapproved} unapproved script${unapproved === 1 ? '' : 's'} · PCI DSS 4.0 6.4.3 and 11.6.1`
                    : 'Every script on your payment pages, each approved with a justification, and an alert when one appears or changes (PCI DSS 4.0 6.4.3 and 11.6.1). Optional defacement checks on the same load.'}
                actions={projectId ? (
                    <Button type="button" onClick={() => go('new')}><Plus className="h-4 w-4" /> Monitor a page</Button>
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to manage its monitored pages.</EmptyState>
            ) : pages.error ? (
                <EmptyState title="Couldn't load monitored pages">{errDetail(pages.error)}</EmptyState>
            ) : pages.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : (
                <SplitView
                    list={
                        <nav aria-label="Monitored pages">
                            <ListGroup title="Monitored pages" count={list.length}>
                                {list.length === 0 && <p className="px-4 py-6 text-center text-sm text-ink-soft">No monitored pages yet.</p>}
                                {list.map(p => (
                                    <ListRow
                                        key={p.id}
                                        selected={page?.id === p.id && view !== 'new'}
                                        onSelect={() => go(p.id)}
                                        title={<span className="break-words">{p.name}</span>}
                                        meta={`${p.script_count ?? 0} script(s)${p.unapproved_count ? ` · ${p.unapproved_count} unapproved` : ''}`}
                                        trailing={<Pill tone={statusPill(p.last_status)}>{p.last_status ?? 'new'}</Pill>}
                                    />
                                ))}
                            </ListGroup>
                        </nav>
                    }
                    detail={
                        view === 'new' ? (
                            <DetailCard label="Monitor a page">
                                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Monitor a page</h2>
                                <PageForm projectId={projectId} page={null} onSaved={(p) => go(p.id)} />
                            </DetailCard>
                        ) : !page ? (
                            list.length === 0
                                ? <EmptyState title="No monitored pages yet">Use “Monitor a page” to start an inventory of a payment page’s scripts.</EmptyState>
                                : <EmptyState title="Page not found">This page is not in the selected project.</EmptyState>
                        ) : (
                            <PageView key={page.id} projectId={projectId} page={page} onDeleted={() => go(null)} />
                        )
                    }
                />
            )}
        </div>
    );
}
