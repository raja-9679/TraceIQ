import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { PlayCircle } from 'lucide-react';
import { caseCallsApi } from '@/api/caseCalls';

/** Tests that run this one through a 'Run another test' step. Renders
 *  nothing when there are none. `warning` = the delete confirmation. */
export function UsedByNotice({ caseId, warning = false }: { caseId: number; warning?: boolean }) {
    const { data } = useQuery({
        queryKey: ['case-used-by', caseId],
        queryFn: () => caseCallsApi.usedBy(caseId),
    });
    const users = data?.used_by ?? [];
    if (!users.length) return null;
    const shown = users.slice(0, 8);
    return (
        <div role={warning ? 'alert' : undefined}
            className={`mb-4 rounded-2xl border p-3 text-sm ${warning
                ? 'border-amber-200 bg-amber-50 text-amber-900'
                : 'border-violet-100 bg-violet-50/50 text-slate-700'}`}>
            <p className="flex items-center gap-1.5 font-semibold">
                <PlayCircle size={14} aria-hidden />
                Used by {users.length} test{users.length === 1 ? '' : 's'}
                {warning ? ' — their “Run Test” step will fail once this one is deleted:' : ' (changes here apply to them too):'}
            </p>
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {shown.map(u => (
                    <li key={u.id}>
                        <Link to={u.test_suite_id ? `/suites/${u.test_suite_id}/cases/${u.id}/edit` : '#'}
                            className="inline-block rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs hover:border-slate-400">
                            {u.name} <span className="text-slate-400">#{u.id}</span>
                        </Link>
                    </li>
                ))}
                {users.length > shown.length && <li className="text-xs text-slate-500 self-center">and {users.length - shown.length} more</li>}
            </ul>
        </div>
    );
}
