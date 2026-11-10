import type { AccessMatrix } from '@/api/accessMatrix';
import { ListGroup, ListRow } from '@/components/redesign/primitives';

interface Props {
    matrices: AccessMatrix[];
    selectedId: number | 'new' | null;
    onSelect: (id: number | 'new') => void;
}

/** The project's matrices; "New matrix" lives in the page header. */
export default function MatrixList({ matrices, selectedId, onSelect }: Props) {
    return (
        <nav aria-label="Access matrices">
            <ListGroup title="Matrices" count={matrices.length}>
                {matrices.length === 0 && <p className="px-4 py-6 text-center text-sm text-ink-soft">No matrices in this project yet.</p>}
                {matrices.map(m => {
                    const ids = m.persona_ids.length + (m.include_anonymous ? 1 : 0);
                    return (
                        <ListRow
                            key={m.id}
                            selected={selectedId === m.id}
                            onSelect={() => onSelect(m.id)}
                            title={<span className="break-words">{m.name}</span>}
                            meta={`${m.rows.length} row(s) · ${ids} identit${ids === 1 ? 'y' : 'ies'}`}
                        />
                    );
                })}
            </ListGroup>
        </nav>
    );
}
