/**
 * Deprecation notice for the legacy CONTINUOUS engine (src/server.ts, WP15b H4).
 *
 * The backend has dispatched every run — CONTINUOUS included — to the
 * distributed workers for several releases; nothing in a shipped deployment
 * (community/prod compose, AIO, Helm) starts server.ts. It stays one more
 * release for operators who run it by hand, and says so loudly, because it
 * silently skips controls the workers enforce.
 */

export const LEGACY_ENGINE_REMOVAL = 'the release after 2026-10';

export const LEGACY_ENGINE_GAPS = [
    'the workspace AI opt-out (settings.ai_allowed) — LLM heal/analysis calls are not gated',
    'the project egress allowlist (settings.egress_policy) — the browser can reach any host',
    'run cancel (WP15f) — a cancelled run keeps executing to the end',
];

export function legacyEngineBanner(): string {
    const bar = '!'.repeat(78);
    return [
        bar,
        '!! DEPRECATED: the legacy TraceIQ execution engine (server.ts) is running.',
        `!! It will be REMOVED in ${LEGACY_ENGINE_REMOVAL}. Run execution-worker instead`,
        '!! (node dist/worker.js); the backend already dispatches every run there.',
        '!! This engine does NOT honour:',
        ...LEGACY_ENGINE_GAPS.map((g) => `!!   - ${g}`),
        '!! See docs/OPERATIONS.md -> "Legacy execution engine".',
        bar,
    ].join('\n');
}
