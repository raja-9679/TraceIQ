import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { pagesApi, type MonitoredPage, type PageScript } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';
import { statusPill } from '@/components/estate/helpers';
import { DetailCard, PanelTabs, Pill } from '@/components/redesign/primitives';
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
        <DetailCard label={`Monitored page ${page.name}`}>
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex min-w-0 flex-col gap-1.5">
                    <div className="flex flex-wrap items-center gap-3">
                        <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{page.name}</h2>
                        <Pill tone={statusPill(page.last_status)}>{page.last_status ?? 'not checked'}</Pill>
                    </div>
                    <p className="break-all text-sm text-ink-soft">
                        <span className="font-mono">{page.url}</span> · last checked {page.last_checked_at ? page.last_checked_at.slice(0, 16).replace('T', ' ') : 'never'}
                        {page.last_similarity != null && ` · content similarity ${Math.round(page.last_similarity * 100)}%`}
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
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
            {page.last_error && <p role="alert" className="text-sm text-pastel-ink-peach">{page.last_error}</p>}
            {err && <p role="alert" className="text-sm text-pastel-ink-peach">{errDetail(err)}</p>}

            <div className="-mx-6 border-y border-[#F0EEE8] px-3 py-2 sm:-mx-7">
                <PanelTabs
                    label="Page views"
                    value={tab}
                    onChange={setTab}
                    tabs={[
                        { id: 'scripts', label: 'Scripts', count: page.script_count ?? undefined },
                        { id: 'changes', label: 'Change history' },
                        { id: 'settings', label: 'Settings & defacement' },
                    ]}
                />
            </div>
            {tab === 'scripts' && (
                <div className="flex flex-col gap-3">
                    <label className="flex items-center gap-2 text-sm text-ink">
                        <input type="checkbox" checked={showRemoved} onChange={e => setShowRemoved(e.target.checked)} />
                        Include removed scripts
                    </label>
                    {scripts.error ? <p role="alert" className="text-sm text-pastel-ink-peach">{errDetail(scripts.error)}</p>
                        : <ScriptsTable scripts={scripts.data ?? []} approvingId={approve.isPending ? approve.variables?.s.id ?? null : null}
                            onApprove={(s, j) => approve.mutate({ s, j })} onRevoke={(s) => revoke.mutate(s)} />}
                    {Object.keys(page.headers_baseline).length > 0 && (
                        <details className="rounded-2xl bg-background p-4 text-xs text-ink">
                            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Security headers last seen</summary>
                            <dl className="mt-2 space-y-0.5">
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
        </DetailCard>
    );
}
