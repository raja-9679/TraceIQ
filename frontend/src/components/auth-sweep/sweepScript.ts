// The scripted "sweep" shown behind the signed-out pages. Nothing here is
// fetched: it is a choreographed story of TraceIQ checking a typical app,
// tier by tier, using the platform's real step-type names so it reads true.

export type NodeKind = "client" | "edge" | "service" | "data" | "external";

export interface SweepNode {
    id: string;
    label: string;
    kind: NodeKind;
    /** Position as a percentage of the map area. */
    x: number;
    y: number;
}

export const NODES: SweepNode[] = [
    { id: "web", label: "Web app", kind: "client", x: 34, y: 9 },
    { id: "mobile", label: "Mobile app", kind: "client", x: 66, y: 9 },

    { id: "gateway", label: "API Gateway", kind: "edge", x: 50, y: 27 },

    { id: "auth", label: "Auth", kind: "service", x: 14, y: 47 },
    { id: "orders", label: "Orders", kind: "service", x: 38, y: 47 },
    { id: "payments", label: "Payments", kind: "service", x: 62, y: 47 },
    { id: "search", label: "Search", kind: "service", x: 86, y: 47 },

    { id: "postgres", label: "Postgres", kind: "data", x: 26, y: 67 },
    { id: "redis", label: "Redis", kind: "data", x: 50, y: 67 },
    { id: "storage", label: "Object storage", kind: "data", x: 74, y: 67 },

    { id: "sso", label: "SSO", kind: "external", x: 14, y: 87 },
    { id: "email", label: "Email", kind: "external", x: 38, y: 87 },
    { id: "stripe", label: "Stripe", kind: "external", x: 62, y: 87 },
];

export const EDGES: [string, string][] = [
    ["web", "gateway"], ["mobile", "gateway"],
    ["gateway", "auth"], ["gateway", "orders"], ["gateway", "payments"], ["gateway", "search"],
    ["auth", "postgres"], ["orders", "postgres"], ["orders", "redis"], ["payments", "redis"],
    ["search", "storage"],
    ["auth", "sso"], ["orders", "email"], ["payments", "stripe"],
];

export const edgeKey = (a: string, b: string) => [a, b].sort().join("~");

export interface SweepStep {
    node: string;
    /** Node the probe travels from; omitted = the probe appears on the node. */
    from?: string;
    /** Log line, without the result suffix. */
    log: string;
    ms: number;
    /** The one planned failure per cycle: fails, gets analysed, heals. */
    fails?: { log: string; analysis: string; healedLog: string };
}

export const STEPS: SweepStep[] = [
    { node: "web", log: "goto /checkout", ms: 412 },
    { node: "gateway", from: "web", log: "check-tls api.shop.example", ms: 44 },
    { node: "auth", from: "gateway", log: "oauth2-token client_credentials", ms: 96 },
    { node: "sso", from: "auth", log: "expect-url contains /callback", ms: 188 },
    { node: "postgres", from: "auth", log: "http-request GET /api/me → 200", ms: 31 },
    { node: "orders", from: "gateway", log: "http-request POST /api/orders → 201", ms: 142 },
    { node: "redis", from: "orders", log: "wait-for-response /api/cart", ms: 12 },
    { node: "email", from: "orders", log: "feed-check order-confirmation", ms: 260 },
    { node: "payments", from: "gateway", log: "graphql mutation chargeCard", ms: 210 },
    { node: "stripe", from: "payments", log: "mock-response stripe /v1/charges", ms: 74 },
    {
        node: "search", from: "gateway", log: "expect-text \"Results for shoes\"", ms: 230,
        fails: {
            log: "expect-text \"Results for shoes\" — selector not found",
            analysis: "AI analysis: .result-item renamed → [data-test=result]",
            healedLog: "heal applied · re-run expect-text",
        },
    },
    { node: "storage", from: "search", log: "expect-visual-match search-grid", ms: 167 },
    { node: "mobile", log: "mobile-tap login_button", ms: 320 },
    { node: "gateway", from: "mobile", log: "check-accessibility /checkout", ms: 158 },
];

/** Milliseconds the probe spends on one step (travel + check). */
export const STEP_MS = 780;
/** Extra time the planned failure holds before analysis, and before healing. */
export const FAIL_HOLD_MS = 1100;
/** Pause with everything green before the next sweep starts. */
export const REST_MS = 2200;
