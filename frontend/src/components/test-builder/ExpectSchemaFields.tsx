import React, { useState } from 'react';
import { Input } from '@/components/ui/input';
import {
    buildOpenApiSchema, openApiRefError, parseInlineSchema, schemaMode,
    type ExpectSchema, type ExpectSchemaMode,
} from './expectSchema';

interface Props {
    value: ExpectSchema | undefined;
    onChange: (value: ExpectSchema | undefined) => void;
}

const MODES: { mode: ExpectSchemaMode; label: string }[] = [
    { mode: 'none', label: 'No schema check' },
    { mode: 'inline', label: 'Inline JSON Schema' },
    { mode: 'openapi', label: 'OpenAPI operation' },
];

const input = 'h-10 text-xs font-mono bg-white border-slate-200 rounded-lg';

export const ExpectSchemaFields: React.FC<Props> = ({ value, onChange }) => {
    const [mode, setMode] = useState<ExpectSchemaMode>(schemaMode(value));
    const [text, setText] = useState(value?.schema ? JSON.stringify(value.schema, null, 2) : '');
    const [textError, setTextError] = useState<string | null>(null);
    const [ref, setRef] = useState({
        operationId: value?.openapi?.operationId || '',
        method: value?.openapi?.method || '',
        path: value?.openapi?.path || '',
        status: value?.status || '',
    });

    const pick = (next: ExpectSchemaMode) => {
        setMode(next);
        if (next === 'none') onChange(undefined);
        else if (next === 'inline') {
            const parsed = parseInlineSchema(text);
            if (parsed.schema) onChange({ schema: parsed.schema });
        } else if (!openApiRefError(buildOpenApiSchema(ref))) {
            onChange(buildOpenApiSchema(ref));
        }
    };

    const editRef = (key: keyof typeof ref, v: string) => {
        const next = { ...ref, [key]: v };
        setRef(next);
        const built = buildOpenApiSchema(next);
        if (!openApiRefError(built)) onChange(built);
    };

    const refError = mode === 'openapi' ? openApiRefError(buildOpenApiSchema(ref)) : null;

    return (
        <div className="space-y-2">
            <span className="text-xs font-bold text-slate-500 uppercase tracking-widest">Response schema</span>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Response schema check">
                {MODES.map((m) => (
                    <button key={m.mode} type="button" aria-pressed={mode === m.mode}
                        onClick={() => pick(m.mode)}
                        className={`px-3 h-8 rounded-full text-xs font-medium border ${mode === m.mode
                            ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-600 border-slate-200'}`}>
                        {m.label}
                    </button>
                ))}
            </div>
            {mode === 'inline' && (
                <div>
                    <textarea aria-label="JSON Schema"
                        className="w-full h-32 p-3 text-xs font-mono border border-slate-200 rounded-xl bg-white text-slate-700 resize-y"
                        placeholder='{"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}'
                        value={text}
                        onChange={(e) => {
                            setText(e.target.value);
                            const parsed = parseInlineSchema(e.target.value);
                            setTextError(parsed.error || null);
                            if (parsed.schema) onChange({ schema: parsed.schema });
                        }} />
                    {textError && <p className="mt-1 text-xs text-rose-600">{textError}</p>}
                </div>
            )}
            {mode === 'openapi' && (
                <div className="grid gap-2 sm:grid-cols-4">
                    <Input aria-label="Operation ID" placeholder="operationId (e.g. getPet)" className={input}
                        value={ref.operationId} onChange={(e) => editRef('operationId', e.target.value)} />
                    <Input aria-label="HTTP method" placeholder="or method (GET)" className={input}
                        value={ref.method} onChange={(e) => editRef('method', e.target.value)} />
                    <Input aria-label="Spec path" placeholder="and path (/pets/{id})" className={input}
                        value={ref.path} onChange={(e) => editRef('path', e.target.value)} />
                    <Input aria-label="Response status" placeholder="status (default: first 2xx)" className={input}
                        value={ref.status} onChange={(e) => editRef('status', e.target.value)} />
                    {refError && <p className="sm:col-span-4 text-xs text-rose-600">{refError}</p>}
                </div>
            )}
            <p className="text-[10px] text-gray-400 italic">
                Strict check of the JSON body: every violation is reported by path. OpenAPI references use the
                project's stored spec (imported with "from OpenAPI" or set via the API) and fail the step if they
                cannot be resolved.
            </p>
        </div>
    );
};
