/**
 * Device-cloud adapter — MOB-4.
 *
 * The mobile worker drives whatever answers the W3C WebDriver protocol.
 * Locally that's an Appium server (APPIUM_URL); in the cloud it's
 * BrowserStack App Automate, Sauce Labs, or LambdaTest — same protocol,
 * three differences this module absorbs:
 *
 *   1. Hub URL + basic-auth credentials.
 *   2. App delivery: clouds cannot fetch our internal MinIO presigned URL,
 *      so the worker downloads the binary and pushes it to the cloud's
 *      storage API; the returned app id (bs://…, storage:…, lt://…) goes
 *      into the `appium:app` capability. Uploads are cached per
 *      (provider, app_build_id) for the worker's lifetime.
 *   3. A vendor options block (bstack:options / sauce:options / lt:options)
 *      carrying credentials, device name, and OS version.
 *
 * Selected via MOBILE_DEVICE_PROVIDER = local (default) | browserstack |
 * saucelabs | lambdatest. iOS runs REQUIRE a cloud provider (or a macOS
 * host running Appium yourself) — Linux cannot host iOS simulators.
 */

export interface UploadableApp {
    app_build_id: number;
    platform: 'android' | 'ios';
    /** Presigned MinIO URL, reachable from the worker only. */
    app_url: string;
    app_name?: string;
    original_filename?: string;
}

export interface DeviceCloudProvider {
    readonly name: string;
    /** WebDriver hub endpoint the WebDriverClient talks to. */
    readonly webdriverUrl: string;
    /** Authorization header for hub + storage calls (undefined = none). */
    readonly authHeader?: string;
    /**
     * Ensure the binary is where this provider can install it from and
     * return the value for the `appium:app` capability.
     */
    resolveApp(app: UploadableApp): Promise<string>;
    /** Add vendor-specific capability blocks (credentials, device, OS). */
    decorateCapabilities(caps: Record<string, any>, app: UploadableApp): Record<string, any>;
}

const DEVICE_NAME = () => process.env.MOBILE_DEVICE_NAME || process.env.DEVICE_NAME || '';
const PLATFORM_VERSION = () => process.env.MOBILE_PLATFORM_VERSION || '';

/** (provider, app_build_id) → cloud app id. Worker-lifetime cache: a
 * restart re-uploads, which is safe (clouds dedupe by checksum anyway). */
const uploadCache = new Map<string, string>();

function basicAuth(user: string, key: string): string {
    return 'Basic ' + Buffer.from(`${user}:${key}`).toString('base64');
}

function requireEnv(name: string): string {
    const v = process.env[name];
    if (!v) throw new Error(`Device cloud misconfigured: ${name} is not set`);
    return v;
}

/** Download the binary from MinIO (worker-reachable) into memory. */
async function fetchAppBytes(app: UploadableApp): Promise<{ buf: Buffer; filename: string }> {
    const res = await fetch(app.app_url);
    if (!res.ok) {
        throw new Error(`Failed to download app build ${app.app_build_id} from storage: HTTP ${res.status}`);
    }
    const buf = Buffer.from(await res.arrayBuffer());
    const filename = app.original_filename
        || `${(app.app_name || 'app').replace(/[^a-zA-Z0-9_-]+/g, '_')}.${app.platform === 'ios' ? 'ipa' : 'apk'}`;
    return { buf, filename };
}

async function uploadOnce(app: UploadableApp, providerName: string,
    doUpload: (buf: Buffer, filename: string) => Promise<string>): Promise<string> {
    const cacheKey = `${providerName}:${app.app_build_id}`;
    const cached = uploadCache.get(cacheKey);
    if (cached) return cached;
    const { buf, filename } = await fetchAppBytes(app);
    console.log(`[DeviceCloud] Uploading ${filename} (${(buf.length / 1e6).toFixed(1)} MB) to ${providerName}…`);
    const appId = await doUpload(buf, filename);
    console.log(`[DeviceCloud] ${providerName} app id: ${appId}`);
    uploadCache.set(cacheKey, appId);
    return appId;
}

// ---------------------------------------------------------------------------
// Local Appium (default) — passthrough: Appium installs from the MinIO URL.
// ---------------------------------------------------------------------------

class LocalAppium implements DeviceCloudProvider {
    readonly name = 'local';
    readonly webdriverUrl = process.env.APPIUM_URL || 'http://localhost:4723';

    async resolveApp(app: UploadableApp): Promise<string> {
        return app.app_url;
    }

    decorateCapabilities(caps: Record<string, any>): Record<string, any> {
        return caps;
    }
}

// ---------------------------------------------------------------------------
// BrowserStack App Automate
// ---------------------------------------------------------------------------

class BrowserStack implements DeviceCloudProvider {
    readonly name = 'browserstack';
    readonly webdriverUrl = 'https://hub-cloud.browserstack.com/wd/hub';
    readonly authHeader = basicAuth(
        requireEnv('BROWSERSTACK_USERNAME'), requireEnv('BROWSERSTACK_ACCESS_KEY'));

