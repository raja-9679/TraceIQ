// http-request `params.body` is "object or string" (backend
// app/api/agent_reference.py): agents, MCP and generated cases store JSON
// bodies as objects, and the worker hands the value to Playwright unchanged,
// so an object goes out as JSON and a string goes out raw. The editor is a
// textarea, so it converts at the edges and never changes which kind it is.

const isJsonValue = (v: unknown): v is object => v !== null && typeof v === 'object';

/** Text for the body editor: objects as pretty JSON, strings as written. */
export function bodyToText(body: unknown): string {
    if (body === undefined || body === null) return '';
    if (isJsonValue(body)) return JSON.stringify(body, null, 2);
    return String(body);
}

/**
 * The body to store after the user edits the text. An object body stays an
 * object: valid JSON replaces it, empty text removes it, and anything else
 * leaves it untouched with an error to show — text never overwrites it.
 * A string (or absent) body stays a string, even if the text looks like JSON.
 */
export function textToBody(text: string, previous: unknown): { body: unknown; error?: string } {
    if (!isJsonValue(previous)) return { body: text };
    if (text.trim() === '') return { body: undefined };
    try {
        return { body: JSON.parse(text) };
    } catch {
        return { body: previous, error: 'Not valid JSON yet — the saved body stays as it was until it is.' };
    }
}
