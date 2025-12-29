import { BrowserContext } from 'playwright';

export class NetworkInterceptor {
    public static async setupNetworkListeners(
        context: BrowserContext,
        requestStartTimes: Map<string, number>,
        networkEvents: any[],
        testCaseContext: { id: number | null, name: string | null }
    ) {
        context.on('request', request => {
            requestStartTimes.set(request.url(), Date.now());
        });

        context.on('response', async response => {
            try {
                const request = response.request();
                const url = response.url();
                const startTime = requestStartTimes.get(url) || Date.now();
                const endTime = Date.now();
                const duration = endTime - startTime;

                // Clean up map
                requestStartTimes.delete(url);

                const headers = await response.allHeaders();
                const reqHeaders = await request.allHeaders();

                networkEvents.push({
                    testCaseId: testCaseContext.id,
                    testCaseName: testCaseContext.name,
                    url: url,
                    method: request.method(),
                    resourceType: request.resourceType(),
                    status: response.status(),
                    startTime,
                    endTime,
                    duration,
                    requestHeaders: reqHeaders,
                    responseHeaders: headers
                });
            } catch (e) {
                console.error('Error capturing network event:', e);
            }
        });
    }

    public static async setupRouteInterception(
        context: BrowserContext,
        currentSettings: any,
        sourceDomain: { value: string | null }
    ) {
        const getDomain = (url: string) => {
            try {
                return new URL(url).hostname;
            } catch {
                return '';
            }
        };

        const appendParams = (targetUrl: string, params: any) => {
            try {
                const urlObj = new URL(targetUrl);
                for (const [key, value] of Object.entries(params)) {
                    const strValue = String(value);
                    const existingValues = urlObj.searchParams.getAll(key);
                    if (!existingValues.includes(strValue)) {
                        urlObj.searchParams.append(key, strValue);
                    }
                }
                return urlObj.toString();
            } catch (e) {
                return targetUrl;
            }
        };

        await context.route('**/*', async route => {
            const request = route.request();
            const urlStr = request.url();
            const hostname = getDomain(urlStr);

            const { headers: globalHeaders, params: globalParams, allowed_domains: allowedDomains, domain_settings: domainSettings } = currentSettings;

            if (!sourceDomain.value && request.isNavigationRequest()) {
                sourceDomain.value = hostname;
                console.log(`Inferred source domain: ${sourceDomain.value}`);
            }

            const headers = { ...request.headers() };
            let modifiedHeaders = false;
            let modifiedUrl = false;
            let newUrl = urlStr;

            // 1. Check for domain-specific settings first
            if (domainSettings[hostname]) {
                const specificHeaders = domainSettings[hostname].headers;
                const specificParams = domainSettings[hostname].params;

                if (specificHeaders) {
                    Object.assign(headers, specificHeaders);
                    modifiedHeaders = true;
                }
                if (specificParams) {
                    newUrl = appendParams(newUrl, specificParams);
                    if (newUrl !== urlStr) modifiedUrl = true;
                }
            }

            // 2. Check source domain or allowed domains
            const normalizedAllowedDomains = allowedDomains.map((d: any) => {
                if (typeof d === 'string') return { domain: d, headers: true, params: false };
                return { domain: d.domain, headers: d.headers !== false, params: d.params === true };
            });

            const matchedAllowedDomain = normalizedAllowedDomains.find((d: any) =>
                hostname === d.domain || hostname.endsWith(`.${d.domain}`)
            );

            const isSourceDomain = sourceDomain.value && (hostname === sourceDomain.value || hostname.endsWith(`.${sourceDomain.value}`));

            if (isSourceDomain || matchedAllowedDomain) {
                const allowHeaders = isSourceDomain || matchedAllowedDomain?.headers;
                const allowParams = isSourceDomain || matchedAllowedDomain?.params;

                if (allowHeaders && Object.keys(globalHeaders).length > 0) {
                    Object.assign(headers, globalHeaders);
                    modifiedHeaders = true;
                }

                if (sourceDomain.value && hostname !== sourceDomain.value && domainSettings[sourceDomain.value]) {
                    const sourceHeaders = domainSettings[sourceDomain.value].headers;
                    const sourceParams = domainSettings[sourceDomain.value].params;
                    if (allowHeaders && sourceHeaders) {
                        Object.assign(headers, sourceHeaders);
                        modifiedHeaders = true;
                    }
                    if (allowParams && sourceParams) {
                        newUrl = appendParams(newUrl, sourceParams);
                        if (newUrl !== urlStr) modifiedUrl = true;
                    }
                }

                if (allowParams && Object.keys(globalParams).length > 0) {
                    newUrl = appendParams(newUrl, globalParams);
                    if (newUrl !== urlStr) modifiedUrl = true;
                }
            }

            if (modifiedHeaders || modifiedUrl) {
                const continueOptions: any = { headers };
                if (modifiedUrl) continueOptions.url = newUrl;
                await route.continue(continueOptions);
            } else {
                await route.continue();
            }
        });
    }
}
