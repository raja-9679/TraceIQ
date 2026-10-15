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
    if (q.isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
    if (q.error) return <p role="alert" className="text-sm text-rose-700">{errDetail(q.error)}</p>;
    const rows = q.data ?? [];
    if (rows.length === 0) return <p className="text-sm text-slate-500">No certificates observed yet — scan an estate first.</p>;
    return (
        <table className="w-full text-sm">
            <caption className="sr-only">Certificate expiry calendar, soonest first</caption>
            <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <th scope="col" className="py-2">Expires</th>
                    <th scope="col">Days left</th>
                    <th scope="col">Host</th>
                    <th scope="col">Estate</th>
                    <th scope="col">Project</th>
                </tr>
            </thead>
            <tbody>
                {rows.map(r => (
                    <tr key={r.domain_id} className="border-t border-slate-100">
                        <td className="py-1.5">{r.cert_expires_at.slice(0, 10)}</td>
                        <td className={certTone(r.days_left)}>{r.days_left}</td>
                        <td className="font-medium text-slate-800">{r.hostname}</td>
                        <td>{r.estate_name}</td>
                        <td className="text-slate-500">{r.project_name}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
