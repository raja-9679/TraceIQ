import type { EstateDomain } from '@/api/estates';
import Sparkline from './Sparkline';
import { Pill } from '@/components/redesign/primitives';
import { certTone, daysLeft, scorePill } from './helpers';

interface Props {
    domains: EstateDomain[];
    selectedId: number | null;
    onSelect: (id: number) => void;
    onRemove: (d: EstateDomain) => void;
    canRemove: boolean;
}

/** Domains worst-first (the API already sorts; never-scanned last). */
export default function DomainTable({ domains, selectedId, onSelect, onRemove, canRemove }: Props) {
    if (domains.length === 0) return <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No domains yet — import some below.</p>;
    return (
        <div className="overflow-x-auto rounded-2xl border border-border">
        <table className="w-full text-sm text-ink">
            <caption className="sr-only">Estate domains, lowest score first</caption>
            <thead>
                <tr className="bg-background text-left text-[11px] uppercase tracking-[0.04em] text-ink-soft">
                    <th scope="col" className="px-3 py-2">Domain</th>
                    <th scope="col" className="px-2">Score</th>
                    <th scope="col" className="px-2">Trend</th>
                    <th scope="col" className="px-2">Certificate</th>
                    <th scope="col" className="px-3"><span className="sr-only">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {domains.map(d => {
                    const days = daysLeft(d.cert_expires_at);
                    return (
                        <tr key={d.id} className={`border-t border-[#F0EEE8] ${selectedId === d.id ? 'bg-accent shadow-[inset_3px_0_0_#1C1B22]' : ''}`}>
                            <td className="px-3 py-2.5">
                                <button type="button" className="text-left font-semibold text-ink underline-offset-2 hover:underline"
                                    onClick={() => onSelect(d.id)} aria-current={selectedId === d.id ? 'true' : undefined}>
                                    {d.hostname}
                                </button>
                                <span className="block text-[11px] text-ink-soft">
                                    {d.source === 'ct' ? 'from CT logs' : 'added'}{d.is_apex ? ' · zone apex' : ''}
                                    {d.allowlisted === false ? ' · not allowlisted (skipped)' : ''}
                                </span>
                            </td>
                            <td className="px-2"><Pill tone={scorePill(d.score)} className="tabular-nums">
                                {d.score ?? '—'}</Pill></td>
                            <td className="px-2"><Sparkline values={d.trend} label={`${d.hostname} score trend`} /></td>
                            <td className={`px-2 text-xs ${certTone(days)}`}>{days === null ? '—' : `${days} day(s)`}</td>
                            <td className="px-3 text-right">
                                {canRemove && (
                                    <button type="button" className="whitespace-nowrap text-xs font-medium text-ink-soft underline-offset-2 hover:text-ink hover:underline"
                                        onClick={() => onRemove(d)}>Remove {d.hostname}</button>
                                )}
                            </td>
                        </tr>
                    );
                })}
            </tbody>
        </table>
        </div>
    );
}
