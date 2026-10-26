import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import { Plus, X, ExternalLink } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { caseCallsApi } from '@/api/caseCalls';
import { getTestCase } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    calledCaseId, isValidKey, overridesToRows, rowsToOverrides, tokensIn, type OverrideRow,
} from './caseCalls';

interface Props {
    params: Record<string, unknown>;
    onParams: (params: Record<string, unknown>) => void;
}

const field = 'h-10 rounded-xl bg-white text-sm';
const select = 'h-10 rounded-xl border border-slate-200 bg-white px-2 text-sm';

/** Fields of a run-test step: which test to run here, and optional values
 *  that differ from this test's for that one call. */
export function RunTestStepFields({ params, onParams }: Props) {
    const projectId = useActiveProjectId();
    const { caseId: editingId } = useParams();
    const selfId = editingId ? parseInt(editingId, 10) : null;
    const current = calledCaseId(params);
    const [q, setQ] = React.useState('');
    const [rows, setRows] = React.useState<OverrideRow[]>(() => overridesToRows(params.overrides));
    const listId = React.useId();

    const matches = useQuery({
        queryKey: ['case-search', projectId, q],
        queryFn: () => caseCallsApi.search(projectId!, q),
        enabled: !!projectId,
        staleTime: 30_000,
    });
    const called = useQuery({
        queryKey: ['testCase', String(current)],
        queryFn: () => getTestCase(current!),
        enabled: !!current,
        retry: false,
    });

    const options = (matches.data ?? []).filter(c => c.id !== selfId && c.executor !== 'manual');
    const suggestions = tokensIn(called.data?.steps);

    const setRowsAndSave = (next: OverrideRow[]) => {
        setRows(next);
        onParams({ ...params, overrides: rowsToOverrides(next) });
    };

    return (
        <div className="flex-1 w-full flex flex-col gap-2">
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_1.4fr_auto] gap-2 items-center">
                <Input aria-label="Find a test" placeholder="Find a test by name…" value={q}
                    onChange={e => setQ(e.target.value)} className={field} />
                <select aria-label="Test to run" className={select} value={current ?? ''}
                    onChange={e => onParams({ ...params, case_id: e.target.value ? parseInt(e.target.value, 10) : undefined })}>
                    <option value="">{matches.isLoading ? 'Loading tests…' : 'Choose a test to run here…'}</option>
                    {current && !options.some(o => o.id === current) && (
                        <option value={current}>{called.data?.name ?? `Test #${current}`} (#{current})</option>
                    )}
                    {options.map(c => <option key={c.id} value={c.id}>{c.name} (#{c.id})</option>)}
                </select>
                {current && called.data?.test_suite_id ? (
                    <Link to={`/suites/${called.data.test_suite_id}/cases/${current}/edit`} target="_blank"
                        className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-900">
                        Open <ExternalLink size={12} />
                    </Link>
                ) : <span />}
            </div>
            {called.isError && current && (
                <p className="text-xs text-red-600">Test #{current} can't be found — pick another one.</p>
            )}

            <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-2.5">
                <p className="text-xs text-slate-500">
                    The test's steps run here, in the same browser, using this test's values.
                    To use different values in this step only (another user, say), add them below.
                </p>
                <datalist id={listId}>
                    {suggestions.map(s => <option key={s} value={s} />)}
                </datalist>
                {rows.map((r, i) => (
                    <div key={i} className="mt-2 grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
                        <Input aria-label={`Value name ${i + 1}`} placeholder="env.LOGIN_USER" list={listId}
                            value={r.key}
                            onChange={e => setRowsAndSave(rows.map((x, j) => j === i ? { ...x, key: e.target.value } : x))}
                            className={`${field} font-mono ${r.key && !isValidKey(r.key) ? 'border-red-300' : ''}`} />
                        <Input aria-label={`Value ${i + 1}`} placeholder="sub@example.com or {{secret.SUB_PASSWORD}}"
                            value={r.value}
                            onChange={e => setRowsAndSave(rows.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}
                            className={`${field} font-mono`} />
                        <button type="button" aria-label={`Remove value ${i + 1}`}
                            onClick={() => setRowsAndSave(rows.filter((_, j) => j !== i))}
                            className="p-1.5 text-slate-400 hover:text-red-500"><X size={14} /></button>
                    </div>
                ))}
                <button type="button" onClick={() => setRows([...rows, { key: '', value: '' }])}
                    className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-900">
                    <Plus size={12} /> Use a different value
                </button>
            </div>
        </div>
    );
}
