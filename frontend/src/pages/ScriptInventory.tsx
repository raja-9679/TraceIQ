import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { pagesApi } from '@/api/estates';
import { getProjects } from '@/lib/api';
import { errDetail } from '@/lib/errors';
import { statusTone, useActiveProject } from '@/components/estate/helpers';
import PageForm from '@/components/scripts/PageForm';
import PageView from '@/components/scripts/PageView';

const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function ScriptInventoryPage() {
    const [projectId, selectProject] = useActiveProject();
    const [params, setParams] = useSearchParams();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const pages = useQuery({
        queryKey: ['monitored-pages', projectId], queryFn: () => pagesApi.list(projectId!), enabled: !!projectId,
    });
    const sel = params.get('page');
    const view = sel === 'new' ? 'new' : num(sel);
    const page = typeof view === 'number' ? pages.data?.find(p => p.id === view) ?? null : null;
    const go = (v: string | number | null) => setParams(v === null ? new URLSearchParams() : new URLSearchParams({ page: String(v) }));

    return (
        <div className="max-w-[1400px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60 flex flex-col sm:flex-row sm:items-end gap-4">
                <div>
                    <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Script Inventory</h1>
                    <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                        Every script on your payment pages, each approved with a justification, and an alert when one
                        appears or changes (PCI DSS 4.0 6.4.3 and 11.6.1). Optional defacement checks on the same load.
                    </p>
                </div>
                <div className="sm:ml-auto shrink-0">
                    <label htmlFor="si-project" className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</label>
                    <select id="si-project" className="h-10 w-[240px] rounded-xl border border-slate-200 bg-white px-3 text-sm"
                        value={projectId ?? ''} onChange={(e) => { selectProject(Number(e.target.value)); go(null); }}>
                        <option value="" disabled>Select a project</option>
                        {(projects.data || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                </div>
            </div>
            {!projectId ? (
                <p className="p-10 text-center bg-white border border-slate-200 rounded-2xl text-slate-500 text-sm">
                    Select a project above to manage its monitored pages.
                </p>
            ) : pages.error ? (
                <p role="alert" className="text-sm text-rose-700">{errDetail(pages.error)}</p>
            ) : (
                <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
                    <nav aria-label="Monitored pages" className="space-y-2">
                        <button type="button" onClick={() => go('new')}
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50">
                            Monitor a page
                        </button>
                        <ul className="space-y-1.5">
                            {(pages.data ?? []).map(p => (
                                <li key={p.id}>
                                    <button type="button" onClick={() => go(p.id)} aria-current={view === p.id ? 'true' : undefined}
                                        className={`w-full rounded-lg border px-3 py-2 text-left ${view === p.id ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                                        <div className="flex items-center gap-2">
                                            <span className="truncate text-sm font-semibold text-slate-800">{p.name}</span>
                                            <span className={`ml-auto rounded px-1.5 text-[11px] ${statusTone(p.last_status)}`}>{p.last_status ?? 'new'}</span>
                                        </div>
                                        <div className="text-[11px] text-slate-500">
                                            {p.script_count ?? 0} script(s){p.unapproved_count ? ` · ${p.unapproved_count} unapproved` : ''}
                                        </div>
                                    </button>
                                </li>
                            ))}
                        </ul>
                        {pages.data?.length === 0 && <p className="text-xs text-slate-500">No monitored pages yet.</p>}
                    </nav>
                    <div className="min-w-0">
                        {view === null ? (
                            <p className="text-sm text-slate-500">Choose a page, or start monitoring one.</p>
                        ) : view === 'new' ? (
                            <PageForm projectId={projectId} page={null} onSaved={(p) => go(p.id)} />
                        ) : !page ? (
                            <p className="text-sm text-slate-500">{pages.isLoading ? 'Loading…' : 'Page not found in this project.'}</p>
                        ) : (
                            <PageView key={page.id} projectId={projectId} page={page} onDeleted={() => go(null)} />
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
