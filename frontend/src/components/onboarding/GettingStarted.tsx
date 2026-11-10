import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { isHidden, setHidden, stepViews, type GettingStartedData } from './steps';

/**
 * "Get started" on Overview: the first steps for the active project, each
 * ticked off from real data (a test exists, it ran, a run passed, …). It goes
 * away by itself once everything is done, and can be hidden before that.
 */
export function GettingStarted({ projectId }: { projectId: number }) {
    // Re-read when the project changes (keyed by the caller), not on every render.
    const [hidden, setHiddenState] = useState(() => isHidden(projectId));

    const { data } = useQuery<GettingStartedData>({
        queryKey: ['getting-started', projectId],
        queryFn: () => api.get(`/projects/${projectId}/getting-started`).then((res) => res.data),
        enabled: !hidden,
        // The steps change because of what the user does on other pages.
        refetchOnWindowFocus: true,
        staleTime: 15_000,
    });

    // Nothing while loading or on error: a card that flashes in and out, or an
    // error box about a checklist, would be worse than no card.
    if (hidden || !data || data.complete) return null;
    const steps = stepViews(data);
    if (steps.length === 0) return null;

    return (
        <section aria-labelledby="getting-started-title" className="rounded-3xl border border-border bg-white">
            <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 sm:px-6">
                <div className="flex min-w-0 flex-col gap-1">
                    <h2 id="getting-started-title" className="text-lg font-bold tracking-[-0.02em] text-ink">Get started</h2>
                    <p className="text-sm text-ink-soft">
                        {data.done} of {data.total} done. Each step is ticked when it has happened in this project.
                    </p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={() => { setHidden(projectId, true); setHiddenState(true); }}>
                    Hide this
                </Button>
            </div>
            <div
                role="progressbar" aria-label="Steps done" aria-valuemin={0} aria-valuemax={data.total} aria-valuenow={data.done}
                className="mx-5 h-1.5 overflow-hidden rounded-full bg-background sm:mx-6"
            >
                <div className="h-full rounded-full bg-ink" style={{ width: `${(data.done / data.total) * 100}%` }} />
            </div>
            <ol className="mt-3 flex flex-col divide-y divide-[#F0EEE8] border-t border-[#F0EEE8]">
                {steps.map((step, i) => (
                    <li key={step.key} className="flex flex-wrap items-center gap-3 px-5 py-3.5 sm:px-6">
                        <span
                            aria-hidden="true"
                            className={cn(
                                'flex h-7 w-7 flex-none items-center justify-center rounded-full text-xs font-bold',
                                step.done ? 'bg-pastel-mint text-ink' : step.current ? 'bg-ink text-white' : 'border border-border text-ink-soft',
                            )}
                        >
                            {step.done ? <Check className="h-4 w-4" strokeWidth={2.6} /> : i + 1}
                        </span>
                        <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-0.5">
                            <span className={cn('text-sm font-semibold', step.done ? 'text-ink-soft line-through decoration-ink-soft/50' : 'text-ink')}>
                                {step.title}
                                <span className="sr-only">{step.done ? ' (done)' : step.current ? ' (next)' : ''}</span>
                            </span>
                            {!step.done && <span className="text-sm text-ink-soft">{step.text}</span>}
                        </div>
                        {!step.done && (
                            <Button asChild variant={step.current ? 'default' : 'outline'} size="sm">
                                <Link to={step.to}>{step.action}</Link>
                            </Button>
                        )}
                    </li>
                ))}
            </ol>
        </section>
    );
}
