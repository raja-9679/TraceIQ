import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Signed-out shell for Direction A: brand wash on the left (chrome), the form
// in a white card on the warm background on the right. On phones it collapses
// to one column with the logo above the card.

export function LogoMark({ size = 'md' }: { size?: 'md' | 'lg' }) {
    const lg = size === 'lg';
    return (
        <span className="flex items-center gap-2.5">
            <span className={cn('flex items-center justify-center bg-ink', lg ? 'h-11 w-11 rounded-[14px]' : 'h-9 w-9 rounded-[11px]')}>
                <svg width={lg ? 24 : 20} height={lg ? 17 : 14} viewBox="0 0 28 20" fill="none" aria-hidden="true">
                    <path d="M1 10h6l3-8 4 16 3-8h10" stroke="#FFFFFF" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
            </span>
            <span className={cn('font-bold tracking-tight text-ink', lg ? 'text-2xl' : 'text-lg')}>TraceIQ</span>
        </span>
    );
}

export function AuthLayout({
    title, subtitle, children, footer,
}: {
    title?: ReactNode;
    subtitle?: ReactNode;
    children: ReactNode;
    footer?: ReactNode;
}) {
    return (
        <div className="flex min-h-screen w-full bg-background text-ink">
            <aside className="bg-tiq-gradient hidden w-[42%] max-w-[560px] flex-col justify-between border-r border-white/80 px-12 py-10 lg:flex">
                <LogoMark size="lg" />
                <div className="flex max-w-[24ch] flex-col gap-3">
                    <p className="text-[32px] font-bold leading-[1.15] tracking-[-0.03em] text-ink">
                        Record browser journeys, run them everywhere, and know why they failed.
                    </p>
                    <p className="text-[15px] leading-relaxed text-ink-soft">UI testing and automation for teams.</p>
                </div>
                <span className="text-xs text-ink-soft">TraceIQ</span>
            </aside>

            <main className="flex flex-1 flex-col items-center justify-center px-4 py-10 sm:px-8">
                <div className="flex w-full max-w-[420px] flex-col gap-6">
                    <div className="lg:hidden">
                        <LogoMark />
                    </div>
                    <div className="flex flex-col gap-6 rounded-3xl border border-border bg-white p-6 shadow-[0_12px_40px_rgba(28,27,34,0.06)] sm:p-8">
                        {(title || subtitle) && (
                            <div className="flex flex-col gap-1.5">
                                {title && <h1 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{title}</h1>}
                                {subtitle && <p className="text-sm text-ink-soft">{subtitle}</p>}
                            </div>
                        )}
                        {children}
                    </div>
                    {footer && <div className="text-center text-sm text-ink-soft">{footer}</div>}
                </div>
            </main>
        </div>
    );
}

export function AuthField({
    id, label, aside, error, hint, children,
}: {
    id: string;
    label: ReactNode;
    aside?: ReactNode;
    error?: string;
    hint?: ReactNode;
    children: ReactNode;
}) {
    return (
        <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-2 px-1">
                <label htmlFor={id} className="text-[13px] font-semibold text-ink">{label}</label>
                {aside}
            </div>
            {children}
            {hint && <p className="px-1 text-xs text-ink-soft">{hint}</p>}
            {error && <p id={`${id}-error`} className="px-1 text-xs font-medium text-pastel-ink-peach">{error}</p>}
        </div>
    );
}

export function AuthError({ children }: { children: ReactNode }) {
    return (
        <div
            role="alert"
            className="rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink shadow-[inset_4px_0_0_#E0603A]"
        >
            <span className="pl-1">{children}</span>
        </div>
    );
}
