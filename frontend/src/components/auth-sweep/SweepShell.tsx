import { useRef, useState, type ReactNode } from "react";
import { Check, Info, LayoutGrid, Sparkles, X, type LucideIcon } from "lucide-react";
import SweepMap from "./SweepMap";
import { NODES } from "./sweepScript";
import { p95, useSweep, type LogStatus } from "./useSweep";

const LOG_MARK: Record<LogStatus, ReactNode> = {
    pass: <Check className="w-3 h-3 text-emerald-400 shrink-0" />,
    heal: <Sparkles className="w-3 h-3 text-emerald-400 shrink-0" />,
    fail: <X className="w-3 h-3 text-rose-400 shrink-0" />,
    info: <Info className="w-3 h-3 text-amber-300 shrink-0" />,
};

export interface ShellPoint { icon: LucideIcon; title: string; desc: string }

function Wordmark({ dark }: { dark?: boolean }) {
    return (
        <div className="flex items-center gap-2.5">
            <span className={`w-7 h-7 rounded-md flex items-center justify-center ${dark ? "bg-white" : "bg-[#0B1220]"}`}>
                <LayoutGrid className={`w-3.5 h-3.5 ${dark ? "text-[#0B1220]" : "text-white"}`} />
            </span>
            <span className={`text-[15px] font-semibold tracking-tight ${dark ? "text-white" : "text-zinc-900"}`}>TraceIQ</span>
        </div>
    );
}

/**
 * Signed-out page frame, "ops console" split. Left: a navy panel with the
 * product statement, the scripted system sweep contained inside it, and a
 * live status line. Right: a plain white column holding the page's form,
 * with a top-right link and a footer. Typing in the form dims the sweep.
 * Below `lg` only the right column is shown.
 */
export default function SweepShell({ title, subtitle, points, trust, topLink, children }: {
    title: ReactNode;
    subtitle: string;
    points: ShellPoint[];
    trust: string[];
    topLink?: ReactNode;
    children: ReactNode;
}) {
    const sweep = useSweep();
    const [typing, setTyping] = useState(false);
    const formRef = useRef<HTMLDivElement>(null);
    const done = Object.values(sweep.status).filter((s) => s === "pass" || s === "healed").length;
    const latest = sweep.logs[0];

    return (
        <div className="min-h-screen w-full flex bg-white text-zinc-900 font-inter">
            <section
                className="hidden lg:flex w-[56%] xl:w-[58%] shrink-0 flex-col bg-[#0B1220] text-slate-300 px-12 xl:px-16 py-10 relative overflow-hidden"
                style={{
                    backgroundImage:
                        "linear-gradient(rgba(148,163,184,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,0.06) 1px, transparent 1px)",
                    backgroundSize: "48px 48px",
                }}
            >
                <Wordmark dark />

                <div className="mt-14 max-w-xl">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-sky-400">Quality intelligence platform</p>
                    <h1 className="mt-3 text-3xl xl:text-[34px] leading-tight font-semibold tracking-tight text-white">{title}</h1>
                    <p className="mt-3 text-[15px] leading-relaxed text-slate-400">{subtitle}</p>
                </div>

                <div aria-hidden className={`flex-1 min-h-[320px] my-8 mx-4 transition-opacity duration-500 ${typing ? "opacity-50" : ""}`}>
                    <SweepMap state={sweep} />
                </div>

                <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 font-mono text-[11px]">
                    <div className="flex items-center gap-2 text-slate-400">
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${sweep.probe ? "bg-sky-400 animate-pulse" : "bg-emerald-400"}`} />
                        <span className="text-slate-200">sweep #{sweep.sweep}</span>
                        <span className="tabular-nums">
                            {done}/{NODES.length} systems verified · {sweep.checks} checks · {sweep.healed} healed · p95 {p95(sweep.timings)} ms
                        </span>
                    </div>
                    <div className="mt-1.5 h-4 flex items-center gap-2 text-slate-300">
                        {latest && (
                            <>
                                {LOG_MARK[latest.status]}
                                <span className="truncate">{latest.text}</span>
                                {latest.ms !== undefined && <span className="text-slate-500 shrink-0">{latest.ms} ms</span>}
                            </>
                        )}
                    </div>
                </div>

                <div className="mt-6 grid grid-cols-2 xl:grid-cols-4 gap-x-6 gap-y-4 border-t border-slate-800 pt-6">
                    {points.map((p) => (
                        <div key={p.title}>
                            <div className="flex items-center gap-2 text-[13px] font-medium text-slate-100">
                                <p.icon className="w-3.5 h-3.5 text-sky-400" /> {p.title}
                            </div>
                            <p className="mt-1 text-xs leading-relaxed text-slate-500">{p.desc}</p>
                        </div>
                    ))}
                </div>

                <div className="mt-6 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-500">
                    {trust.map((t) => (
                        <span key={t} className="flex items-center gap-1.5">
                            <Check className="w-3 h-3 text-emerald-500" /> {t}
                        </span>
                    ))}
                </div>
            </section>

            <div className="flex-1 min-w-0 flex flex-col px-6 sm:px-10 py-8">
                <div className="flex items-center justify-between gap-4">
                    <div className="lg:invisible"><Wordmark /></div>
                    <div className="text-sm text-zinc-500 text-right">{topLink}</div>
                </div>

                <div
                    ref={formRef}
                    onInput={() => setTyping(true)}
                    onBlur={(e) => {
                        if (!formRef.current?.contains(e.relatedTarget as Node | null)) setTyping(false);
                    }}
                    className="flex-1 flex items-center justify-center py-10"
                >
                    <div className="w-full max-w-[380px]">{children}</div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-400">
                    <span>© {new Date().getFullYear()} TraceIQ</span>
                    <span>Trouble signing in? Contact your workspace administrator.</span>
                </div>
            </div>
        </div>
    );
}
