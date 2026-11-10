import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { accessMatrixApi } from '@/api/accessMatrix';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { Button } from '@/components/ui/button';
import {
    DetailCard, EmptyState, PageHeader, PanelTabs, SplitView,
} from '@/components/redesign/primitives';
import MatrixEditor from '@/components/access-matrix/MatrixEditor';
import MatrixList from '@/components/access-matrix/MatrixList';
import RunsView from '@/components/access-matrix/RunsView';
import { errorDetail } from '@/components/access-matrix/grid';

// Thin shell: list + (editor | runs) for the sidebar's active project.
// Selection lives in the URL (?matrix=&tab=&run=&cell=) so a finding or a
// teammate can deep-link to one cell's evidence.

const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function AccessMatrixPage() {
    const projectId = useActiveProjectId();
    const [params, setParams] = useSearchParams();
    const matrices = useQuery({
        queryKey: ['access-matrices', projectId],
        queryFn: () => accessMatrixApi.list(projectId!),
        enabled: !!projectId,
    });

    // Switching project in the sidebar drops a selection that belonged to the
    // previous project (the old in-page picker did the same). A deep link on
    // first load is kept.
    const lastProject = useRef(projectId);
    useEffect(() => {
        if (lastProject.current !== projectId) {
            lastProject.current = projectId;
            setParams(new URLSearchParams());
        }
    }, [projectId, setParams]);

    const sel = params.get('matrix');
    // Nothing in the URL → show the first matrix (same as Estate / Script
    // inventory); the URL is only written when the user picks one.
    const selectedId: number | 'new' | null = sel === 'new' ? 'new' : num(sel) ?? matrices.data?.[0]?.id ?? null;
    const tab = params.get('tab') === 'runs' ? 'runs' : 'edit';
    const matrix = typeof selectedId === 'number' ? matrices.data?.find(m => m.id === selectedId) ?? null : null;

    const go = (next: Record<string, string | number | null>) => {
        const p = new URLSearchParams(params);
        for (const [k, v] of Object.entries(next)) {
            if (v === null) p.delete(k); else p.set(k, String(v));
        }
        setParams(p, { replace: false });
    };

    const list = matrices.data ?? [];
    const identities = (m: { persona_ids: number[]; include_anonymous: boolean }) => m.persona_ids.length + (m.include_anonymous ? 1 : 0);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Access matrix"
                subtitle={projectId && list.length
                    ? `${list.length} matri${list.length === 1 ? 'x' : 'ces'} in this project · declare who may reach what, then run it to catch broken access control and cross-tenant leaks.`
                    : 'Declare who may reach what — targets × identities, allow or deny — and run it to catch broken access control and cross-tenant leaks.'}
                actions={projectId ? (
                    <Button type="button" onClick={() => go({ matrix: 'new', tab: null, run: null, cell: null })}>
                        <Plus className="h-4 w-4" /> New matrix
                    </Button>
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to manage its access matrices.</EmptyState>
            ) : matrices.error ? (
                <EmptyState title="Couldn't load access matrices">{errorDetail(matrices.error)}</EmptyState>
            ) : matrices.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : (
                <SplitView
                    list={
                        <MatrixList matrices={list} selectedId={selectedId}
                            onSelect={(id) => go({ matrix: id, tab: null, run: null, cell: null })} />
                    }
                    detail={
                        selectedId === null ? (
                            <EmptyState title={list.length ? 'Choose a matrix' : 'No matrices yet'}>
                                {list.length ? 'Pick a matrix on the left to edit its grid or see its runs.' : 'Use “New matrix” to declare your first set of targets and identities.'}
                            </EmptyState>
                        ) : selectedId === 'new' ? (
                            <DetailCard label="New access matrix">
                                <div className="flex flex-col gap-1.5">
                                    <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">New matrix</h2>
                                    <span className="text-sm text-ink-soft">Rows are targets, columns are identities; each cell says whether that identity should get in.</span>
                                </div>
                                <MatrixEditor key={`new-${projectId}`} projectId={projectId} matrix={null}
                                    onSaved={(m) => go({ matrix: m.id })} onDeleted={() => go({ matrix: null })} />
                            </DetailCard>
                        ) : !matrix ? (
                            <EmptyState title="Matrix not found">This matrix is not in the selected project.</EmptyState>
                        ) : (
                            <DetailCard label={`Access matrix ${matrix.name}`}>
                                <div className="flex flex-col gap-1.5">
                                    <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{matrix.name}</h2>
                                    <span className="text-sm text-ink-soft">
                                        {matrix.rows.length} row{matrix.rows.length === 1 ? '' : 's'} · {identities(matrix)} identit{identities(matrix) === 1 ? 'y' : 'ies'}
                                        {matrix.description ? ` · ${matrix.description}` : ''}
                                    </span>
                                </div>
                                <div className="-mx-6 border-y border-[#F0EEE8] px-3 py-2 sm:-mx-7">
                                    <PanelTabs
                                        label="Matrix views"
                                        value={tab}
                                        onChange={(t) => go({ tab: t === 'edit' ? null : t })}
                                        tabs={[{ id: 'edit', label: 'Edit grid' }, { id: 'runs', label: 'Runs & results' }]}
                                    />
                                </div>
                                {tab === 'edit' ? (
                                    <MatrixEditor key={`${matrix.id}-${matrix.updated_at}`} projectId={projectId} matrix={matrix}
                                        onSaved={() => undefined} onDeleted={() => go({ matrix: null, tab: null, run: null, cell: null })} />
                                ) : (
                                    <RunsView matrix={matrix} runId={num(params.get('run'))} cellId={num(params.get('cell'))}
                                        onSelect={(run, cell) => go({ tab: 'runs', run, cell })} />
                                )}
                            </DetailCard>
                        )
                    }
                />
            )}
        </div>
    );
}
