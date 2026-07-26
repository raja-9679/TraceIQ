/**
 * Minimal W3C WebDriver client for driving Appium over plain HTTP.
 *
 * Phase MOB deliberately avoids the heavy `webdriverio` dependency: Appium 2
 * speaks the standard W3C WebDriver protocol, and the mobile worker needs
 * only a dozen endpoints. Everything goes through global fetch (Node 18+).
 */

export interface LocatorStrategy {
    using: string;
    value: string;
}

/**
 * Parse TraceIQ's mobile selector convention into a WebDriver locator:
 *   "~foo"                      → accessibility id
 *   "//..." or "xpath=..."      → xpath
 *   "id=com.app:id/btn"         → id (resource-id on Android, name on iOS)
 *   "android=new UiSelector()…" → -android uiautomator
 *   "ios=label == 'Done'"       → -ios predicate string
 *   anything else               → accessibility id
 */
export function parseLocator(selector: string): LocatorStrategy {
    if (selector.startsWith('~')) {
        return { using: 'accessibility id', value: selector.slice(1) };
    }
    if (selector.startsWith('xpath=')) {
        return { using: 'xpath', value: selector.slice(6) };
    }
    if (selector.startsWith('//') || selector.startsWith('(')) {
        return { using: 'xpath', value: selector };
    }
    if (selector.startsWith('id=')) {
        return { using: 'id', value: selector.slice(3) };
    }
    if (selector.startsWith('android=')) {
        return { using: '-android uiautomator', value: selector.slice(8) };
    }
    if (selector.startsWith('ios=')) {
        return { using: '-ios predicate string', value: selector.slice(4) };
    }
    return { using: 'accessibility id', value: selector };
}

const ELEMENT_KEY = 'element-6066-11e4-a52e-4f735466cecf';

export class WebDriverError extends Error {
    constructor(message: string, public wireError?: string) {
        super(message);
        this.name = 'WebDriverError';
    }
}

export class WebDriverClient {
    constructor(private baseUrl: string) {
        this.baseUrl = baseUrl.replace(/\/+$/, '');
    }

    private async cmd(method: string, path: string, body?: any): Promise<any> {
        const res = await fetch(`${this.baseUrl}${path}`, {
            method,
            headers: { 'Content-Type': 'application/json; charset=utf-8' },
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
        let json: any = null;
        try {
            json = await res.json();
        } catch {
            // Non-JSON responses surface as protocol errors below
        }
        if (!res.ok || (json && json.value && json.value.error)) {
            const wire = json?.value?.error;
            const msg = json?.value?.message || `HTTP ${res.status} on ${method} ${path}`;
            throw new WebDriverError(msg, wire);
        }
        return json?.value;
    }

    async status(): Promise<any> {
        return this.cmd('GET', '/status');
    }

    async createSession(capabilities: Record<string, any>): Promise<string> {
        const value = await this.cmd('POST', '/session', {
            capabilities: { alwaysMatch: capabilities, firstMatch: [{}] },
        });
        const sessionId = value?.sessionId;
        if (!sessionId) {
            throw new WebDriverError('Appium did not return a sessionId');
        }
        return sessionId;
    }

    async deleteSession(sessionId: string): Promise<void> {
        await this.cmd('DELETE', `/session/${sessionId}`);
    }

    async findElement(sessionId: string, selector: string): Promise<string> {
        const locator = parseLocator(selector);
        const value = await this.cmd('POST', `/session/${sessionId}/element`, locator);
        const el = value?.[ELEMENT_KEY] || value?.ELEMENT;
        if (!el) {
            throw new WebDriverError(`Element not found: ${selector}`, 'no such element');
        }
        return el;
    }

    /** All matching element ids (no error when none) — used to count matches. */
    async findElements(sessionId: string, selector: string): Promise<string[]> {
        const locator = parseLocator(selector);
        const value = await this.cmd('POST', `/session/${sessionId}/elements`, locator);
        if (!Array.isArray(value)) return [];
        return value
            .map((v: any) => v?.[ELEMENT_KEY] || v?.ELEMENT)
            .filter((el: any): el is string => typeof el === 'string');
    }

    /** Poll findElement until it resolves or timeoutMs elapses. */
    async waitForElement(sessionId: string, selector: string, timeoutMs = 10000): Promise<string> {
        const deadline = Date.now() + timeoutMs;
        let lastError: any;
        while (Date.now() < deadline) {
            try {
                return await this.findElement(sessionId, selector);
            } catch (err) {
                lastError = err;
                await new Promise((r) => setTimeout(r, 500));
            }
        }
        throw lastError || new WebDriverError(`Timed out waiting for: ${selector}`);
    }

    async click(sessionId: string, elementId: string): Promise<void> {
        await this.cmd('POST', `/session/${sessionId}/element/${elementId}/click`, {});
    }

    async sendKeys(sessionId: string, elementId: string, text: string): Promise<void> {
        await this.cmd('POST', `/session/${sessionId}/element/${elementId}/value`, { text });
    }

    async clear(sessionId: string, elementId: string): Promise<void> {
        await this.cmd('POST', `/session/${sessionId}/element/${elementId}/clear`, {});
    }

    async getText(sessionId: string, elementId: string): Promise<string> {
        return this.cmd('GET', `/session/${sessionId}/element/${elementId}/text`);
    }

    async isDisplayed(sessionId: string, elementId: string): Promise<boolean> {
        return this.cmd('GET', `/session/${sessionId}/element/${elementId}/displayed`);
    }

    async getElementRect(sessionId: string, elementId: string): Promise<{ x: number; y: number; width: number; height: number }> {
        return this.cmd('GET', `/session/${sessionId}/element/${elementId}/rect`);
    }

    async getWindowRect(sessionId: string): Promise<{ x: number; y: number; width: number; height: number }> {
        return this.cmd('GET', `/session/${sessionId}/window/rect`);
    }

    /** W3C pointer-action sequence (used for long-press and swipe). */
    async performActions(sessionId: string, actions: any[]): Promise<void> {
        await this.cmd('POST', `/session/${sessionId}/actions`, { actions });
        await this.cmd('DELETE', `/session/${sessionId}/actions`).catch(() => undefined);
    }

    /** Base64 PNG of the current screen. */
    async takeScreenshot(sessionId: string): Promise<string> {
        return this.cmd('GET', `/session/${sessionId}/screenshot`);
    }

    /** Native page source (XML) — the mobile analogue of a DOM snapshot. */
    async getPageSource(sessionId: string): Promise<string> {
        return this.cmd('GET', `/session/${sessionId}/source`);
    }

    /** Appium `mobile:` extension scripts (activateApp, terminateApp, pressKey, …). */
    async executeScript(sessionId: string, script: string, args: any[] = []): Promise<any> {
        return this.cmd('POST', `/session/${sessionId}/execute/sync`, { script, args });
    }
}
