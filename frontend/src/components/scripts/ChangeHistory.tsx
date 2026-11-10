import type { PageChange } from '@/api/estates';

const TONE: Record<string, string> = {
    script_added: 'text-pastel-ink-peach', script_changed: 'text-pastel-ink-peach', defacement: 'text-pastel-ink-peach',
    headers_changed: 'text-pastel-ink-butter', script_removed: 'text-ink-soft', script_approved: 'text-pastel-ink-mint',
};

export default function ChangeHistory({ changes }: { changes: PageChange[] }) {
    if (changes.length === 0) return <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No changes recorded yet.</p>;
    return (
        <ol className="divide-y divide-[#F0EEE8] overflow-hidden rounded-2xl border border-border text-sm">
            {changes.map(c => (
                <li key={c.id} className="flex flex-wrap gap-x-3 gap-y-1 px-4 py-2.5">
                    <time className="shrink-0 font-mono text-[11px] text-ink-soft" dateTime={c.created_at}>
                        {c.created_at.slice(0, 16).replace('T', ' ')}
                    </time>
                    <span className={`shrink-0 text-[11px] font-semibold uppercase ${TONE[c.kind] ?? 'text-ink-soft'}`}>
                        {c.kind.replace(/_/g, ' ')}
                    </span>
                    <span className="min-w-0 break-all text-ink">{c.summary}</span>
                </li>
            ))}
        </ol>
    );
}
