// Exclusive environments (WP16c, decision V3) — display helpers.

export interface EnvironmentLock {
    run_id: number | null;
    since: string | null;
    environment?: string | null;
    error?: string;
}

/** The lease timestamps are naive UTC ISO strings from the backend. */
export function asUtc(iso: string | null | undefined): Date | null {
    if (!iso) return null;
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(d.getTime()) ? null : d;
}

export function holderText(lock: EnvironmentLock | null | undefined, now: Date = new Date()): string {
    if (!lock) return 'Free';
    if (lock.error) return 'Lock state unavailable';
    if (lock.run_id == null) return 'Free';
    const since = asUtc(lock.since);
    if (!since) return `In use by run #${lock.run_id}`;
    const mins = Math.max(0, Math.round((now.getTime() - since.getTime()) / 60000));
    const ago = mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`;
    return `In use by run #${lock.run_id} since ${since.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })} (${ago})`;
}

export interface RunEnvironmentLock {
    run_id: number;
    state: 'none' | 'waiting' | 'holding';
    environment_id?: number | null;
    environment?: string | null;
    holder_run_id?: number | null;
    since?: string | null;
}

export function runLockText(l: RunEnvironmentLock | null | undefined): string | null {
    if (!l || l.state === 'none') return null;
    const name = l.environment ? `environment ${l.environment}` : 'its environment';
    if (l.state === 'holding') return `Holding ${name} (exclusive) until this run finishes.`;
    return l.holder_run_id != null
        ? `Waiting for ${name}, held by run #${l.holder_run_id}. It starts automatically when that run finishes.`
        : `Waiting for ${name}. It starts automatically when the environment is free.`;
}
