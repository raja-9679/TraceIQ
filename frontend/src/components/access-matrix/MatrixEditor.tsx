import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { accessMatrixApi, type AccessMatrix, type AccessMatrixInput } from '@/api/accessMatrix';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import GridEditor from './GridEditor';
import Prerequisites from './Prerequisites';
import { cleanDraft, columnKeys, columnLabel, draftFrom, emptyDraft, parseStatuses } from './model';
import { errorDetail } from './grid';

interface Props {
    projectId: number;
    matrix: AccessMatrix | null;          // null = creating a new one
    onSaved: (m: AccessMatrix) => void;
    onDeleted: () => void;
}

const lbl = 'block text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-1';

export default function MatrixEditor({ projectId, matrix, onSaved, onDeleted }: Props) {
    const qc = useQueryClient();
    const [draft, setDraft] = useState<AccessMatrixInput>(() => (matrix ? draftFrom(matrix) : emptyDraft()));
    const [denyText, setDenyText] = useState((matrix?.deny_statuses ?? []).join(', '));
    const [error, setError] = useState<string | null>(null);
    const personas = useQuery({ queryKey: ['am-personas', projectId], queryFn: () => accessMatrixApi.personas(projectId) });
    const cases = useQuery({ queryKey: ['am-cases', projectId], queryFn: () => accessMatrixApi.cases(projectId) });

    const names = Object.fromEntries((personas.data ?? []).map(p => [String(p.id), p.name]));
    const colKeys = columnKeys(draft.persona_ids, draft.include_anonymous);
    const columns = colKeys.map(k => ({ key: k, label: columnLabel(k, names) }));
    const chosen = draft.persona_ids.map(id => ({ id, name: names[String(id)] ?? `Persona #${id}` }));

    const invalidate = () => qc.invalidateQueries({ queryKey: ['access-matrices', projectId] });
    const save = useMutation({
        mutationFn: () => {
            const body = cleanDraft({ ...draft, deny_statuses: parseStatuses(denyText) });
            return matrix ? accessMatrixApi.update(matrix.id, body) : accessMatrixApi.create(projectId, body);
        },
        onSuccess: (m) => { setError(null); toast.success('Matrix saved'); invalidate(); onSaved(m); },
        onError: (e) => setError(errorDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => accessMatrixApi.remove(matrix!.id),
        onSuccess: () => { toast.success('Matrix deleted'); invalidate(); onDeleted(); },
        onError: (e) => setError(errorDetail(e)),
    });

    const togglePersona = (id: number, on: boolean) => setDraft(d => ({
        ...d, persona_ids: on ? [...d.persona_ids, id] : d.persona_ids.filter(x => x !== id),
    }));

    return (
        <div className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2">
                <div>
                    <label className={lbl} htmlFor="am-name">Name</label>
                    <Input id="am-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </div>
                <div>
                    <label className={lbl} htmlFor="am-base">Base URL (for relative paths)</label>
                    <Input id="am-base" value={draft.base_url ?? ''} placeholder="https://app.example.com"
                        onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} />
                </div>
                <div>
                    <label className={lbl} htmlFor="am-desc">Description</label>
                    <Input id="am-desc" value={draft.description ?? ''} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                </div>
                <div>
                    <label className={lbl} htmlFor="am-deny">Statuses that count as denied</label>
                    <Input id="am-deny" value={denyText} placeholder="401, 403, 404 (default)" onChange={(e) => setDenyText(e.target.value)} />
                </div>
            </div>

            <fieldset className="rounded-xl border border-slate-200 bg-white p-4">
                <legend className="px-1 text-sm font-bold text-slate-800">Identities (columns)</legend>
                {personas.isLoading ? <p className="text-xs text-slate-400">Loading personas…</p>
                    : personas.error ? <p className="text-xs text-rose-600">{errorDetail(personas.error)}</p>
                    : (personas.data ?? []).length === 0 ? (
                        <p className="text-xs text-slate-500">No personas in this project yet. Create personas (saved logged-in identities) to use them as columns.</p>
                    ) : (
                        <div className="flex flex-wrap gap-x-5 gap-y-2">
                            {(personas.data ?? []).map(p => (
                                <label key={p.id} className="flex items-center gap-2 text-sm text-slate-700">
                                    <input type="checkbox" checked={draft.persona_ids.includes(p.id)}
                                        onChange={(e) => togglePersona(p.id, e.target.checked)} />
                                    {p.name}{p.project_id === null && <span className="text-[10px] text-slate-400">(workspace)</span>}
                                </label>
                            ))}
                        </div>
                    )}
                <label className="mt-3 flex items-center gap-2 text-sm text-slate-700">
                    <input type="checkbox" checked={draft.include_anonymous}
                        onChange={(e) => setDraft({ ...draft, include_anonymous: e.target.checked })} />
                    Anonymous column (no session, no credential headers)
                </label>
            </fieldset>

            <GridEditor rows={draft.rows} columns={columns} personas={chosen} cases={cases.data ?? []}
                onChange={(rows) => setDraft({ ...draft, rows })} />

            <Prerequisites projectId={projectId} rows={draft.rows} baseUrl={draft.base_url} columns={colKeys} />

            {error && (
                <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800 whitespace-pre-wrap">
                    {error}
                </div>
            )}

            <div className="flex flex-wrap gap-2">
                <Button type="button" onClick={() => save.mutate()} disabled={save.isPending || !draft.name.trim()}>
                    {save.isPending ? 'Saving…' : matrix ? 'Save changes' : 'Create matrix'}
                </Button>
                {matrix && (
                    <Button type="button" variant="outline" className="text-rose-700" disabled={remove.isPending}
                        onClick={() => {
                            if (window.confirm(`Delete "${matrix.name}" and all of its runs?`)) remove.mutate();
                        }}>
                        Delete matrix
                    </Button>
                )}
            </div>
        </div>
    );
}
