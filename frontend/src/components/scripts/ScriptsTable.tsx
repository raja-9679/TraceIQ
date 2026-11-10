import { useState } from 'react';
import type { PageScript } from '@/api/estates';
import { shortHash } from '@/components/estate/helpers';
import { Pill } from '@/components/redesign/primitives';
import ApproveForm from './ApproveForm';

interface Props {
    scripts: PageScript[];
    approvingId: number | null;
    onApprove: (s: PageScript, justification: string) => void;
    onRevoke: (s: PageScript) => void;
}

export default function ScriptsTable({ scripts, approvingId, onApprove, onRevoke }: Props) {
    const [openId, setOpenId] = useState<number | null>(null);
    if (scripts.length === 0) return <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No scripts recorded yet — run a check.</p>;
    return (
        <div className="overflow-x-auto rounded-2xl border border-border">
        <table className="w-full text-sm text-ink">
            <caption className="sr-only">Scripts on this page, unapproved first</caption>
            <thead>
                <tr className="bg-background text-left text-[11px] uppercase tracking-[0.04em] text-ink-soft">
                    <th scope="col" className="px-3 py-2">Script</th>
                    <th scope="col" className="px-2">SHA-384</th>
                    <th scope="col" className="px-2">SRI</th>
                    <th scope="col" className="px-3">Approval</th>
                </tr>
            </thead>
            <tbody>
                {scripts.map(s => {
                    const label = s.src || `inline script ${shortHash(s.sha384)}`;
                    return (
                        <tr key={s.id} className="border-t border-[#F0EEE8] align-top">
                            <td className="max-w-[420px] px-3 py-2.5">
                                <div className="break-all font-semibold text-ink">{label}</div>
                                <div className="text-[11px] text-ink-soft">
                                    {s.kind}{s.third_party ? ' · third party' : ' · first party'}
                                    {!s.present && ' · removed'} · first seen {s.first_seen_at.slice(0, 10)}
                                </div>
                                {s.authorized && s.justification && (
                                    <div className="mt-1 text-xs text-ink-soft">Justification: {s.justification}</div>
                                )}
                                {openId === s.id && (
                                    <ApproveForm scriptLabel={label} pending={approvingId === s.id}
                                        onSubmit={(j) => { onApprove(s, j); setOpenId(null); }} onCancel={() => setOpenId(null)} />
                                )}
                            </td>
                            <td className="px-2 py-2.5 font-mono text-[11px] text-ink-soft" title={s.sha384 ?? ''}>{shortHash(s.sha384)}</td>
                            <td className="px-2 py-2.5 text-xs">{s.integrity ? 'yes' : <span className="font-semibold text-pastel-ink-butter">none</span>}</td>
                            <td className="px-3 py-2.5">
                                {s.authorized ? (
                                    <div className="space-y-1">
                                        <Pill tone="mint">approved</Pill>
                                        <button type="button" className="block text-xs font-medium text-ink-soft underline-offset-2 hover:text-ink hover:underline"
                                            onClick={() => onRevoke(s)}>Withdraw approval</button>
                                    </div>
                                ) : (
                                    <div className="space-y-1">
                                        <Pill tone="peach">unapproved</Pill>
                                        {s.present && openId !== s.id && (
                                            <button type="button" className="block text-xs font-semibold text-ink underline underline-offset-2"
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
        </div>
    );
}
