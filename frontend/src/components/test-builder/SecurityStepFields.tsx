import React from 'react';
import { Input } from '@/components/ui/input';
import { SECURITY_STEP_FIELDS } from './securitySteps';

interface Props {
    type: string;
    params: Record<string, unknown>;
    value?: string;
    onParam: (key: string, value: unknown) => void;
    onValue: (value: string) => void;
}

/** Labelled inputs for the security assertion steps (see securitySteps.ts). */
export const SecurityStepFields: React.FC<Props> = ({ type, params, value, onParam, onValue }) => {
    const fields = SECURITY_STEP_FIELDS[type] || [];
    return (
        <div className="flex-1 w-full grid grid-cols-1 sm:grid-cols-2 gap-2">
            {fields.map(f => {
                const current = f.key === 'value' ? (value ?? '') : params?.[f.key];
                if (f.kind === 'bool') {
                    return (
                        <label key={f.key} className="flex items-center gap-2 text-xs text-slate-600">
                            <input type="checkbox" checked={current === true}
                                onChange={e => onParam(f.key, e.target.checked ? true : undefined)} />
                            {f.label}
                        </label>
                    );
                }
                return (
                    <Input key={f.key} aria-label={f.label} placeholder={f.placeholder || f.label}
                        value={current === undefined || current === null ? '' : String(current)}
                        onChange={e => {
                            const raw = e.target.value;
                            if (f.key === 'value') return onValue(raw);
                            onParam(f.key, f.kind === 'number' ? (raw === '' ? undefined : Number(raw)) : (raw || undefined));
                        }}
                        className="h-10 rounded-xl bg-white font-mono text-sm" />
                );
            })}
        </div>
    );
};
