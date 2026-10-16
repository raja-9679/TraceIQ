import { cn } from '@/lib/utils';

/** The one label every demo surface carries, so generated data is never mistaken for real. */
export default function DemoBadge({ className }: { className?: string }) {
    return (
        <span
            className={cn('inline-flex flex-none items-center rounded-full bg-pastel-butter px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.06em] text-pastel-ink-butter', className)}
            title="Demo workspace: generated sample data, never sends notifications"
        >
            Demo
        </span>
    );
}
