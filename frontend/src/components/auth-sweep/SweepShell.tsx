import { useRef, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { Check, Info, LayoutGrid, Sparkles, X } from "lucide-react";
import SweepMap from "./SweepMap";
import { NODES } from "./sweepScript";
import { p95, useSweep, type LogStatus } from "./useSweep";

const LOG_MARK: Record<LogStatus, ReactNode> = {
    pass: <Check className="w-3 h-3 text-emerald-600" />,
    heal: <Sparkles className="w-3 h-3 text-emerald-600" />,
    fail: <X className="w-3 h-3 text-red-600" />,
    info: <Info className="w-3 h-3 text-amber-600" />,
};

function Stat({ label, value }: { label: string; value: ReactNode }) {
    return (
        <div className="flex items-baseline gap-1.5">
            <span className="font-semibold text-zinc-900 tabular-nums">{value}</span>
            <span className="text-zinc-500">{label}</span>
        </div>
    );
}

/**
 * Signed-out page frame: the scripted system sweep on the left, the page's
 * form in a white column on the right. Typing in the form dims the sweep
 * (not focus: the first field autofocuses, which would dim it on load).
 * Below `lg` only the form is shown.
 */
export default function SweepShell({ eyebrow, title, subtitle, children }: {
    eyebrow: string;
    title: ReactNode;
    subtitle: string;
    children: ReactNode;
}) {
    const sweep = useSweep();
    const [focused, setFocused] = useState(false);
    const formRef = useRef<HTMLElement>(null);
    const done = Object.values(sweep.status).filter((s) => s === "pass" || s === "healed").length;

    return (
        <div className="min-h-screen w-full flex bg-[#FAFAFA] text-zinc-900 font-inter">
            <section
                aria-hidden
                className={`hidden lg:flex flex-1 min-w-0 flex-col px-10 xl:px-14 py-10 relative transition-opacity duration-500 ${focused ? "opacity-75" : ""}`}
                style={{
                    backgroundImage: "radial-gradient(circle, #E4E4E7 1px, transparent 1px)",
                    backgroundSize: "24px 24px",
                }}
            >
                <div className="max-w-2xl">
                    <div className="flex items-center gap-2 mb-6">
                        <span className="w-8 h-8 rounded-xl bg-zinc-900 flex items-center justify-center">
                            <LayoutGrid className="w-4 h-4 text-white" />
                        </span>
                        <span className="font-bold tracking-tight">TraceIQ</span>
                        <span className="ml-2 px-2.5 py-0.5 rounded-full bg-white border border-zinc-200 text-[11px] font-medium text-zinc-600 flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                            {eyebrow}
                        </span>
                    </div>
                    <h1 className="text-4xl xl:text-[44px] leading-[1.1] font-bold tracking-tight">{title}</h1>
                    <p className="mt-3 text-base text-zinc-600 max-w-xl leading-relaxed">{subtitle}</p>
                </div>

                <div className="flex-1 min-h-[360px] my-8 pl-24 pr-6">
                    <SweepMap state={sweep} />
                </div>

                <div className="bg-white rounded-2xl border border-zinc-200 shadow-sm overflow-hidden">
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-5 py-3 border-b border-zinc-100 text-xs">
                        <div className="flex items-center gap-2 font-semibold text-zinc-900">
                            <span className={`w-2 h-2 rounded-full ${sweep.probe ? "bg-indigo-500 animate-pulse" : "bg-emerald-500"}`} />
                            Sweep #{sweep.sweep}
                            <span className="font-normal text-zinc-500">
                                {done}/{NODES.length} systems verified
                            </span>
                        </div>
                        <Stat label="checks" value={sweep.checks} />
                        <Stat label="passed" value={sweep.passed} />
                        <Stat label="healed" value={sweep.healed} />
                        <Stat label="p95" value={`${p95(sweep.timings)} ms`} />
                    </div>
                    <ul className="px-5 py-3 h-[104px] overflow-hidden font-mono text-[11px] space-y-1">
                        {sweep.logs.slice(0, 4).map((l, i) => (
                            <motion.li
                                key={l.id}
                                initial={{ opacity: 0, y: -6 }}
                                animate={{ opacity: 1 - i * 0.2, y: 0 }}
                                className="flex items-center gap-2 text-zinc-700"
                            >
                                {LOG_MARK[l.status]}
                                <span className="truncate">{l.text}</span>
                                {l.ms !== undefined && <span className="ml-auto pl-3 text-zinc-400 shrink-0">{l.ms} ms</span>}
                            </motion.li>
                        ))}
                    </ul>
                </div>
            </section>

            <main
                ref={formRef}
                onInput={() => setFocused(true)}
                onBlur={(e) => {
                    if (!formRef.current?.contains(e.relatedTarget as Node | null)) setFocused(false);
                }}
                className="w-full lg:w-[460px] xl:w-[520px] shrink-0 flex flex-col items-center justify-center px-6 py-10 bg-white lg:border-l border-zinc-200"
            >
                {children}
            </main>
        </div>
    );
}
