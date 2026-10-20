import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input } from '@/components/ui/input';
import { harFilesApi } from '@/api/harFiles';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { SEQUENCE_EXAMPLE, harIdParam, sequenceToText, textToSequence, withMode } from './networkMockSteps';

interface Props {
    type: string;
    params: Record<string, unknown>;
    selector?: string;
    value?: string;
    onParams: (params: Record<string, unknown>) => void;
    onSelector: (value: string) => void;
    onValue: (value: string) => void;
}

const field = 'h-10 rounded-xl bg-white font-mono text-sm';
const select = 'h-10 rounded-xl border border-slate-200 bg-white px-2 text-sm';

function HarReplayFields({ params, selector, onParams, onSelector }: Props) {
    const projectId = useActiveProjectId();
    const hars = useQuery({
        queryKey: ['har-files', projectId],
        queryFn: () => harFilesApi.list(projectId!),
        enabled: !!projectId,
    });
    const current = params.har_id === undefined ? '' : String(params.har_id);
    const options = hars.data ?? [];
    return (
        <div className="flex-1 w-full grid grid-cols-1 sm:grid-cols-3 gap-2">
            {options.length > 0 ? (
                <select aria-label="HAR file" className={select} value={current}
                    onChange={e => onParams({ ...params, har_id: harIdParam(e.target.value) })}>
                    <option value="">Choose a HAR…</option>
                    {options.map(h => <option key={h.id} value={h.id}>{h.name} (id {h.id})</option>)}
                </select>
            ) : (
                <Input aria-label="HAR file id" placeholder="HAR id (Environments → Recorded traffic)" inputMode="numeric"
                    value={current} onChange={e => onParams({ ...params, har_id: harIdParam(e.target.value) })} className={field} />
            )}
            <Input aria-label="Only URLs matching (glob, optional)" placeholder="**/api/** (optional)"
                value={selector || ''} onChange={e => onSelector(e.target.value)} className={field} />
            <select aria-label="When the HAR has no match" className={select}
                value={String(params.not_found || 'abort')}
                onChange={e => onParams({ ...params, not_found: e.target.value === 'abort' ? undefined : e.target.value })}>
                <option value="abort">No match: fail the request</option>
                <option value="fallback">No match: go to the network</option>
            </select>
        </div>
    );
}

function MockResponseFields({ params, selector, value, onParams, onSelector, onValue }: Props) {
    const sequenceMode = params.sequence !== undefined;
    const [text, setText] = React.useState(() => sequenceToText(params.sequence) || SEQUENCE_EXAMPLE);
    const [error, setError] = React.useState<string | null>(null);
    const editSequence = (t: string) => {
        setText(t);
        const parsed = textToSequence(t);
        setError(parsed.error ?? null);
        if (parsed.sequence) onParams({ ...params, sequence: parsed.sequence });
    };
    return (
        <div className="flex-1 w-full space-y-2">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <Input aria-label="URL pattern to mock" placeholder="**/api/me" value={selector || ''}
                    onChange={e => onSelector(e.target.value)} className={field} />
                <select aria-label="Mock mode" className={select} value={sequenceMode ? 'sequence' : 'single'}
                    onChange={e => {
                        const mode = e.target.value as 'single' | 'sequence';
                        const next = withMode(params, mode);
                        if (mode === 'sequence') setText(sequenceToText(next.sequence));
                        onParams(next);
                    }}>
                    <option value="single">Same response every time</option>
                    <option value="sequence">Sequence (nth call → nth response)</option>
                </select>
                {sequenceMode ? (
                    <select aria-label="After the sequence" className={select} value={String(params.after_sequence || 'repeat_last')}
                        onChange={e => onParams({ ...params, after_sequence: e.target.value === 'repeat_last' ? undefined : e.target.value })}>
                        <option value="repeat_last">Then repeat the last response</option>
                        <option value="fallthrough">Then go to the network</option>
                    </select>
                ) : (
                    <Input aria-label="Status code" placeholder="Status (default 200)" inputMode="numeric"
                        value={params.status === undefined ? '' : String(params.status)}
                        onChange={e => onParams({ ...params, status: e.target.value === '' ? undefined : Number(e.target.value) })}
                        className={field} />
                )}
            </div>
            {sequenceMode ? (
                <>
                    <textarea aria-label="Response sequence (JSON list)" rows={6} value={text}
                        onChange={e => editSequence(e.target.value)}
                        className="w-full rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs" />
                    {error && <p className="text-xs text-rose-600">{error}</p>}
                </>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <Input aria-label="Content type" placeholder="Content type (default text/plain)"
                        value={String(params.content_type || '')}
                        onChange={e => onParams({ ...params, content_type: e.target.value || undefined })} className={field} />
                    <Input aria-label="Response body" placeholder="Body" value={value || ''}
                        onChange={e => onValue(e.target.value)} className={`${field} sm:col-span-2`} />
                </div>
            )}
        </div>
    );
}

/** Labelled inputs for mock-response and har-replay (see networkMockSteps.ts). */
export const NetworkMockStepFields: React.FC<Props> = (props) =>
    props.type === 'har-replay' ? <HarReplayFields {...props} /> : <MockResponseFields {...props} />;
