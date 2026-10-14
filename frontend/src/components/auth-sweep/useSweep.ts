import { useEffect, useReducer } from "react";
import { useReducedMotion } from "framer-motion";
import { STEPS, STEP_MS, FAIL_HOLD_MS, REST_MS, edgeKey } from "./sweepScript";

export type NodeStatus = "idle" | "checking" | "pass" | "fail" | "healing" | "healed";
export type LogStatus = "pass" | "fail" | "info" | "heal";

export interface LogLine { id: number; text: string; ms?: number; status: LogStatus }

export interface SweepState {
    sweep: number;
    status: Record<string, NodeStatus>;
    nodeMs: Record<string, number>;
    traversed: string[];
    /** Where the probe is travelling; `seq` restarts its animation. */
    probe: { from?: string; to: string; seq: number } | null;
    logs: LogLine[];
    checks: number;
    passed: number;
    healed: number;
    timings: number[];
}

type Action =
    | { type: "start"; i: number }
    | { type: "pass"; i: number }
    | { type: "fail"; i: number }
    | { type: "analyse"; i: number }
    | { type: "heal"; i: number }
    | { type: "rest" }
    | { type: "reset" };

const MAX_LOGS = 7;

const initial: SweepState = {
    sweep: 1, status: {}, nodeMs: {}, traversed: [], probe: null,
    logs: [], checks: 0, passed: 0, healed: 0, timings: [],
};

let logSeq = 0;
const pushLog = (logs: LogLine[], line: Omit<LogLine, "id">) =>
    [{ ...line, id: ++logSeq }, ...logs].slice(0, MAX_LOGS);

function reducer(s: SweepState, a: Action): SweepState {
    if (a.type === "reset") {
        return { ...s, sweep: s.sweep + 1, status: {}, nodeMs: {}, traversed: [], probe: null };
    }
    if (a.type === "rest") return { ...s, probe: null };

    const step = STEPS[a.i];
    const edge = step.from ? [edgeKey(step.from, step.node)] : [];
    switch (a.type) {
        case "start":
            return {
                ...s,
                status: { ...s.status, [step.node]: "checking" },
                probe: { from: step.from, to: step.node, seq: (s.probe?.seq ?? 0) + 1 },
            };
        case "pass":
            return {
                ...s,
                status: { ...s.status, [step.node]: "pass" },
                nodeMs: { ...s.nodeMs, [step.node]: step.ms },
                traversed: [...s.traversed, ...edge],
                logs: pushLog(s.logs, { text: step.log, ms: step.ms, status: "pass" }),
                checks: s.checks + 1, passed: s.passed + 1,
                timings: [...s.timings, step.ms].slice(-60),
            };
        case "fail":
            return {
                ...s,
                status: { ...s.status, [step.node]: "fail" },
                traversed: [...s.traversed, ...edge],
                logs: pushLog(s.logs, { text: step.fails!.log, status: "fail" }),
                checks: s.checks + 1,
            };
        case "analyse":
            return {
                ...s,
                status: { ...s.status, [step.node]: "healing" },
                logs: pushLog(s.logs, { text: step.fails!.analysis, status: "info" }),
            };
        case "heal":
            return {
                ...s,
                status: { ...s.status, [step.node]: "healed" },
                nodeMs: { ...s.nodeMs, [step.node]: step.ms },
                logs: pushLog(s.logs, { text: step.fails!.healedLog, ms: step.ms, status: "heal" }),
                healed: s.healed + 1,
                timings: [...s.timings, step.ms].slice(-60),
            };
    }
}

/** One cycle as (wait-before, action) pairs; `reset` closes the loop. */
function cycle(): [number, Action][] {
    const out: [number, Action][] = [];
    STEPS.forEach((step, i) => {
        out.push([out.length ? STEP_MS * 0.25 : 400, { type: "start", i }]);
        if (step.fails) {
            out.push([STEP_MS * 0.75, { type: "fail", i }]);
            out.push([FAIL_HOLD_MS, { type: "analyse", i }]);
            out.push([FAIL_HOLD_MS, { type: "heal", i }]);
        } else {
            out.push([STEP_MS * 0.75, { type: "pass", i }]);
        }
    });
    out.push([STEP_MS * 0.5, { type: "rest" }]);
    out.push([REST_MS, { type: "reset" }]);
    return out;
}

const finished = () => cycle()
    .filter(([, a]) => a.type !== "reset")
    .reduce((s, [, a]) => reducer(s, a), initial);

export function p95(timings: number[]): number {
    if (!timings.length) return 0;
    const sorted = [...timings].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)];
}

/**
 * Drives the scripted sweep. With reduced motion it returns the finished
 * state of one cycle and never animates; while the tab is hidden it pauses.
 */
export function useSweep(): SweepState {
    const reduce = useReducedMotion();
    const [state, dispatch] = useReducer(reducer, initial);

    useEffect(() => {
        if (reduce) return;
        const frames = cycle();
        let idx = 0;
        let timer: number | undefined;
        let stopped = false;

        const schedule = () => {
            timer = window.setTimeout(fire, frames[idx][0]);
        };
        const fire = () => {
            if (stopped) return;
            if (document.hidden) {
                document.addEventListener("visibilitychange", resume, { once: true });
                return;
            }
            dispatch(frames[idx][1]);
            idx = (idx + 1) % frames.length;
            schedule();
        };
        const resume = () => (document.hidden
            ? document.addEventListener("visibilitychange", resume, { once: true })
            : schedule());

        schedule();
        return () => {
            stopped = true;
            window.clearTimeout(timer);
            document.removeEventListener("visibilitychange", resume);
        };
    }, [reduce]);

    return reduce ? staticState : state;
}

const staticState = finished();
