// Client-side ids for list items (steps, assertions) before they are saved.
//
// crypto.randomUUID exists only in a secure context (https or localhost). A
// self-hosted instance opened over plain http on a LAN name or IP is not one,
// and calling it there threw "crypto.randomUUID is not a function" the moment
// you added a step in the test builder. getRandomValues works everywhere.
export function newId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
    const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