    async resolveApp(app: UploadableApp): Promise<string> {
        return uploadOnce(app, this.name, async (buf, filename) => {
            const form = new FormData();
            form.append('file', new Blob([new Uint8Array(buf)]), filename);
            const res = await fetch('https://api-cloud.browserstack.com/app-automate/upload', {
                method: 'POST',
                headers: { Authorization: this.authHeader },
                body: form,
            });
            const json: any = await res.json().catch(() => ({}));
            if (!res.ok || !json.app_url) {
                throw new Error(`BrowserStack app upload failed: HTTP ${res.status} ${JSON.stringify(json).slice(0, 300)}`);
            }
            return json.app_url as string;   // bs://<hash>
        });
    }

    decorateCapabilities(caps: Record<string, any>): Record<string, any> {
        return {
            ...caps,
            'bstack:options': {
                userName: process.env.BROWSERSTACK_USERNAME,
                accessKey: process.env.BROWSERSTACK_ACCESS_KEY,
                ...(DEVICE_NAME() ? { deviceName: DEVICE_NAME() } : {}),
                ...(PLATFORM_VERSION() ? { osVersion: PLATFORM_VERSION() } : {}),
                projectName: 'TraceIQ',
                sessionName: caps['traceiq:sessionName'] || 'TraceIQ mobile run',
            },
        };
    }
}

// ---------------------------------------------------------------------------
// Sauce Labs (real devices / emulators via App Storage)
// ---------------------------------------------------------------------------

class SauceLabs implements DeviceCloudProvider {
    readonly name = 'saucelabs';
    private region = process.env.SAUCE_REGION || 'us-west-1';
    readonly webdriverUrl = `https://ondemand.${this.region}.saucelabs.com/wd/hub`;
    readonly authHeader = basicAuth(
        requireEnv('SAUCE_USERNAME'), requireEnv('SAUCE_ACCESS_KEY'));

    async resolveApp(app: UploadableApp): Promise<string> {
        return uploadOnce(app, this.name, async (buf, filename) => {
            const form = new FormData();
            form.append('payload', new Blob([new Uint8Array(buf)]), filename);
            form.append('name', filename);
            const res = await fetch(`https://api.${this.region}.saucelabs.com/v1/storage/upload`, {
                method: 'POST',
                headers: { Authorization: this.authHeader },
                body: form,
            });
            const json: any = await res.json().catch(() => ({}));
            const id = json?.item?.id;
            if (!res.ok || !id) {
                throw new Error(`Sauce Labs app upload failed: HTTP ${res.status} ${JSON.stringify(json).slice(0, 300)}`);
            }
            return `storage:${id}`;
        });
    }

    decorateCapabilities(caps: Record<string, any>): Record<string, any> {
        return {
            ...caps,
            ...(PLATFORM_VERSION() ? { 'appium:platformVersion': PLATFORM_VERSION() } : {}),
            'sauce:options': {
                username: process.env.SAUCE_USERNAME,
                accessKey: process.env.SAUCE_ACCESS_KEY,
                name: caps['traceiq:sessionName'] || 'TraceIQ mobile run',
            },
        };
    }
}

// ---------------------------------------------------------------------------
// LambdaTest (real devices)
// ---------------------------------------------------------------------------

class LambdaTest implements DeviceCloudProvider {
    readonly name = 'lambdatest';
    readonly webdriverUrl = 'https://mobile-hub.lambdatest.com/wd/hub';
    readonly authHeader = basicAuth(
        requireEnv('LT_USERNAME'), requireEnv('LT_ACCESS_KEY'));

    async resolveApp(app: UploadableApp): Promise<string> {
        return uploadOnce(app, this.name, async (buf, filename) => {
            const form = new FormData();
            form.append('appFile', new Blob([new Uint8Array(buf)]), filename);
            form.append('name', filename);
            const res = await fetch('https://manual-api.lambdatest.com/app/upload/realDevice', {
                method: 'POST',
                headers: { Authorization: this.authHeader },
                body: form,
            });
            const json: any = await res.json().catch(() => ({}));
            const id = json?.app_url || json?.app_id;
            if (!res.ok || !id) {
                throw new Error(`LambdaTest app upload failed: HTTP ${res.status} ${JSON.stringify(json).slice(0, 300)}`);
            }
            return id as string;   // lt://<id>
        });
    }

    decorateCapabilities(caps: Record<string, any>): Record<string, any> {
        return {
            ...caps,
            ...(PLATFORM_VERSION() ? { 'appium:platformVersion': PLATFORM_VERSION() } : {}),
            'lt:options': {
                username: process.env.LT_USERNAME,
                accessKey: process.env.LT_ACCESS_KEY,
                ...(DEVICE_NAME() ? { deviceName: DEVICE_NAME() } : {}),
                ...(PLATFORM_VERSION() ? { platformVersion: PLATFORM_VERSION() } : {}),
                isRealMobile: true,
                project: 'TraceIQ',
                build: caps['traceiq:sessionName'] || 'TraceIQ mobile run',
            },
        };
    }
}

// ---------------------------------------------------------------------------

export function pickDeviceProvider(): DeviceCloudProvider {
    const name = (process.env.MOBILE_DEVICE_PROVIDER || 'local').toLowerCase();
    switch (name) {
        case 'local': return new LocalAppium();
        case 'browserstack': return new BrowserStack();
        case 'saucelabs':
        case 'sauce': return new SauceLabs();
        case 'lambdatest': return new LambdaTest();
        default:
            throw new Error(
                `Unknown MOBILE_DEVICE_PROVIDER '${name}' — use local | browserstack | saucelabs | lambdatest`);
    }
}
