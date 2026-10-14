import { useRef, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { Check, Info, Sparkles, X, type LucideIcon } from "lucide-react";
import SweepMap from "./SweepMap";
import { NODES } from "./sweepScript";
import { p95, useSweep, type LogStatus } from "./useSweep";

const LOG_MARK: Record<LogStatus, ReactNode> = {
    pass: <Check className="w-3 h-3 text-emerald-600 shrink-0" />,
    heal: <Sparkles className="w-3 h-3 text-emerald-600 shrink-0" />,
    fail: <X className="w-3 h-3 text-red-600 shrink-0" />,
    info: <Info className="w-3 h-3 text-amber-600 shrink-0" />,
};

export interface ShellPoint { icon: LucideIcon; title: string; desc: string }

/**
 * Signed-out page frame. The scripted system sweep runs as a faded,
 * full-screen backdrop; in front of it sit a product panel (headline,
 * capability points, a one-line live status tied to the sweep, trust
 * signals) and the page's form. Typing in the form dims the backdrop.
 * Below `lg` only the form is shown.
 */
export default function SweepShell({ badge, title, subtitle, points, trust, children }: {
    badge: string;
    title: ReactNode;
    subtitle: string;
    points: ShellPoint[];
    trust: string[];
    children: ReactNode;
}) {
    const sweep = useSweep();
    const [typing, setTyping] = useState(false);
    const formRef = useRef<HTMLDivElement>(null);
    const done = Object.values(sweep.status).filter((s) => s === "pass" || s === "healed").length;
    const latest = sweep.logs[0];

    return (
        <div className="min-h-screen w-full flex bg-[#FAFAFA] text-zinc-900 font-inter relative overflow-hidden">
            <div
                aria-hidden
                className="absolute inset-0 z-0 hidden lg:block pointer-events-none"
                style={{
                    backgroundImage: "radial-gradient(circle, #E4E4E7 1px, transparent 1px)",
                    backgroundSize: "28px 28px",
                }}
            >
                <div className={`absolute inset-x-[4%] inset-y-[6%] transition-opacity duration-500 ${typing ? "opacity-20" : "opacity-35"}`}>
                    <SweepMap state={sweep} />
                </div>
            </div>

            <div className="w-full max-w-7xl mx-auto flex items-center justify-center relative z-10 px-6 lg:px-12 min-h-screen py-10">
                <div className="grid lg:grid-cols-2 gap-12 w-full items-center">
                    <motion.div
                        initial={{ opacity: 0, y: 12 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="hidden lg:block rounded-3xl bg-white/85 backdrop-blur-md border border-zinc-200/80 shadow-[0_8px_32px_rgba(0,0,0,0.06)] overflow-hidden"
                    >
                        <div className="p-8 space-y-6">
                            <div className="space-y-4">
                                <span className="inline-block px-3 py-1 bg-zinc-900 text-white rounded-full text-xs font-medium">{badge}</span>
                                <h1 className="text-4xl xl:text-[44px] leading-[1.1] font-bold tracking-tight">{title}</h1>
                                <p className="text-base text-zinc-600 leading-relaxed max-w-lg">{subtitle}</p>
                            </div>

                            <ul className="grid grid-cols-2 gap-3">
                                {points.map((p) => (
                                    <li key={p.title} className="p-4 rounded-xl bg-white border border-zinc-200/80">
                                        <span className="w-8 h-8 mb-3 rounded-lg bg-zinc-50 border border-zinc-100 flex items-center justify-center">
                                            <p.icon className="w-4 h-4 text-indigo-600" />
                                        </span>
                                        <h3 className="text-sm font-semibold text-zinc-900">{p.title}</h3>
                                        <p className="mt-1 text-xs text-zinc-500 leading-relaxed">{p.desc}</p>
                                    </li>
                                ))}
                            </ul>
                        </div>

                        <div className="px-8 py-3 bg-zinc-50/80 border-t border-zinc-100 text-xs">
                            <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-full shrink-0 ${sweep.probe ? "bg-indigo-500 animate-pulse" : "bg-emerald-500"}`} />
                                <span className="font-semibold text-zinc-900">Live sweep #{sweep.sweep}</span>
                                <span className="text-zinc-500 tabular-nums">
                                    {done}/{NODES.length} systems · {sweep.healed} healed · p95 {p95(sweep.timings)} ms
                                </span>
                            </div>
                            <div className="mt-1.5 h-4 flex items-center gap-2 font-mono text-[11px] text-zinc-600">
                                {latest && (
                                    <span className="flex items-center gap-2 min-w-0">
                                        {LOG_MARK[latest.status]}
                                        <span className="truncate">{latest.text}</span>
                                        {latest.ms !== undefined && <span className="text-zinc-400 shrink-0">{latest.ms} ms</span>}
                                    </span>
                                )}
                            </div>
                        </div>

                        <div className="px-8 py-3 border-t border-zinc-100 flex flex-wrap gap-x-5 gap-y-1">
                            {trust.map((t) => (
                                <span key={t} className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-500">
                                    <Check className="w-3 h-3 text-emerald-600" /> {t}
                                </span>
                            ))}
                        </div>
                    </motion.div>

                    <div
                        ref={formRef}
                        onInput={() => setTyping(true)}
                        onBlur={(e) => {
                            if (!formRef.current?.contains(e.relatedTarget as Node | null)) setTyping(false);
                        }}
                        className="flex justify-center lg:justify-end"
                    >
                        {children}
                    </div>
                </div>
            </div>

            <div className="absolute bottom-6 left-6 hidden lg:block text-[10px] uppercase tracking-wider text-zinc-400 font-medium z-10">
                © {new Date().getFullYear()} TraceIQ
            </div>
        </div>
    );
}
