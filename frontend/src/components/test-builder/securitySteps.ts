// Form fields for the security assertion steps. Mirrors the /api/step-types
// catalog entries in backend/app/api/agent_reference.py.

export interface SecurityField {
    key: string;
    label: string;
    kind?: 'text' | 'number' | 'bool';
    placeholder?: string;
}

export const SECURITY_STEP_TYPES = ['expect-header', 'expect-cookie-flags', 'expect-blocked-after', 'expect-session-dead'] as const;

export const isSecurityStep = (t: string): boolean => (SECURITY_STEP_TYPES as readonly string[]).includes(t);

export const SECURITY_STEP_FIELDS: Record<string, SecurityField[]> = {
    'expect-header': [
        { key: 'name', label: 'Header name', placeholder: 'Strict-Transport-Security' },
        { key: 'mode', label: 'Mode (present | absent | equals | regex)', placeholder: 'present' },
        { key: 'value', label: 'Expected value / pattern' },
        { key: 'url', label: 'URL (default: current page)' },
    ],
    'expect-cookie-flags': [
        { key: 'name', label: 'Cookie name', placeholder: 'sessionid' },
        { key: 'sameSite', label: 'SameSite (Strict | Lax | None)' },
        { key: 'secure', label: 'Require Secure', kind: 'bool' },
        { key: 'httpOnly', label: 'Require HttpOnly', kind: 'bool' },
        { key: 'url', label: 'URL to load first (optional)' },
    ],
    'expect-blocked-after': [
        { key: 'value', label: 'Request URL', placeholder: '/api/auth/login' },
        { key: 'method', label: 'Method', placeholder: 'POST' },
        { key: 'attempts', label: 'Blocked by attempt N', kind: 'number', placeholder: '10' },
        { key: 'statuses', label: 'Blocking statuses', placeholder: '429,423,403' },
        { key: 'authorized', label: 'I am authorized to send repeated requests to this target', kind: 'bool' },
    ],
    'expect-session-dead': [
        { key: 'value', label: 'URL to replay after logout', placeholder: '/api/me' },
        { key: 'logout_url', label: 'Logout URL' },
        { key: 'logout_selector', label: 'or Logout button selector' },
        { key: 'statuses', label: 'Dead statuses', placeholder: '401,403,440' },
    ],
};

export const SECURITY_STEP_LABELS: Record<string, string> = {
    'expect-header': 'Expect Header',
    'expect-cookie-flags': 'Expect Cookie Flags',
    'expect-blocked-after': 'Expect Rate Limit',
    'expect-session-dead': 'Expect Session Dead',
};
