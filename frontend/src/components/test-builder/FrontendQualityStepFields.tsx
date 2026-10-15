import React from 'react';
import { Input } from '@/components/ui/input';
import { BUDGET_FIELDS, listParam, withBudget } from './frontendQualitySteps';

interface Props {
    type: string;
    params: Record<string, unknown>;
    selector?: string;
    onParam: (key: string, value: unknown) => void;
    onSelector: (value: string) => void;
}

const field = 'h-10 rounded-xl bg-white font-mono text-sm';

/** Labelled inputs for the frontend-quality steps (see frontendQualitySteps.ts). */
export const FrontendQualityStepFields: React.FC<Props> = ({ type, params, selector, onParam, onSelector }) => {
    const ignore = Array.isArray(params?.ignore) ? (params.ignore as string[]).join(', ') : '';
    if (type === 'check-page-budget') {
        const budgets = (params?.budgets || {}) as Record<string, unknown>;
        return (
            <div className="flex-1 w-full space-y-2">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {BUDGET_FIELDS.map(f => (
                        <Input key={f.key} aria-label={f.label} placeholder={f.label} inputMode="decimal"
                            value={budgets[f.key] === undefined ? '' : String(budgets[f.key])}
                            onChange={e => onParam('budgets', withBudget(budgets, f.key, e.target.value))}
                            className={field} />
                    ))}
                </div>
                <div className="flex flex-wrap gap-3 items-center text-xs text-slate-600">
                    <label className="flex items-center gap-2">
                        SEO checks
                        <select aria-label="SEO checks" className="h-8 rounded-lg border border-slate-200 px-2"
                            value={String(params?.seo || 'warn')} onChange={e => onParam('seo', e.target.value === 'warn' ? undefined : e.target.value)}>
                            <option value="warn">report only</option>
                            <option value="fail">fail on SEO errors</option>
                            <option value="off">off</option>
                        </select>
                    </label>
                    <label className="flex items-center gap-2">
                        <input type="checkbox" checked={params?.fail === false}
                            onChange={e => onParam('fail', e.target.checked ? false : undefined)} />
                        Record only (never fail)
                    </label>
                </div>
            </div>
        );
    }
    return (
        <div className="flex-1 w-full grid grid-cols-1 sm:grid-cols-3 gap-2">
            {type === 'expect-rtl-layout' && (
                <label className="flex items-center gap-2 text-xs text-slate-600">
                    Direction
                    <select aria-label="Expected direction" className="h-10 rounded-xl border border-slate-200 px-2"
                        value={String(params?.expect_dir || 'rtl')}
                        onChange={e => onParam('expect_dir', e.target.value === 'rtl' ? undefined : e.target.value)}>
                        <option value="rtl">right-to-left</option>
                        <option value="ltr">left-to-right</option>
                    </select>
                </label>
            )}
            <Input aria-label={type === 'expect-rtl-layout' ? 'Element that must also be RTL (optional)' : 'Only check inside (selector, optional)'}
                placeholder={type === 'expect-rtl-layout' ? 'Element that must be RTL (optional)' : 'Container selector (optional)'}
                value={selector || ''} onChange={e => onSelector(e.target.value)} className={field} />
            <Input aria-label="Tolerance (px)" placeholder="Tolerance px (default 1)" inputMode="numeric"
                value={params?.tolerance_px === undefined ? '' : String(params.tolerance_px)}
                onChange={e => onParam('tolerance_px', e.target.value === '' ? undefined : Number(e.target.value))}
                className={field} />
            <Input aria-label="Ignore selectors (comma separated)" placeholder="Ignore: .carousel, .ticker"
                defaultValue={ignore} onBlur={e => onParam('ignore', listParam(e.target.value))} className={field} />
        </div>
    );
};
