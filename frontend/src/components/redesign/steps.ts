// Plain-language wording for test steps, shared by the suite page's list
// (CaseSplit) and card views.

export type Step = { type?: string; selector?: string | null; value?: string | null; intent?: string | null } | string;

const isPlaceholder = (v: string) => /^\s*\{\{[^}]+\}\}\s*$/.test(v);

// Typed values can be credentials pasted into a step; show them only when
// they are a {{variable}} reference.
const q = (v: string) => `“${v}”`;

// Plain words for the common step types; anything else falls back to
// "<type> on <selector> with <value>". The label is a short verb for the chip.
export function describeStep(step: Step): { verb: string; text: string; detail: string } {
    if (typeof step === 'string') return { verb: 'Step', text: step, detail: '' };
    const type = (step.type || 'step').toLowerCase();
    const sel = step.selector || '';
    const val = step.value ?? '';
    const typed = /fill|type|press|secret/.test(type);
    const v = val && (!typed || isPlaceholder(val)) ? val : val ? '(typed value)' : '';
    const on = sel ? ` ${sel}` : '';
    const map: Record<string, [string, string]> = {
        goto: ['Go', `Go to ${v || 'the page'}`],
        click: ['Click', `Click${on}`],
        hover: ['Hover', `Hover over${on}`],
        fill: ['Type', `Type ${v ? (v === '(typed value)' ? 'a value' : q(v)) : 'a value'} into${on}`],
        check: ['Tick', `Tick${on}`],
        'press-key': ['Press', `Press ${v}${sel ? ` in ${sel}` : ''}`],
        'select-option': ['Choose', `Choose ${q(v)} in${on}`],
        'wait-timeout': ['Wait', `Wait ${v} ms`],
        'expect-visible': ['Check', `Check${on} is visible`],
        'expect-hidden': ['Check', `Check${on} is hidden`],
        'expect-text': ['Check', `Check${on} shows ${q(v)}`],
        'expect-not-text': ['Check', `Check${on} does not show ${q(v)}`],
        'expect-title': ['Check', `Check the page title is ${q(v)}`],
        'expect-url': ['Check', `Check the URL contains ${q(v)}`],
        expect: ['Check', `Check${on}${v ? ` is ${q(v)}` : ''}`],
        assert: ['Check', `Check${on}${v ? ` is ${q(v)}` : ''}`],
        screenshot: ['Capture', `Take a screenshot${v ? ` ${q(v)}` : ''}`],
        'http-request': ['Request', `Send ${v || 'an HTTP request'}`],
    };
    const known = map[type];
    if (step.intent) return { verb: known?.[0] ?? 'Step', text: step.intent, detail: [sel, v].filter(Boolean).join(' · ') };
    if (known) return { verb: known[0], text: known[1], detail: '' };
    const verb = type.replace(/[-_]/g, ' ');
    return { verb: verb.split(' ')[0], text: [verb, sel && `on ${sel}`, v && `with ${v}`].filter(Boolean).join(' '), detail: '' };
}

