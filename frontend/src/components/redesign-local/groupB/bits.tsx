import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Small Direction A pieces shared by the Runs & Releases sub-pages
// (runs list, comparison, traceability, analytics, schedules).

export function Segmented<T extends string>({
    label, value, options, onChange,
}: {
    label: string;
    value: T;
    options: { id: T; label: string }[];
    onChange: (v: T) => void;
}) {
    return (
        <div role="group" aria-label={label} className="inline-flex flex-wrap gap-1 rounded-full bg-[#EFEDE7] p-1">
            {options.map((o) => {
                const active = o.id === value;
                return (
                    <button
                        key={o.id || 'all'}
                        type="button"
                        aria-pressed={active}
                        onClick={() => onChange(o.id)}
                        className={cn(
                            'min-h-9 rounded-full px-3.5 text-[13px] font-semibold transition-colors',
                            active ? 'bg-white text-ink shadow-sm' : 'text-ink-soft hover:text-ink'
                        )}
                    >
                        {o.label}
                    </button>
                );
            })}
        </div>
    );
}

export function Tile({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className={cn('text-[26px] font-bold leading-tight tracking-tight text-ink tabular-nums', tone)}>{value}</span>
            {hint && <span className="text-xs text-ink-soft">{hint}</span>}
        </div>
    );
}

export function SkeletonRows({ count = 4, className }: { count?: number; className?: string }) {
    return (
        <div className={cn('flex flex-col gap-2', className)} aria-busy="true" aria-label="Loading">
            {Array.from({ length: count }).map((_, i) => (
                <div key={i} className="h-[60px] animate-pulse rounded-2xl bg-white/70" />
            ))}
        </div>
    );
}
