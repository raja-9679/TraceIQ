import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { estatesApi } from '@/api/estates';
import { getProjects } from '@/lib/api';
import { errDetail } from '@/lib/errors';
import CertCalendar from '@/components/estate/CertCalendar';
import EstateForm from '@/components/estate/EstateForm';
import EstateView from '@/components/estate/EstateView';
import { scoreTone, useActiveProject } from '@/components/estate/helpers';

// Thin shell: project picker, estate list, and (new | estate | certificates).
// Selection lives in the URL (?estate=&domain=) so a domain can be deep-linked.

const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function EstatePage() {
    const [projectId, selectProject] = useActiveProject();
    const [params, setParams] = useSearchParams();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const estates = useQuery({
        queryKey: ['estates', projectId], queryFn: () => estatesApi.list(projectId!), enabled: !!projectId,
    });
    const project = projects.data?.find(p => p.id === projectId);
    const sel = params.get('estate');
    const view = sel === 'new' ? 'new' : sel === 'certs' ? 'certs' : num(sel);

    const go = (next: Record<string, string | number | null>) => {
        const p = new URLSearchParams(params);
        for (const [k, v] of Object.entries(next)) {
            if (v === null) p.delete(k); else p.set(k, String(v));
        }
        setParams(p);
    };

    return (
        <div className="max-w-[1400px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60 flex flex-col sm:flex-row sm:items-end gap-4">
                <div>
                    <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Estate</h1>
                    <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                        Every domain you own, one score each: email authentication, certificates, HSTS,
                        dangling DNS — checked passively, worst first.
                    </p>
                </div>
                <div className="sm:ml-auto shrink-0">
                    <label htmlFor="estate-project" className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</label>
                    <select id="estate-project" className="h-10 w-[240px] rounded-xl border border-slate-200 bg-white px-3 text-sm"
                        value={projectId ?? ''} onChange={(e) => { selectProject(Number(e.target.value)); setParams(new URLSearchParams()); }}>
                        <option value="" disabled>Select a project</option>
                        {(projects.data || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                </div>
            </div>

            {!projectId ? (
                <p className="p-10 text-center bg-white border border-slate-200 rounded-2xl text-slate-500 text-sm">
                    Select a project above to manage its estates.
                </p>
            ) : estates.error ? (
                <p role="alert" className="text-sm text-rose-700">{errDetail(estates.error)}</p>
            ) : (
                <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
                    <nav aria-label="Estates" className="space-y-2">
                        <button type="button" onClick={() => go({ estate: 'new', domain: null })}
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50">
                            New estate
                        </button>
                        <button type="button" onClick={() => go({ estate: 'certs', domain: null })}
                            aria-current={view === 'certs' ? 'true' : undefined}
                            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50">
                            Certificate calendar
                        </button>
                        <ul className="space-y-1.5">
                            {(estates.data ?? []).map(e => (
                                <li key={e.id}>
                                    <button type="button" onClick={() => go({ estate: e.id, domain: null })}
                                        aria-current={view === e.id ? 'true' : undefined}
                                        className={`w-full rounded-lg border px-3 py-2 text-left ${view === e.id ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                                        <div className="flex items-center gap-2">
                                            <span className="truncate text-sm font-semibold text-slate-800">{e.name}</span>
                                            <span className={`ml-auto rounded-full px-1.5 text-[11px] font-semibold ${scoreTone(e.worst_score)}`}
                                                title="Worst domain score">{e.worst_score ?? '—'}</span>
                                        </div>
                                        <div className="text-[11px] text-slate-500">
                                            {e.domain_count ?? 0} domain(s){e.average_score != null ? ` · average ${e.average_score}` : ''}
                                        </div>
                                    </button>
                                </li>
                            ))}
                        </ul>
                        {estates.data?.length === 0 && <p className="text-xs text-slate-500">No estates in this project yet.</p>}
                    </nav>
                    <div className="min-w-0">
                        {view === null ? (
                            <p className="text-sm text-slate-500">Choose an estate, or create a new one.</p>
                        ) : view === 'new' ? (
                            <EstateForm projectId={projectId} estate={null} onSaved={(e) => go({ estate: e.id })} />
                        ) : view === 'certs' ? (
                            project ? <CertCalendar workspaceId={project.workspace_id} /> : null
                        ) : (
                            <EstateView key={view} projectId={projectId} estateId={view} domainId={num(params.get('domain'))}
                                onSelectDomain={(id) => go({ domain: id })} onDeleted={() => go({ estate: null, domain: null })} />
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
