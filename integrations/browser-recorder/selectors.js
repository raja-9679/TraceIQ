// Selector and step helpers for the recorder's content script.
//
// Loaded before content.js (manifest order) and shares its isolated world, so
// everything hangs off one global. No chrome.* calls here: these are pure DOM
// functions, which is what lets test/recorder.e2e.mjs exercise them in a page.
//
// Selector order follows the worker's heal ranking (test id > id > name /
// aria-label / placeholder > text > CSS path): the more a selector says about
// what the element IS, the longer it survives a redesign. Every candidate is
// kept only if it matches exactly one element, because the worker acts on the
// first match and a selector that matches three things is a wrong test waiting
// to happen.

(function () {
    const TEST_ID_ATTRS = ['data-testid', 'data-test-id', 'data-test', 'data-cy', 'data-qa'];
    const CLICKABLE = 'button, a[href], [role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="option"], [role="checkbox"], [role="radio"], [role="switch"], summary, label, input, select, textarea';
    const TEXT_TAGS = new Set(['button', 'a', 'summary', 'label', 'h1', 'h2', 'h3', 'h4', 'li', 'td', 'th', 'span', 'p', 'div']);
    const MAX_TEXT = 60;

    function newId() {
        // crypto.randomUUID needs a secure context; a recorded site on plain
        // http (a LAN staging box) is not one.
        if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
        const b = crypto.getRandomValues(new Uint8Array(16));
        b[6] = (b[6] & 0x0f) | 0x40;
        b[8] = (b[8] & 0x3f) | 0x80;
        const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
        return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
    }

    function quote(value) {
        return `"${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
    }

    function textOf(el) {
        return (el.textContent || '').replace(/\s+/g, ' ').trim();
    }

    // Ids minted by frameworks (":r1:", "radix-42", "ember123", a uuid) change
    // on every render or build, so a selector built on one breaks immediately.
    function looksGenerated(id) {
        return /^:|^\d|\d{3,}|[0-9a-f]{8}-[0-9a-f]{4}|^(radix|ember|react|mui|headlessui|__)/i.test(id);
    }

    function isUnique(root, css, el) {
        try {
            const found = root.querySelectorAll(css);
            return found.length === 1 && found[0] === el;
        } catch {
            return false; // a value produced an invalid selector — skip it
        }
    }

    // How many elements Playwright's `text="..."` would find under `scope`: it
    // takes the SMALLEST element with exactly that text, so a <button> whose
    // text sits in a <span> is found through the span. Counting any other way
    // (by tag, say) produces selectors the worker cannot resolve.
    function exactTextCount(scope, text) {
        let n = 0;
        for (const candidate of scope.querySelectorAll('*')) {
            if (candidate.childElementCount > 40 || textOf(candidate) !== text) continue;
            if (Array.from(candidate.children).some((c) => textOf(c) === text)) continue;
            n += 1;
            if (n > 1) break;
        }
        return n;
    }

    // "#form > div:nth-of-type(2) > button": anchored on the nearest ancestor
    // with a usable id or test id, so a change elsewhere on the page does not
    // move it. Last resort — it describes position, not meaning.
    function cssPath(el, root) {
        const parts = [];
        let node = el;
        while (node && node.nodeType === 1 && node !== root.documentElement) {
            const tag = node.tagName.toLowerCase();
            let anchor = '';
            for (const attr of TEST_ID_ATTRS) {
                const v = node.getAttribute(attr);
                if (v) { anchor = `[${attr}=${quote(v)}]`; break; }
            }
            if (!anchor && node.id && !looksGenerated(node.id)) anchor = `#${CSS.escape(node.id)}`;
            if (anchor && node !== el) {
                parts.unshift(anchor);
                break;
            }
            const parent = node.parentElement;
            let part = tag;
            if (parent) {
                const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
                if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
            }
            parts.unshift(part);
            node = parent;
        }
        return parts.join(' > ');
    }

    // Nearest ancestor that has a test id or a hand-written id of its own.
    function stableAncestor(el) {
        const root = el.ownerDocument;
        for (let node = el.parentElement; node && node !== root.documentElement; node = node.parentElement) {
            for (const attr of TEST_ID_ATTRS) {
                const v = node.getAttribute(attr);
                if (v && isUnique(root, `[${attr}=${quote(v)}]`, node)) return { node, css: `[${attr}=${quote(v)}]` };
            }
            if (node.id && !looksGenerated(node.id) && isUnique(root, `#${CSS.escape(node.id)}`, node)) {
                return { node, css: `#${CSS.escape(node.id)}` };
            }
        }
        return null;
    }

    function selectorFor(el) {
        const root = el.ownerDocument;
        const tag = el.tagName.toLowerCase();

        for (const attr of TEST_ID_ATTRS) {
            const v = el.getAttribute(attr);
            if (v && isUnique(root, `[${attr}=${quote(v)}]`, el)) return `[${attr}=${quote(v)}]`;
        }
        if (el.id && !looksGenerated(el.id) && isUnique(root, `#${CSS.escape(el.id)}`, el)) {
            return `#${CSS.escape(el.id)}`;
        }
        for (const attr of ['name', 'aria-label', 'placeholder', 'title', 'alt']) {
            const v = el.getAttribute(attr);
            if (v && v.length <= 80) {
                const css = `${tag}[${attr}=${quote(v)}]`;
                if (isUnique(root, css, el)) return css;
            }
        }
        const text = textOf(el);
        // Text made only of ordinary characters: a no-break space or a quote
        // would need escaping rules the worker and this page must agree on.
        const plainText = text && text.length <= MAX_TEXT && TEXT_TAGS.has(tag) && /^[^"\\\u00a0]+$/.test(text);
        if (plainText && exactTextCount(root, text) === 1) return `text=${quote(text)}`;
        if (tag === 'a') {
            const href = el.getAttribute('href');
            if (href && href.length <= 100 && !href.startsWith('javascript:')) {
                const css = `a[href=${quote(href)}]`;
                if (isUnique(root, css, el)) return css;
            }
        }
        // "Edit" appears on every row, but only once inside #invoice-42: text
        // scoped to the nearest named ancestor still says what the element is.
        if (plainText) {
            const anchor = stableAncestor(el);
            if (anchor && exactTextCount(anchor.node, text) === 1) return `${anchor.css} >> text=${quote(text)}`;
        }
        const path = cssPath(el, root);
        if (path && isUnique(root, path, el)) return path;
        return path || tag;
    }

    // What a person would call the element: shown in the step list and used
    // by the worker's semantic fallback when the selector stops matching.
    function intentFor(el) {
        const label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt');
        if (label) return label.slice(0, 80);
        if (el.labels && el.labels.length) {
            const t = textOf(el.labels[0]);
            if (t) return t.slice(0, 80);
        }
        const text = textOf(el);
        if (text) return text.slice(0, 80);
        return el.getAttribute('name') || el.tagName.toLowerCase();
    }

    // A click usually lands on the icon or the text inside a control; the
    // step should name the control.
    function clickTarget(el) {
        return el.closest(CLICKABLE) || el;
    }

    function isTextField(el) {
        if (el instanceof HTMLTextAreaElement) return true;
        if (!(el instanceof HTMLInputElement)) return false;
        return !['checkbox', 'radio', 'file', 'button', 'submit', 'reset', 'image', 'range', 'color'].includes(el.type);
    }

    // Passwords never leave the page: the step carries a {{secret.NAME}}
    // template, the same form the editor and worker already use, and the
    // tester stores the real value under Environments.
    function secretNameFor(el) {
        const raw = el.getAttribute('name') || el.id || 'password';
        const name = raw.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase();
        return name && /^[A-Z]/.test(name) ? name.slice(0, 40) : 'PASSWORD';
    }

    // A field is secret for the whole recording once it has been a password
    // field: a "show password" button turns it into type=text, and what is
    // typed after that is still the password. content.js adds to this set when
    // it sees a type change away from "password".
    const everPassword = new WeakSet();
    const SECRET_AUTOCOMPLETE = /\b(current-password|new-password|one-time-code)\b/i;

    function isSecretField(el) {
        if (!(el instanceof HTMLInputElement)) return false;
        if (el.type === 'password' || SECRET_AUTOCOMPLETE.test(el.getAttribute('autocomplete') || '')) everPassword.add(el);
        return everPassword.has(el);
    }

    function fillValue(el) {
        return isSecretField(el) ? `{{secret.${secretNameFor(el)}}}` : el.value;
    }

    // expect-url goes to Playwright's waitForURL, which takes a glob. Host and
    // scheme are left out so the test survives a move between environments.
    function urlPattern(loc) {
        return `**${loc.pathname}${loc.search ? '**' : ''}`;
    }

    globalThis.__traceiqRecorder = {
        newId, selectorFor, intentFor, clickTarget, isTextField, fillValue, isSecretField, everPassword, urlPattern, textOf,
    };
})();
