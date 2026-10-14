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

const PILL: Record<NodeStatus, string> = {
    idle: "bg-white border-zinc-200 text-zinc-500",
    checking: "bg-white border-indigo-300 text-indigo-700 ring-4 ring-indigo-100",
    pass: "bg-white border-emerald-300 text-emerald-700",
    fail: "bg-red-50 border-red-300 text-red-700 ring-4 ring-red-100",
    healing: "bg-amber-50 border-amber-300 text-amber-800 ring-4 ring-amber-100",
    healed: "bg-white border-emerald-300 text-emerald-700",
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
            <div className={`flex items-center gap-2 pl-2 pr-3 py-1.5 rounded-full border text-xs font-medium shadow-sm transition-all duration-300 whitespace-nowrap ${PILL[status]}`}>
                <span className="w-6 h-6 rounded-full bg-zinc-50 border border-zinc-100 flex items-center justify-center text-zinc-600">
                    <Icon className="w-3.5 h-3.5" />
                </span>
                <span className="text-zinc-800">{node.label}</span>
                <StatusMark status={status} />
                {ms !== undefined && (status === "pass" || status === "healed") && (
                    <span className="font-mono text-[10px] text-zinc-400">{ms} ms</span>
                )}
            </div>
            {(status === "healing" || status === "healed" || status === "fail") && (
                // Centring lives on a plain wrapper: framer-motion's transform
                // would replace a translate-x class on the animated element.
                <div className="absolute left-1/2 -translate-x-1/2 top-full mt-1.5">
                <motion.div
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`px-2 py-0.5 rounded-full text-[10px] font-semibold whitespace-nowrap border ${
                        status === "fail" ? "bg-white border-red-200 text-red-600"
                            : status === "healing" ? "bg-white border-amber-200 text-amber-700"
                                : "bg-white border-emerald-200 text-emerald-700"}`}
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
                            stroke={on ? "#6366F1" : done ? "#10B981" : "#D4D4D8"}
                            strokeOpacity={on ? 0.9 : done ? 0.55 : 0.7}
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
                    className="absolute w-3 h-3 -ml-1.5 -mt-1.5 rounded-full bg-indigo-500 shadow-[0_0_0_6px_rgba(99,102,241,0.18)] pointer-events-none z-0"
                    initial={{ left: `${from.x}%`, top: `${from.y}%`, scale: probe.from ? 1 : 0 }}
                    animate={{ left: `${to.x}%`, top: `${to.y}%`, scale: 1 }}
                    transition={{ duration: (STEP_MS * 0.6) / 1000, ease: "easeInOut" }}
                />
            )}
        </div>
    );
}
