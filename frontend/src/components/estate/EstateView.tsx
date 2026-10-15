import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { estatesApi, type EstateDomain } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';
import DomainDrilldown from './DomainDrilldown';
import DomainImport from './DomainImport';
import DomainTable from './DomainTable';
import EstateForm from './EstateForm';
import { statusTone } from './helpers';

interface Props {
    projectId: number;
    estateId: number;
    domainId: number | null;
    onSelectDomain: (id: number | null) => void;
    onDeleted: () => void;
}

export default function EstateView({ projectId, estateId, domainId, onSelectDomain, onDeleted }: Props) {
    const qc = useQueryClient();
    const [editing, setEditing] = useState(false);
    const estate = useQuery({
        queryKey: ['estate', estateId], queryFn: () => estatesApi.get(estateId),
        // Poll while a scan is in flight so scores appear without a reload.
        refetchInterval: (q) => (['pending', 'running'].includes(q.state.data?.latest_scan?.status ?? '') ? 4000 : false),
    });
    const scans = useQuery({ queryKey: ['estate-scans', estateId], queryFn: () => estatesApi.scans(estateId) });
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['estate', estateId] });
        qc.invalidateQueries({ queryKey: ['estate-scans', estateId] });
        qc.invalidateQueries({ queryKey: ['estates', projectId] });
    };
    const scan = useMutation({ mutationFn: () => estatesApi.scan(estateId), onSuccess: refresh });
    const remove = useMutation({ mutationFn: () => estatesApi.remove(estateId), onSuccess: () => { refresh(); onDeleted(); } });
    const removeDomain = useMutation({
        mutationFn: (d: EstateDomain) => estatesApi.removeDomain(estateId, d.id),
        onSuccess: () => { onSelectDomain(null); refresh(); },
    });

    if (estate.isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
    if (estate.error) return <p role="alert" className="text-sm text-rose-700">{errDetail(estate.error)}</p>;
    const e = estate.data!;
    const latest = e.latest_scan;
    const busy = latest && ['pending', 'running'].includes(latest.status);
    const mutationError = scan.error || remove.error || removeDomain.error;

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-xl font-bold text-slate-900">{e.name}</h2>
                {latest && <span className={`rounded px-2 py-0.5 text-xs ${statusTone(latest.status === 'completed' ? 'ok' : latest.status)}`}>
                    last scan {latest.status}{latest.finished_at ? ` · ${latest.finished_at.slice(0, 16).replace('T', ' ')}` : ''}</span>}
                <div className="ml-auto flex gap-2">
                    <Button type="button" onClick={() => scan.mutate()} disabled={!!busy || scan.isPending}>
                        {busy ? 'Scanning…' : 'Scan now'}</Button>
                    <Button type="button" variant="outline" onClick={() => setEditing(v => !v)}>
                        {editing ? 'Close settings' : 'Settings'}</Button>
                    <Button type="button" variant="outline" onClick={() => {
                        if (window.confirm(`Delete estate "${e.name}" and its scan history?`)) remove.mutate();
                    }}>Delete estate</Button>
                </div>
            </div>
            <p className="text-xs text-slate-500">
                Passive checks only: DNS records, certificate-transparency discovery, and ordinary HTTPS visits
                (certificate, HSTS, redirect, takeover markers). No port scans or attack traffic.
            </p>
            {mutationError && <p role="alert" className="text-sm text-rose-700">{errDetail(mutationError)}</p>}
            {editing && <EstateForm projectId={projectId} estate={e} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} />}
            {latest?.summary?.skipped_not_allowlisted?.length ? (
                <p className="text-xs text-amber-700">Skipped (no longer allowlisted): {latest.summary.skipped_not_allowlisted.join(', ')}</p>
            ) : null}
            <div className="grid gap-5 xl:grid-cols-[1fr_1fr]">
                <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
                    <DomainTable domains={e.domains ?? []} selectedId={domainId} onSelect={onSelectDomain}
                        onRemove={(d) => removeDomain.mutate(d)} canRemove />
                    <DomainImport estateId={estateId} />
                </div>
                <div>
                    {domainId ? <DomainDrilldown domainId={domainId} />
                        : <p className="text-sm text-slate-500">Choose a domain to see every check and how its score is made.</p>}
                </div>
            </div>
            <section aria-label="Scan history" className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="mb-2 text-sm font-semibold text-slate-800">Scan history</h3>
                <ul className="space-y-1 text-xs text-slate-600">
                    {(scans.data ?? []).map(s => (
                        <li key={s.id}>
                            #{s.id} · {s.created_at.slice(0, 16).replace('T', ' ')} · {s.trigger} · {s.status}
                            {s.summary?.average_score != null && ` · average ${s.summary.average_score}`}
                            {s.summary?.domains_scanned != null && ` · ${s.summary.domains_scanned} domain(s)`}
                            {s.error && <span className="text-rose-700"> · {s.error}</span>}
                        </li>
                    ))}
                    {scans.data?.length === 0 && <li>No scans yet.</li>}
                </ul>
            </section>
        </div>
    );
}
