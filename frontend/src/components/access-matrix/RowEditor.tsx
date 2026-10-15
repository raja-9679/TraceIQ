import type { MatrixRow, ProjectCase, RowKind, SwapSpec } from '@/api/accessMatrix';
import { bodyText, defaultSwap, parseBody } from './grid';

const METHODS = ['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'];
const field = 'h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-xs';
const lbl = 'block text-[10px] font-semibold uppercase tracking-wide text-slate-500 mb-0.5';

interface Props {
    row: MatrixRow;
    personas: { id: number; name: string }[];
    cases: ProjectCase[];
    onChange: (row: MatrixRow) => void;
    onRemove: () => void;
}

/** The target half of a matrix row: what is requested, and (optionally) how
 * to swap in another identity's resource id. */
export default function RowEditor({ row, personas, cases, onChange, onRemove }: Props) {
    const set = (patch: Partial<MatrixRow>) => onChange({ ...row, ...patch });
    const setSwap = (patch: Partial<SwapSpec>) => row.swap && set({ swap: { ...row.swap, ...patch } });
    const id = (s: string) => `${row.key}-${s}`;

    return (
        <div className="space-y-2 min-w-[300px]">
            <div className="grid grid-cols-[1fr_110px] gap-2">
                <div>
                    <label className={lbl} htmlFor={id('label')}>Label</label>
                    <input id={id('label')} className={field} value={row.label} placeholder="e.g. Order detail"
                        onChange={(e) => set({ label: e.target.value })} />
                </div>
                <div>
                    <label className={lbl} htmlFor={id('kind')}>Target</label>
                    <select id={id('kind')} className={field} value={row.kind}
                        onChange={(e) => set({ kind: e.target.value as RowKind, method: 'GET' })}>
                        <option value="request">Request</option>
                        <option value="page">Page</option>
                        <option value="case">Test case</option>
                    </select>
                </div>
            </div>

            {row.kind === 'case' ? (
                <div>
                    <label className={lbl} htmlFor={id('case')}>Test case</label>
                    <select id={id('case')} className={field} value={row.test_case_id ?? ''}
                        onChange={(e) => set({ test_case_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Choose a case…</option>
                        {cases.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                </div>
            ) : (
                <div className="grid grid-cols-[90px_1fr] gap-2">
                    <div>
                        <label className={lbl} htmlFor={id('method')}>Method</label>
                        <select id={id('method')} className={field} value={row.kind === 'page' ? 'GET' : row.method || 'GET'}
                            disabled={row.kind === 'page'} onChange={(e) => set({ method: e.target.value })}>
                            {METHODS.map(m => <option key={m}>{m}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={lbl} htmlFor={id('url')}>{row.kind === 'page' ? 'Page URL' : 'URL or path'}</label>
                        <input id={id('url')} className={field} value={row.url ?? ''} placeholder="/api/orders/{id}"
                            onChange={(e) => set({ url: e.target.value })} />
                    </div>
                </div>
            )}

            {row.kind === 'page' && (
                <div>
                    <label className={lbl} htmlFor={id('selector')}>Element selector (optional)</label>
                    <input id={id('selector')} className={field} value={row.selector ?? ''} placeholder="[data-testid=admin-panel]"
                        onChange={(e) => set({ selector: e.target.value || null })} />
                </div>
            )}

            {row.kind === 'request' && row.method !== 'GET' && row.method !== 'HEAD' && (
                <div>
                    <label className={lbl} htmlFor={id('body')}>Body (JSON or text, optional)</label>
                    <textarea id={id('body')} className={`${field} h-16 py-1 font-mono`} defaultValue={bodyText(row.body)}
                        onBlur={(e) => set({ body: parseBody(e.target.value) })} />
                </div>
            )}

            {row.kind !== 'case' && (
                <label className="flex items-center gap-2 text-xs text-slate-600">
                    <input type="checkbox" checked={!!row.swap} disabled={!personas.length && !row.swap}
                        onChange={(e) => set({ swap: e.target.checked ? defaultSwap(personas[0].id) : null })} />
                    ID swap — fetch an id as the owner, then request it as every identity
                </label>
            )}

            {row.swap && (
                <div className="rounded-md border border-indigo-100 bg-indigo-50/40 p-2 space-y-2">
                    <div className="grid grid-cols-2 gap-2">
                        <div>
                            <label className={lbl} htmlFor={id('owner')}>Owner persona</label>
                            <select id={id('owner')} className={field} value={row.swap.owner_persona_id}
                                onChange={(e) => setSwap({ owner_persona_id: Number(e.target.value) })}>
                                {personas.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                            </select>
                        </div>
                        <div>
                            <label className={lbl} htmlFor={id('ph')}>Placeholder in URL</label>
                            <input id={id('ph')} className={field} value={row.swap.placeholder}
                                onChange={(e) => setSwap({ placeholder: e.target.value })} />
                        </div>
                    </div>
                    <div className="grid grid-cols-[90px_1fr] gap-2">
                        <div>
                            <label className={lbl} htmlFor={id('omethod')}>Owner method</label>
                            <select id={id('omethod')} className={field} value={row.swap.method}
                                onChange={(e) => setSwap({ method: e.target.value })}>
                                {METHODS.map(m => <option key={m}>{m}</option>)}
                            </select>
                        </div>
                        <div>
                            <label className={lbl} htmlFor={id('ourl')}>Owner URL (lists the owner's resources)</label>
                            <input id={id('ourl')} className={field} value={row.swap.url} placeholder="/api/orders"
                                onChange={(e) => setSwap({ url: e.target.value })} />
                        </div>
                    </div>
                    <div className="grid grid-cols-[90px_1fr] gap-2">
                        <div>
                            <label className={lbl} htmlFor={id('xsrc')}>Extract from</label>
                            <select id={id('xsrc')} className={field} value={row.swap.extractor.source}
                                onChange={(e) => setSwap({ extractor: { ...row.swap!.extractor, source: e.target.value as 'json' | 'regex' | 'header' } })}>
                                <option value="json">JSON path</option>
                                <option value="regex">Regex</option>
                                <option value="header">Header</option>
                            </select>
                        </div>
                        <div>
                            <label className={lbl} htmlFor={id('xexpr')}>Extractor</label>
                            <input id={id('xexpr')} className={field} value={row.swap.extractor.expr} placeholder="data.items.0.id"
                                onChange={(e) => setSwap({ extractor: { ...row.swap!.extractor, expr: e.target.value } })} />
                        </div>
                    </div>
                </div>
            )}

            <button type="button" onClick={onRemove} className="text-[11px] font-medium text-rose-600 hover:underline">
                Remove row
            </button>
        </div>
    );
}
