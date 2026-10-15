import type { EstateDomain } from '@/api/estates';
import Sparkline from './Sparkline';
import { certTone, daysLeft, scoreTone } from './helpers';

interface Props {
    domains: EstateDomain[];
    selectedId: number | null;
    onSelect: (id: number) => void;
    onRemove: (d: EstateDomain) => void;
    canRemove: boolean;
}

/** Domains worst-first (the API already sorts; never-scanned last). */
export default function DomainTable({ domains, selectedId, onSelect, onRemove, canRemove }: Props) {
    if (domains.length === 0) return <p className="text-sm text-slate-500">No domains yet — import some below.</p>;
    return (
        <table className="w-full text-sm">
            <caption className="sr-only">Estate domains, lowest score first</caption>
            <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <th scope="col" className="py-2">Domain</th>
                    <th scope="col">Score</th>
                    <th scope="col">Trend</th>
                    <th scope="col">Certificate</th>
                    <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {domains.map(d => {
                    const days = daysLeft(d.cert_expires_at);
                    return (
                        <tr key={d.id} className={`border-t border-slate-100 ${selectedId === d.id ? 'bg-indigo-50' : ''}`}>
                            <td className="py-2">
                                <button type="button" className="text-left font-medium text-indigo-700 hover:underline"
                                    onClick={() => onSelect(d.id)} aria-current={selectedId === d.id ? 'true' : undefined}>
                                    {d.hostname}
                                </button>
                                <span className="ml-2 text-[11px] text-slate-400">
                                    {d.source === 'ct' ? 'from CT logs' : 'added'}{d.is_apex ? ' · zone apex' : ''}
                                    {d.allowlisted === false ? ' · not allowlisted (skipped)' : ''}
                                </span>
                            </td>
                            <td><span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${scoreTone(d.score)}`}>
                                {d.score ?? '—'}</span></td>
                            <td><Sparkline values={d.trend} label={`${d.hostname} score trend`} /></td>
                            <td className={`text-xs ${certTone(days)}`}>{days === null ? '—' : `${days} day(s)`}</td>
                            <td className="text-right">
                                {canRemove && (
                                    <button type="button" className="text-xs text-slate-500 hover:text-rose-700"
                                        onClick={() => onRemove(d)}>Remove {d.hostname}</button>
                                )}
                            </td>
                        </tr>
                    );
                })}
            </tbody>
        </table>
    );
}
