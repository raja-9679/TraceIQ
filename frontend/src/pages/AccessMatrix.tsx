import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { accessMatrixApi } from '@/api/accessMatrix';
import { getProjects } from '@/lib/api';
import MatrixEditor from '@/components/access-matrix/MatrixEditor';
import MatrixList from '@/components/access-matrix/MatrixList';
import RunsView from '@/components/access-matrix/RunsView';
import { errorDetail } from '@/components/access-matrix/grid';

// Thin shell: project picker + list + (editor | runs). Selection lives in the
// URL (?matrix=&tab=&run=&cell=) so a finding or a teammate can deep-link to
// one cell's evidence.

function useActiveProject(): [number | null, (id: number) => void] {
    const read = () => { const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null; };
    const [projectId, setProjectId] = useState<number | null>(read);
    useEffect(() => {
        const h = () => setProjectId(read());
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const select = (id: number) => {
        setProjectId(id);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };
    return [projectId, select];
}

const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function AccessMatrixPage() {
    const [projectId, selectProject] = useActiveProject();
    const [params, setParams] = useSearchParams();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const matrices = useQuery({
        queryKey: ['access-matrices', projectId],
        queryFn: () => accessMatrixApi.list(projectId!),
        enabled: !!projectId,
    });

    const sel = params.get('matrix');
    const selectedId: number | 'new' | null = sel === 'new' ? 'new' : num(sel);
    const tab = params.get('tab') === 'runs' ? 'runs' : 'edit';
    const matrix = typeof selectedId === 'number' ? matrices.data?.find(m => m.id === selectedId) ?? null : null;

    const go = (next: Record<string, string | number | null>) => {
        const p = new URLSearchParams(params);
        for (const [k, v] of Object.entries(next)) {
            if (v === null) p.delete(k); else p.set(k, String(v));
        }
        setParams(p, { replace: false });
    };

    return (
        <div className="max-w-[1400px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60 flex flex-col sm:flex-row sm:items-end gap-4">
                <div>
                    <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Access Matrix</h1>
                    <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                        Declare who may reach what — targets × identities, allow or deny — and run it to catch
                        broken access control and cross-tenant leaks.
                    </p>
                </div>
                <div className="sm:ml-auto shrink-0">
                    <label htmlFor="am-project" className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</label>
                    <select id="am-project" className="h-10 w-[240px] rounded-xl border border-slate-200 bg-white px-3 text-sm"
                        value={projectId ?? ''} onChange={(e) => { selectProject(Number(e.target.value)); setParams(new URLSearchParams()); }}>
                        <option value="" disabled>Select a project</option>
                        {(projects.data || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                </div>
            </div>

            {!projectId ? (
                <p className="p-10 text-center bg-white border border-slate-200 rounded-2xl text-slate-500 text-sm">
                    Select a project above to manage its access matrices.
                </p>
            ) : matrices.error ? (
                <p role="alert" className="text-sm text-rose-700">{errorDetail(matrices.error)}</p>
            ) : (
                <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
                    <MatrixList matrices={matrices.data ?? []} selectedId={selectedId}
                        onSelect={(id) => go({ matrix: id, tab: null, run: null, cell: null })} />
                    <div className="min-w-0">
                        {selectedId === null ? (
                            <p className="text-sm text-slate-500">Choose a matrix, or create a new one.</p>
                        ) : selectedId === 'new' ? (
                            <MatrixEditor key={`new-${projectId}`} projectId={projectId} matrix={null}
                                onSaved={(m) => go({ matrix: m.id })} onDeleted={() => go({ matrix: null })} />
                        ) : !matrix ? (
                            <p className="text-sm text-slate-500">{matrices.isLoading ? 'Loading…' : 'Matrix not found in this project.'}</p>
                        ) : (
                            <>
                                <div role="tablist" aria-label="Matrix views" className="mb-4 flex gap-2">
                                    {(['edit', 'runs'] as const).map(t => (
                                        <button key={t} type="button" role="tab" aria-selected={tab === t}
                                            onClick={() => go({ tab: t === 'edit' ? null : t })}
                                            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${tab === t ? 'bg-slate-900 text-white' : 'bg-white border border-slate-200 text-slate-600'}`}>
                                            {t === 'edit' ? 'Edit grid' : 'Runs & results'}
                                        </button>
                                    ))}
                                </div>
                                {tab === 'edit' ? (
                                    <MatrixEditor key={`${matrix.id}-${matrix.updated_at}`} projectId={projectId} matrix={matrix}
                                        onSaved={() => undefined} onDeleted={() => go({ matrix: null, tab: null, run: null, cell: null })} />
                                ) : (
                                    <RunsView matrix={matrix} runId={num(params.get('run'))} cellId={num(params.get('cell'))}
                                        onSelect={(run, cell) => go({ tab: 'runs', run, cell })} />
                                )}
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
