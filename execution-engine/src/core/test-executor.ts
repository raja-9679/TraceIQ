import { Page, FrameLocator, Locator } from 'playwright';
import { DOMParser } from '@xmldom/xmldom';
import * as xpath from 'xpath';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';
import * as path from 'path';

export class TestExecutor {
    public static async executeStep(
        page: Page,
        context: Page | FrameLocator,
        step: any,
        globalSettings: any = {},
        testCaseContext?: any
    ): Promise<any> {
        console.log(`  Step: ${step.type} ${step.selector || ''} ${step.value || ''}`);

        const moveMouseTo = async (locator: Locator) => {
            try {
                await locator.hover();
            } catch (e) {
                // ignore
            }
        };

        const getLocator = (selector: string) => {
            return context.locator(selector).first();
        };

        switch (step.type) {
            case 'goto': {
                let url = step.value || step.selector || 'about:blank';
                if (globalSettings?.params && Object.keys(globalSettings.params).length > 0) {
                    try {
                        const urlObj = new URL(url);
                        for (const [key, value] of Object.entries(globalSettings.params)) {
                            urlObj.searchParams.append(key, String(value));
                        }
                        url = urlObj.toString();
                        console.log(`  Modified URL with params: ${url}`);
                    } catch (e) {
                        console.warn(`  Could not append params to URL ${url}: ${e}`);
                    }
                }

                let attempts = 0;
                const maxAttempts = 3;
                const waitUntil = (step.params?.wait_until as 'load' | 'domcontentloaded' | 'networkidle' | 'commit') || 'domcontentloaded';

                while (attempts < maxAttempts) {
                    try {
                        await page.goto(url, { waitUntil, timeout: 30000 });
                        break;
                    } catch (e) {
                        attempts++;
                        console.warn(`  Goto attempt ${attempts} failed: ${e}`);
                        if (attempts === maxAttempts) throw e;
                        await new Promise(r => setTimeout(r, 1000));
                    }
                }
                break;
            }

            case 'http-request': {
                const method = step.params?.method || 'GET';
                const reqUrl = step.value || step.selector;
                const stepHeaders = step.params?.headers || {};
                const stepParams = step.params?.params || {};
                const body = step.params?.body;

                const mergedHeaders = { ...globalSettings.headers, ...stepHeaders };
                const mergedParams = { ...globalSettings.params, ...stepParams };

                console.log(`  [API] ${method} ${reqUrl} (Headers: ${Object.keys(mergedHeaders).length}, Params: ${Object.keys(mergedParams).length})`);

                let apiResponse;
                let actualRequestHeaders = mergedHeaders;
                let actualRequestUrl = reqUrl;
                const requestHandler = async (request: any) => {
                    try {
                        const requestUrl = request.url();
                        if ((requestUrl === reqUrl || requestUrl.split('?')[0] === reqUrl.split('?')[0]) &&
                            request.method() === method) {
                            actualRequestHeaders = await request.allHeaders();
                            actualRequestUrl = requestUrl;
                            console.log(`    [API] Captured actual request URL: ${actualRequestUrl}`);
                        }
                    } catch (e) { }
                };

                page.context().on('request', requestHandler);
                try {
                    apiResponse = await page.request.fetch(reqUrl, {
                        method,
                        headers: mergedHeaders,
                        params: mergedParams,
                        data: body,
                        timeout: 30000
                    });
                } finally {
                    page.context().off('request', requestHandler);
                }

                const status = apiResponse.status();
                const apiHeaders = apiResponse.headers();
                const respBody = await apiResponse.text();
                let jsonBody;
                try { jsonBody = JSON.parse(respBody); } catch (e) { }

                const resultObject = {
                    type: 'http-request',
                    status,
                    headers: apiHeaders,
                    body: respBody,
                    request: {
                        url: actualRequestUrl,
                        method,
                        headers: actualRequestHeaders,
                        params: mergedParams,
                        body
                    }
                };

                if (step.params?.assertions) {
                    for (const assertion of step.params.assertions) {
                        try {
                            if (assertion.type === 'status') {
                                if (status !== parseInt(assertion.value)) {
                                    throw new Error(`Expected status ${assertion.value} but got ${status}`);
                                }
                            } else if (assertion.type === 'json-path') {
                                if (!jsonBody) throw new Error("Response is not JSON, cannot perform json-path assertion");
                                const pathParts = assertion.path.split('.');
                                let current = jsonBody;
                                for (const part of pathParts) {
                                    if (current === undefined || current === null) break;
                                    current = current[part];
                                }
                                if (assertion.operator === 'equals') {
                                    if (String(current) !== String(assertion.value)) {
                                        throw new Error(`Expected ${assertion.path} to equal ${assertion.value} but got ${current}`);
                                    }
                                } else if (assertion.operator === 'contains') {
                                    if (!String(current).includes(String(assertion.value))) {
                                        throw new Error(`Expected ${assertion.path} to contain ${assertion.value} but got ${current}`);
                                    }
                                }
                            } else if (assertion.type === 'json-schema') {
                                if (!jsonBody) throw new Error("Response is not JSON, cannot perform json-schema assertion");
                                const ajv = new Ajv({ allErrors: true });
                                addFormats(ajv);
                                const schema = JSON.parse(assertion.value || '{}');
                                const validate = ajv.compile(schema);
                                if (!validate(jsonBody)) {
                                    const errors = validate.errors?.map((e: any) => `${e.instancePath} ${e.message}`).join(', ');
                                    throw new Error(`JSON Schema validation failed: ${errors}`);
                                }
                            }
                        } catch (e: any) {
                            e.stepResult = resultObject;
                            throw e;
                        }
                    }
                }

                return resultObject;
            }

            case 'feed-check': {
                const feedUrl = step.value || step.selector;
                const mergedHeaders = { ...globalSettings.headers };
                const mergedParams = { ...globalSettings.params };

                console.log(`  [Feed] Checking ${feedUrl}`);

                let feedResponse;
                let actualRequestHeaders = mergedHeaders;
                let actualRequestUrl = feedUrl;
                const requestHandler = async (request: any) => {
                    try {
                        const requestUrl = request.url();
                        if ((requestUrl === feedUrl || requestUrl.split('?')[0] === feedUrl.split('?')[0]) && request.method() === 'GET') {
                            actualRequestHeaders = await request.allHeaders();
                            actualRequestUrl = requestUrl;
                        }
                    } catch (e) { }
                };

                page.context().on('request', requestHandler);
                try {
                    feedResponse = await page.request.get(feedUrl, { headers: mergedHeaders, params: mergedParams });
                } finally {
                    page.context().off('request', requestHandler);
                }

                if (!feedResponse.ok()) throw new Error(`Failed to fetch feed: ${feedResponse.status()}`);

                const feedText = await feedResponse.text();
                const doc = new DOMParser().parseFromString(feedText, 'text/xml');

                const resultObject = {
                    type: 'feed-check',
                    status: feedResponse.status(),
                    headers: feedResponse.headers(),
                    body: feedText,
                    request: {
                        url: actualRequestUrl,
                        method: 'GET',
                        headers: actualRequestHeaders,
                        params: mergedParams
                    }
                };

                if (step.params?.assertions) {
                    for (const assertion of step.params.assertions) {
                        try {
                            if (assertion.type === 'xpath') {
                                const nodes = xpath.select(assertion.path, doc);
                                const nodeValue = nodes[0] ? (nodes[0] as any).textContent : null;

                                if (assertion.operator === 'equals') {
                                    if (nodeValue !== assertion.value) throw new Error(`Expected XPath ${assertion.path} to equal ${assertion.value} but got ${nodeValue}`);
                                } else if (assertion.operator === 'contains') {
                                    if (!nodeValue || !nodeValue.includes(assertion.value)) throw new Error(`Expected XPath ${assertion.path} to contain ${assertion.value} but got ${nodeValue}`);
                                } else if (assertion.operator === 'exists') {
                                    if (!nodes || nodes.length === 0) throw new Error(`Expected XPath ${assertion.path} to exist`);
                                }
                            } else if (assertion.type === 'text') {
                                if (!feedText.includes(assertion.value)) throw new Error(`Expected feed to contain text "${assertion.value}"`);
                            }
                        } catch (e: any) {
                            e.stepResult = resultObject;
                            throw e;
                        }
                    }
                }

                return resultObject;
            }

            case 'click': {
                const clickSelector = step.selector || step.value;
                if (clickSelector) {
                    const locator = getLocator(clickSelector);
                    await locator.waitFor({ state: 'visible', timeout: 80000 });
                    await moveMouseTo(locator);
                    await locator.click();
                }
                break;
            }

            case 'fill':
                if (step.selector) {
                    const locator = getLocator(step.selector);
                    await locator.waitFor({ state: 'visible', timeout: 80000 });
                    await moveMouseTo(locator);
                    await locator.fill(step.value || '');
                }
                break;

            case 'check': {
                const checkSelector = step.selector || step.value;
                if (checkSelector) {
                    const locator = getLocator(checkSelector);
                    await locator.waitFor({ state: 'visible', timeout: 80000 });
                    await moveMouseTo(locator);
                    await locator.check();
                }
                break;
            }

            case 'expect-visible': {
                const visibleSelector = step.selector || step.value;
                if (visibleSelector) {
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(visibleSelector, { state: 'visible', timeout: 80000 });
                    } else {
                        await getLocator(visibleSelector).waitFor({ state: 'visible', timeout: 80000 });
                    }
                }
                break;
            }

            case 'wait-for-selector': {
                const waitSelector = step.selector || step.value;
                if (waitSelector) {
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(waitSelector, { state: 'attached', timeout: 80000 });
                    } else {
                        await getLocator(waitSelector).waitFor({ state: 'attached', timeout: 80000 });
                    }
                }
                break;
            }

