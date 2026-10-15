import React from 'react';
import { Input } from '@/components/ui/input';
import { autoCleanupHint, cleanupFor, cleanupMode, type CleanupMode, type CleanupObject } from './cleanupSpec';

const METHODS = ['DELETE', 'POST', 'PUT', 'PATCH', 'GET'];

// Registers a request that deletes what this step created; the worker runs
// it after the test even when the test fails.
export const CleanupField: React.FC<{
    stepType: string;
    spec: unknown;
    onChange: (next: true | CleanupObject | undefined) => void;
}> = ({ stepType, spec, onChange }) => {
    const mode = cleanupMode(spec);
    const custom = mode === 'custom' ? (spec as CleanupObject) : null;
    return (
        <div className="w-full bg-slate-50 border-t border-slate-100 px-5 py-4 rounded-b-2xl space-y-2">
            <label className="flex flex-col gap-1 text-xs font-bold text-slate-500 uppercase tracking-widest sm:flex-row sm:items-center sm:gap-3">
                Clean up afterwards
                <select
                    value={mode}
                    onChange={(e) => onChange(cleanupFor(e.target.value as CleanupMode, spec))}
                    className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-medium normal-case tracking-normal text-slate-700"
                >
                    <option value="off">No cleanup</option>
                    <option value="auto">Delete what this created</option>
                    <option value="custom">Custom cleanup request</option>
                </select>
            </label>
            {mode === 'auto' && <p className="text-[11px] text-slate-500">{autoCleanupHint(stepType)} Runs after the test, even if it fails.</p>}
            {custom && (
                <div className="flex flex-col gap-2 sm:flex-row">
                    <label className="text-[11px] font-semibold text-slate-500 sm:w-32">
                        Method
                        <select
                            value={String(custom.method || 'DELETE')}
                            onChange={(e) => onChange({ ...custom, method: e.target.value })}
                            className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs"
                        >
                            {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
                        </select>
                    </label>
                    <label className="flex-1 text-[11px] font-semibold text-slate-500">
                        Cleanup URL
                        <Input
                            value={String(custom.url || '')}
                            onChange={(e) => onChange({ ...custom, url: e.target.value })}
                            placeholder="/api/items/{{item_id}}"
                            className="mt-1 h-9 bg-white font-mono text-xs"
                        />
                    </label>
                </div>
            )}
        </div>
    );
};
