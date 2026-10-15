import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { pagesApi, type MonitoredPage, type PageScript } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';
import { statusTone } from '@/components/estate/helpers';
import ChangeHistory from './ChangeHistory';
import PageForm from './PageForm';
import ScriptsTable from './ScriptsTable';

type Tab = 'scripts' | 'changes' | 'settings';

interface Props {
    projectId: number;
    page: MonitoredPage;
    onDeleted: () => void;
}

export default function PageView({ projectId, page, onDeleted }: Props) {
    const qc = useQueryClient();
    const [tab, setTab] = useState<Tab>('scripts');
    const [showRemoved, setShowRemoved] = useState(false);
    const scripts = useQuery({
        queryKey: ['page-scripts', page.id, showRemoved], queryFn: () => pagesApi.scripts(page.id, showRemoved),
    });
    const changes = useQuery({ queryKey: ['page-changes', page.id], queryFn: () => pagesApi.changes(page.id) });
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['monitored-pages', projectId] });
        qc.invalidateQueries({ queryKey: ['page-scripts', page.id] });
        qc.invalidateQueries({ queryKey: ['page-changes', page.id] });
    };
    const check = useMutation({ mutationFn: () => pagesApi.check(page.id), onSuccess: refresh });
    const reset = useMutation({ mutationFn: () => pagesApi.resetBaseline(page.id), onSuccess: refresh });
    const remove = useMutation({ mutationFn: () => pagesApi.remove(page.id), onSuccess: () => { refresh(); onDeleted(); } });
    const approve = useMutation({
        mutationFn: ({ s, j }: { s: PageScript; j: string }) => pagesApi.approve(page.id, s.id, j), onSuccess: refresh,
    });
    const revoke = useMutation({ mutationFn: (s: PageScript) => pagesApi.revoke(page.id, s.id), onSuccess: refresh });
    const err = check.error || reset.error || remove.error || approve.error || revoke.error;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-xl font-bold text-slate-900">{page.name}</h2>
                <span className={`rounded px-2 py-0.5 text-xs font-semibold ${statusTone(page.last_status)}`}>
                    {page.last_status ?? 'not checked'}</span>
                <div className="ml-auto flex flex-wrap gap-2">
                    <Button type="button" onClick={() => check.mutate()} disabled={check.isPending}>
                        {check.isSuccess ? 'Check queued' : 'Check now'}</Button>
                    {page.track_content && (
                        <Button type="button" variant="outline" onClick={() => reset.mutate()} disabled={reset.isPending}>
                            Accept current content as baseline</Button>
                    )}
                    <Button type="button" variant="outline" onClick={() => {
                        if (window.confirm(`Stop monitoring "${page.name}" and delete its inventory and history?`)) remove.mutate();
                    }}>Delete page</Button>
                </div>
            </div>
            <p className="break-all text-xs text-slate-500">
                {page.url} · last checked {page.last_checked_at ? page.last_checked_at.slice(0, 16).replace('T', ' ') : 'never'}
                {page.last_similarity != null && ` · content similarity ${Math.round(page.last_similarity * 100)}%`}
            </p>
            {page.last_error && <p role="alert" className="text-sm text-rose-700">{page.last_error}</p>}
            {err && <p role="alert" className="text-sm text-rose-700">{errDetail(err)}</p>}

            <div role="tablist" aria-label="Page views" className="flex gap-2">
                {(['scripts', 'changes', 'settings'] as const).map(t => (
                    <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                        className={`rounded-lg px-3 py-1.5 text-sm font-medium ${tab === t ? 'bg-slate-900 text-white' : 'bg-white border border-slate-200 text-slate-600'}`}>
                        {t === 'scripts' ? 'Scripts' : t === 'changes' ? 'Change history' : 'Settings & defacement'}
                    </button>
                ))}
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                {tab === 'scripts' && (
                    <div className="space-y-3">
                        <label className="flex items-center gap-2 text-xs text-slate-600">
                            <input type="checkbox" checked={showRemoved} onChange={e => setShowRemoved(e.target.checked)} />
                            Include removed scripts
                        </label>
                        {scripts.error ? <p role="alert" className="text-sm text-rose-700">{errDetail(scripts.error)}</p>
                            : <ScriptsTable scripts={scripts.data ?? []} approvingId={approve.isPending ? approve.variables?.s.id ?? null : null}
                                onApprove={(s, j) => approve.mutate({ s, j })} onRevoke={(s) => revoke.mutate(s)} />}
                        {Object.keys(page.headers_baseline).length > 0 && (
                            <details className="text-xs text-slate-600">
                                <summary className="cursor-pointer font-medium">Security headers last seen</summary>
                                <dl className="mt-1 space-y-0.5">
                                    {Object.entries(page.headers_baseline).map(([k, v]) => (
                                        <div key={k}><dt className="inline font-mono">{k}:</dt> <dd className="inline break-all">{v}</dd></div>
                                    ))}
                                </dl>
                            </details>
                        )}
                    </div>
                )}
                {tab === 'changes' && <ChangeHistory changes={changes.data ?? []} />}
                {tab === 'settings' && <PageForm key={page.id} projectId={projectId} page={page} onSaved={() => setTab('scripts')} />}
            </div>
        </div>
    );
}