            case 'expect-hidden': {
                const hiddenSelector = step.selector || step.value;
                if (hiddenSelector) {
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(hiddenSelector, { state: 'hidden', timeout: 50000 });
                    } else {
                        await getLocator(hiddenSelector).waitFor({ state: 'hidden', timeout: 50000 });
                    }
                }
                break;
            }

            case 'expect-text':
                if (step.selector && step.value) {
                    const locator = getLocator(step.selector);
                    await locator.waitFor({ state: 'visible', timeout: 50000 });
                    const text = await locator.textContent();
                    if (!text?.includes(step.value)) {
                        throw new Error(`Expected text "${step.value}" not found in element "${step.selector}"`);
                    }
                }
                break;

            case 'expect-url': {
                const expectedUrl = step.value || step.selector;
                if (expectedUrl) await page.waitForURL(expectedUrl, { timeout: 15000 });
                break;
            }

            case 'hover': {
                const hoverSelector = step.selector || step.value;
                if (hoverSelector) {
                    const locator = getLocator(hoverSelector);
                    await locator.hover();
                }
                break;
            }

            case 'select-option':
                if (step.selector && step.value) {
                    const locator = getLocator(step.selector);
                    await moveMouseTo(locator);
                    await locator.selectOption(step.value);
                }
                break;

            case 'press-key': {
                const key = step.value || step.selector;
                if (key) await page.keyboard.press(key);
                break;
            }

            case 'screenshot': {
                const screenshotName = step.value || `screenshot-${Date.now()}`;
                const videoPath = await page.video()?.path();
                const screenshotPath = path.join(videoPath ? path.dirname(videoPath) : '/tmp', `${screenshotName}.png`);
                await page.screenshot({ path: screenshotPath, fullPage: true });
                console.log(`Screenshot saved to: ${screenshotPath}`);
                break;
            }

            case 'scroll-to': {
                const scrollSelector = step.selector || step.value;
                if (scrollSelector) {
                    const locator = getLocator(scrollSelector);
                    await locator.scrollIntoViewIfNeeded();
                }
                break;
            }

            case 'wait-timeout': {
                const timeout = parseInt(step.value || step.selector || '1000');
                await page.waitForTimeout(timeout);
                break;
            }

            case 'carousel-find': {
                const targetSelector = step.selector;
                const nextButtonSelector = step.value;
                const maxSwipes = step.params?.max_swipes || 10;

                let found = false;
                for (let i = 0; i < maxSwipes; i++) {
                    const target = getLocator(targetSelector);
                    if (await target.isVisible().catch(() => false)) {
                        found = true;
                        break;
                    }
                    console.log(`  [Carousel] Target not visible, clicking next (${i + 1}/${maxSwipes})`);
                    const nextBtn = getLocator(nextButtonSelector);
                    if (await nextBtn.isVisible()) {
                        await nextBtn.click();
                        await page.waitForTimeout(500);
                    } else {
                        throw new Error(`Carousel next button '${nextButtonSelector}' not found/visible`);
                    }
                }
                if (!found) {
                    const target = getLocator(targetSelector);
                    if (await target.isVisible().catch(() => false)) {
                        found = true;
                    } else {
                        throw new Error(`Could not find target '${targetSelector}' in carousel after ${maxSwipes} attempts`);
                    }
                }
                break;
            }

            case 'verify-nth-child': {
                const parentSelector = step.selector;
                const index = parseInt(step.value || '0');
                const expectedText = step.params?.text;

                const elements = getLocator(parentSelector);
                const count = await elements.count();

                if (index < 0 || index >= count) {
                    // Note: count is 0 if none found, which is index out of bounds 0 >= 0
                    throw new Error(`Index ${index} out of bounds (found ${count} elements for '${parentSelector}')`);
                }

                const child = elements.nth(index);
                if (expectedText) {
                    await child.waitFor({ state: 'visible', timeout: 30000 });
                    const text = await child.textContent();
                    if (!text?.includes(expectedText)) {
                        throw new Error(`Expected nth-child(${index}) to contain "${expectedText}" but got "${text}"`);
                    }
                }
                break;
            }

            case 'count-children': {
                const parentSelector = step.selector;
                const expectedCount = parseInt(step.value || '0');
                const operator = step.params?.operator || 'equals';

                if (expectedCount > 0) {
                    try {
                        await context.locator(parentSelector).first().waitFor({ state: 'attached', timeout: 5000 });
                    } catch (e) { }
                }

                const count = await context.locator(parentSelector).count();
                console.log(`  [Count] Found ${count} elements matching '${parentSelector}'`);

                if (operator === 'equals' && count !== expectedCount) throw new Error(`Expected ${expectedCount} children, found ${count}`);
                if (operator === 'gte' && count < expectedCount) throw new Error(`Expected at least ${expectedCount} children, found ${count}`);
                if (operator === 'lte' && count > expectedCount) throw new Error(`Expected at most ${expectedCount} children, found ${count}`);
                break;
            }

            default:
                if (step.type === 'switch-frame') break; // Handled in the main loop
                console.warn(`Unknown step type: ${step.type}`);
        }
    }
}
