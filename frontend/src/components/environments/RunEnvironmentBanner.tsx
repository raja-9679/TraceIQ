import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FlaskConical, Lock } from 'lucide-react';
import { api } from '@/lib/api';
import { runLockText, type RunEnvironmentLock } from './environmentLock';

/** WP16c: "Waiting for environment X held by run #N" / "Holding …" on a live run. */
export function RunEnvironmentBanner({ runId, live }: { runId: number; live: boolean }) {
    const { data } = useQuery({
        queryKey: ['run-environment-lock', runId],
        queryFn: async (): Promise<RunEnvironmentLock> => (await api.get(`/runs/${runId}/environment-lock`)).data,
        enabled: live,
        refetchInterval: live ? 10000 : false,
    });
    const text = live ? runLockText(data) : null;
    if (!text || !data) return null;
    return (
        <div role="status" className="flex items-start gap-3 rounded-3xl border border-border bg-white p-5 shadow-[inset_4px_0_0_#B9A8F0]">
            <Lock size={18} className="mt-0.5 flex-none text-ink" aria-hidden="true" />
            <p className="text-sm text-ink">
                {text}
                {data.state === 'waiting' && data.holder_run_id != null && (
                    <> <Link to={`/runs/${data.holder_run_id}`} className="font-semibold underline underline-offset-2">Open run #{data.holder_run_id}</Link></>
                )}
            </p>
        </div>
    );
}

/** WP16c: this run tried a proposed repair; it is excluded from every statistic. */
export function ValidationRunBanner({ kind }: { kind?: string | null }) {
    return (
        <div role="note" className="flex items-start gap-3 rounded-3xl border border-border bg-white p-5 shadow-[inset_4px_0_0_#9AD8B5]">
            <FlaskConical size={18} className="mt-0.5 flex-none text-ink" aria-hidden="true" />
            <p className="text-sm text-ink">
                Validation run: this case ran once with a proposed {kind === 'heal' ? 'selector heal' : 'test change'} applied
                to this run only. The stored test was not changed, and this run does not count towards pass rates,
                the quality gate, flake scores or reports. <Link to="/proposals" className="font-semibold underline underline-offset-2">Review proposals</Link>
            </p>
        </div>
    );
}
