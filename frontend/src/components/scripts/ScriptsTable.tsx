import { useState } from 'react';
import type { PageScript } from '@/api/estates';
import { shortHash } from '@/components/estate/helpers';
import ApproveForm from './ApproveForm';

interface Props {
    scripts: PageScript[];
    approvingId: number | null;
    onApprove: (s: PageScript, justification: string) => void;
    onRevoke: (s: PageScript) => void;
}

export default function ScriptsTable({ scripts, approvingId, onApprove, onRevoke }: Props) {
    const [openId, setOpenId] = useState<number | null>(null);
    if (scripts.length === 0) return <p className="text-sm text-slate-500">No scripts recorded yet — run a check.</p>;
    return (
        <table className="w-full text-sm">
            <caption className="sr-only">Scripts on this page, unapproved first</caption>
            <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <th scope="col" className="py-2">Script</th>
                    <th scope="col">SHA-384</th>
                    <th scope="col">SRI</th>
                    <th scope="col">Approval</th>
                </tr>
            </thead>
            <tbody>
                {scripts.map(s => {
                    const label = s.src || `inline script ${shortHash(s.sha384)}`;
                    return (
                        <tr key={s.id} className="border-t border-slate-100 align-top">
                            <td className="max-w-[420px] py-2">
                                <div className="break-all font-medium text-slate-800">{label}</div>
                                <div className="text-[11px] text-slate-500">
                                    {s.kind}{s.third_party ? ' · third party' : ' · first party'}
                                    {!s.present && ' · removed'} · first seen {s.first_seen_at.slice(0, 10)}
                                </div>
                                {s.authorized && s.justification && (
                                    <div className="mt-1 text-xs text-slate-600">Justification: {s.justification}</div>
                                )}
                                {openId === s.id && (
                                    <ApproveForm scriptLabel={label} pending={approvingId === s.id}
                                        onSubmit={(j) => { onApprove(s, j); setOpenId(null); }} onCancel={() => setOpenId(null)} />
                                )}
                            </td>
                            <td className="font-mono text-[11px] text-slate-600" title={s.sha384 ?? ''}>{shortHash(s.sha384)}</td>
                            <td className="text-xs">{s.integrity ? 'yes' : <span className="text-amber-700">none</span>}</td>
                            <td>
                                {s.authorized ? (
                                    <div className="space-y-1">
                                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-800">approved</span>
                                        <button type="button" className="block text-xs text-slate-500 hover:text-rose-700"
                                            onClick={() => onRevoke(s)}>Withdraw approval</button>
                                    </div>
                                ) : (
                                    <div className="space-y-1">
                                        <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold text-rose-800">unapproved</span>
                                        {s.present && openId !== s.id && (
                                            <button type="button" className="block text-xs font-medium text-indigo-700 hover:underline"
                                                onClick={() => setOpenId(s.id)}>Approve…</button>
                                        )}
                                    </div>
                                )}
                            </td>
                        </tr>
                    );
                })}
            </tbody>
        </table>
    );
}
