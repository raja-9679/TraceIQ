import { motion } from "framer-motion";
import {
    Check, Cloud, Database, Globe, HardDrive, KeyRound, Loader2, Mail, Network,
    Search, ShoppingCart, Smartphone, Sparkles, X, CreditCard, Zap, ShieldCheck, type LucideIcon,
} from "lucide-react";
import { NODES, EDGES, STEP_MS, edgeKey, type SweepNode } from "./sweepScript";
import type { NodeStatus, SweepState } from "./useSweep";

const ICONS: Record<string, LucideIcon> = {
    web: Globe, mobile: Smartphone, gateway: Network,
    auth: KeyRound, orders: ShoppingCart, payments: CreditCard, search: Search,
    postgres: Database, redis: Zap, storage: HardDrive,
    sso: ShieldCheck, stripe: Cloud, email: Mail,
};

const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));

// Drawn on the navy console panel of the signed-out pages.
const PILL: Record<NodeStatus, string> = {
    idle: "bg-[#0F1A2E] border-slate-700/80 text-slate-500",
    checking: "bg-[#0F1A2E] border-sky-400/70 text-sky-300 ring-4 ring-sky-400/10",
    pass: "bg-[#0F1A2E] border-emerald-500/50 text-emerald-400",
    fail: "bg-[#2A1219] border-rose-500/70 text-rose-400 ring-4 ring-rose-500/10",
    healing: "bg-[#2A2112] border-amber-400/70 text-amber-300 ring-4 ring-amber-400/10",
    healed: "bg-[#0F1A2E] border-emerald-500/50 text-emerald-400",
};

function StatusMark({ status }: { status: NodeStatus }) {
    if (status === "checking") return <Loader2 className="w-3.5 h-3.5 animate-spin" />;
    if (status === "pass" || status === "healed") return <Check className="w-3.5 h-3.5" />;
    if (status === "fail") return <X className="w-3.5 h-3.5" />;
    if (status === "healing") return <Sparkles className="w-3.5 h-3.5" />;
    return null;
}

function Node({ node, status, ms }: { node: SweepNode; status: NodeStatus; ms?: number }) {
    const Icon = ICONS[node.id];
    return (
        <div
            className="absolute z-10 -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${node.x}%`, top: `${node.y}%` }}
        >
            <div className={`flex items-center gap-2 pl-2 pr-3 py-1.5 rounded-md border text-xs font-medium transition-all duration-300 whitespace-nowrap ${PILL[status]}`}>
                <span className="w-5 h-5 rounded bg-slate-800/80 flex items-center justify-center text-slate-400">
                    <Icon className="w-3 h-3" />
                </span>
                <span className="text-slate-200">{node.label}</span>
                <StatusMark status={status} />
                {ms !== undefined && (status === "pass" || status === "healed") && (
                    <span className="font-mono text-[10px] text-slate-500">{ms} ms</span>
                )}
            </div>
            {(status === "healing" || status === "healed" || status === "fail") && (
                // Centring lives on a plain wrapper: framer-motion's transform
                // would replace a translate-x class on the animated element.
                <div className="absolute left-1/2 -translate-x-1/2 top-full mt-1.5">
                <motion.div
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`px-2 py-0.5 rounded text-[10px] font-medium whitespace-nowrap border ${
                        status === "fail" ? "bg-[#0B1220] border-rose-500/40 text-rose-400"
                            : status === "healing" ? "bg-[#0B1220] border-amber-400/40 text-amber-300"
                                : "bg-[#0B1220] border-emerald-500/40 text-emerald-400"}`}
                >
                    {status === "fail" ? "expect-text failed" : status === "healing" ? "AI analysing…" : "healed by AI"}
                </motion.div>
                </div>
            )}
        </div>
    );
}

/** The system map: connections, nodes and the travelling probe. */
export default function SweepMap({ state }: { state: SweepState }) {
    const traversed = new Set(state.traversed);
    const probe = state.probe;
    const active = probe?.from ? edgeKey(probe.from, probe.to) : null;
    const from = probe ? byId[probe.from ?? probe.to] : null;
    const to = probe ? byId[probe.to] : null;

    return (
        <div className="relative w-full h-full">
            <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
                {EDGES.map(([a, b]) => {
                    const k = edgeKey(a, b);
                    const on = k === active;
                    const done = traversed.has(k);
                    return (
                        <line
                            key={k}
                            x1={byId[a].x} y1={byId[a].y} x2={byId[b].x} y2={byId[b].y}
                            stroke={on ? "#38BDF8" : done ? "#10B981" : "#334155"}
                            strokeOpacity={on ? 0.9 : done ? 0.5 : 0.8}
                            strokeWidth={on ? 2 : 1.25}
                            strokeDasharray={done || on ? undefined : "4 4"}
                            vectorEffect="non-scaling-stroke"
                            style={{ transition: "stroke 300ms, stroke-opacity 300ms" }}
                        />
                    );
                })}
            </svg>

            {NODES.map((n) => (
                <Node key={n.id} node={n} status={state.status[n.id] ?? "idle"} ms={state.nodeMs[n.id]} />
            ))}

            {probe && from && to && (
                <motion.div
                    key={probe.seq}
                    className="absolute w-3 h-3 -ml-1.5 -mt-1.5 rounded-full bg-sky-400 shadow-[0_0_0_6px_rgba(56,189,248,0.18),0_0_18px_rgba(56,189,248,0.6)] pointer-events-none z-0"
                    initial={{ left: `${from.x}%`, top: `${from.y}%`, scale: probe.from ? 1 : 0 }}
                    animate={{ left: `${to.x}%`, top: `${to.y}%`, scale: 1 }}
                    transition={{ duration: (STEP_MS * 0.6) / 1000, ease: "easeInOut" }}
                />
            )}
        </div>
    );
}
