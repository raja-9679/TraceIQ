import { useQuery } from '@tanstack/react-query';
import { estatesApi } from '@/api/estates';
import { errDetail } from '@/lib/errors';
import { certTone } from './helpers';

interface Props {
    workspaceId: number;
}

/** Certificate expiries across every estate in the workspace the viewer can read. */
export default function CertCalendar({ workspaceId }: Props) {
    const q = useQuery({ queryKey: ['estate-certs', workspaceId], queryFn: () => estatesApi.certificates(workspaceId) });
    if (q.isLoading) return <p className="text-sm text-ink-soft">Loading…</p>;
    if (q.error) return <p role="alert" className="text-sm text-pastel-ink-peach">{errDetail(q.error)}</p>;
    const rows = q.data ?? [];
    if (rows.length === 0) return <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No certificates observed yet — scan an estate first.</p>;
    return (
        <div className="overflow-x-auto rounded-2xl border border-border">
        <table className="w-full text-sm text-ink">
            <caption className="sr-only">Certificate expiry calendar, soonest first</caption>
            <thead>
                <tr className="bg-background text-left text-[11px] uppercase tracking-[0.04em] text-ink-soft">
                    <th scope="col" className="px-3 py-2">Expires</th>
                    <th scope="col" className="px-3">Days left</th>
                    <th scope="col" className="px-3">Host</th>
                    <th scope="col" className="px-3">Estate</th>
                    <th scope="col" className="px-3">Project</th>
                </tr>
            </thead>
            <tbody>
                {rows.map(r => (
                    <tr key={r.domain_id} className="border-t border-[#F0EEE8]">
                        <td className="px-3 py-2.5 font-mono text-xs">{r.cert_expires_at.slice(0, 10)}</td>
                        <td className={`px-3 tabular-nums ${certTone(r.days_left)}`}>{r.days_left}</td>
                        <td className="px-3 font-semibold text-ink">{r.hostname}</td>
                        <td className="px-3">{r.estate_name}</td>
                        <td className="px-3 text-ink-soft">{r.project_name}</td>
                    </tr>
                ))}
            </tbody>
        </table>
        </div>
    );
}
