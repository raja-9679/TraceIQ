import type { Workspace } from '@/lib/api';
import { selectClass } from './selectClass';

/** Labelled workspace select for the page header toolbar. Hidden when there is only one. */
export function WorkspacePicker({ workspaces, value, onChange }: {
    workspaces: Workspace[];
    value: number | null;
    onChange: (id: number) => void;
}) {
    if (workspaces.length < 2) return null;
    return (
        <label className="flex items-center gap-2 text-sm font-medium text-ink">
            Workspace
            <select className={selectClass} value={value ?? ''} onChange={(e) => onChange(Number(e.target.value))}>
                {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
        </label>
    );
}
