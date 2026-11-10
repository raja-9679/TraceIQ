import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { estatesApi, type EstateDomain } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';
import {
    DetailCard, EmptyState, Eyebrow, ListCard, Panel, Pill,
} from '@/components/redesign/primitives';
import DomainDrilldown from './DomainDrilldown';
import DomainImport from './DomainImport';
import DomainTable from './DomainTable';
import EstateForm from './EstateForm';
import { statusPill } from './helpers';

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

    if (estate.isLoading) return <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />;
    if (estate.error) return <EmptyState title="Couldn't load this estate">{errDetail(estate.error)}</EmptyState>;
    const e = estate.data!;
    const latest = e.latest_scan;
    const busy = latest && ['pending', 'running'].includes(latest.status);
    const mutationError = scan.error || remove.error || removeDomain.error;
    const scanList = scans.data ?? [];

    return (
        <div className="flex flex-col gap-5">
            <DetailCard label={`Estate ${e.name}`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex min-w-0 flex-col gap-1.5">
                        <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{e.name}</h2>
                        {latest && (
                            <Pill tone={statusPill(latest.status === 'completed' ? 'ok' : latest.status)} className="self-start">
                                last scan {latest.status}{latest.finished_at ? ` · ${latest.finished_at.slice(0, 16).replace('T', ' ')}` : ''}
                            </Pill>
                        )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Button type="button" onClick={() => scan.mutate()} disabled={!!busy || scan.isPending}>
                            {busy ? 'Scanning…' : 'Scan now'}</Button>
                        <Button type="button" variant="outline" onClick={() => setEditing(v => !v)} aria-expanded={editing}>
                            {editing ? 'Close settings' : 'Settings'}</Button>
                        <Button type="button" variant="outline" onClick={() => {
                            if (window.confirm(`Delete estate "${e.name}" and its scan history?`)) remove.mutate();
                        }}>Delete estate</Button>
                    </div>
                </div>
                <Panel title="Passive checks only">
                    DNS records, certificate-transparency discovery, and ordinary HTTPS visits
                    (certificate, HSTS, redirect, takeover markers). No port scans or attack traffic.
                </Panel>
                {mutationError && <p role="alert" className="text-sm text-pastel-ink-peach">{errDetail(mutationError)}</p>}
                {editing && (
                    <div className="rounded-2xl border border-border p-4">
                        <EstateForm projectId={projectId} estate={e} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} />
                    </div>
                )}
                {latest?.summary?.skipped_not_allowlisted?.length ? (
                    <p className="text-xs text-pastel-ink-butter">Skipped (no longer allowlisted): {latest.summary.skipped_not_allowlisted.join(', ')}</p>
                ) : null}
                <div className="flex flex-col gap-2">
                    <Eyebrow right={`${(e.domains ?? []).length} domain(s)`}>Domains, lowest score first</Eyebrow>
                    <DomainTable domains={e.domains ?? []} selectedId={domainId} onSelect={onSelectDomain}
                        onRemove={(d) => { if (window.confirm(`Remove ${d.hostname} from this estate? Its past scan results are deleted too.`)) removeDomain.mutate(d); }} canRemove />
                </div>
                <div className="border-t border-[#F0EEE8] pt-5">
                    <DomainImport estateId={estateId} />
                </div>
            </DetailCard>

            {domainId ? <DomainDrilldown domainId={domainId} />
                : <EmptyState title="Choose a domain">Pick a domain above to see every check and how its score is made.</EmptyState>}

            <ListCard title="Scan history" count={scanList.length}>
                {scanList.map(s => (
                    <div key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-xs text-ink-soft">
                        <Pill tone={statusPill(s.status === 'completed' ? 'ok' : s.status)}>{s.status}</Pill>
                        <span className="font-semibold text-ink">#{s.id}</span>
                        <span>{s.created_at.slice(0, 16).replace('T', ' ')} · {s.trigger}
                            {s.summary?.average_score != null && ` · average ${s.summary.average_score}`}
                            {s.summary?.domains_scanned != null && ` · ${s.summary.domains_scanned} domain(s)`}</span>
                        {s.error && <span className="text-pastel-ink-peach">{s.error}</span>}
                    </div>
                ))}
                {scans.data?.length === 0 && <p className="px-4 py-4 text-sm text-ink-soft">No scans yet.</p>}
            </ListCard>
        </div>
    );
}
