import type { PageChange } from '@/api/estates';

const TONE: Record<string, string> = {
    script_added: 'text-rose-700', script_changed: 'text-rose-700', defacement: 'text-rose-700',
    headers_changed: 'text-amber-700', script_removed: 'text-slate-600', script_approved: 'text-emerald-700',
};

export default function ChangeHistory({ changes }: { changes: PageChange[] }) {
    if (changes.length === 0) return <p className="text-sm text-slate-500">No changes recorded yet.</p>;
    return (
        <ol className="space-y-1.5 text-sm">
            {changes.map(c => (
                <li key={c.id} className="flex gap-3">
                    <time className="shrink-0 font-mono text-[11px] text-slate-400" dateTime={c.created_at}>
                        {c.created_at.slice(0, 16).replace('T', ' ')}
                    </time>
                    <span className={`shrink-0 text-[11px] font-semibold uppercase ${TONE[c.kind] ?? 'text-slate-500'}`}>
                        {c.kind.replace(/_/g, ' ')}
                    </span>
                    <span className="break-all text-slate-700">{c.summary}</span>
                </li>
            ))}
        </ol>
    );
}
