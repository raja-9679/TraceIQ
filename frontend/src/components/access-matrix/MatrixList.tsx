import type { AccessMatrix } from '@/api/accessMatrix';
import { Button } from '@/components/ui/button';

interface Props {
    matrices: AccessMatrix[];
    selectedId: number | 'new' | null;
    onSelect: (id: number | 'new') => void;
}

export default function MatrixList({ matrices, selectedId, onSelect }: Props) {
    return (
        <nav aria-label="Access matrices" className="space-y-2">
            <Button type="button" variant="outline" className="w-full" onClick={() => onSelect('new')}>
                New matrix
            </Button>
            {matrices.length === 0 && <p className="text-xs text-slate-500">No matrices in this project yet.</p>}
            <ul className="space-y-1.5">
                {matrices.map(m => (
                    <li key={m.id}>
                        <button type="button" onClick={() => onSelect(m.id)} aria-current={selectedId === m.id ? 'true' : undefined}
                            className={`w-full rounded-lg border px-3 py-2 text-left ${selectedId === m.id ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                            <div className="truncate text-sm font-semibold text-slate-800">{m.name}</div>
                            <div className="text-[11px] text-slate-500">
                                {m.rows.length} row(s) · {m.persona_ids.length + (m.include_anonymous ? 1 : 0)} identit{m.persona_ids.length + (m.include_anonymous ? 1 : 0) === 1 ? 'y' : 'ies'}
                            </div>
                        </button>
                    </li>
                ))}
            </ul>
        </nav>
    );
}
