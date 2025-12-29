import { chromium, firefox, webkit, Browser, BrowserContext, Page } from 'playwright';

export class BrowserManager {
    private browser: Browser | null = null;
    private currentBrowserType: string = 'chromium';

    async start(browserType: string = 'chromium'): Promise<Browser> {
        if (this.browser && this.currentBrowserType !== browserType) {
            console.log(`Switching browser from ${this.currentBrowserType} to ${browserType}`);
            await this.stop();
        }

        if (!this.browser) {
            console.log(`Launching browser: ${browserType}`);

            switch (browserType) {
                case 'firefox':
                    this.browser = await firefox.launch({
                        headless: true
                    });
                    break;
                case 'webkit':
                    this.browser = await webkit.launch({
                        headless: true
                    });
                    break;
                case 'chromium':
                default:
                    this.browser = await chromium.launch({
                        headless: true,
                        args: ['--no-sandbox', '--disable-setuid-sandbox']
                    });
                    break;
            }
            this.currentBrowserType = browserType;
        }
        return this.browser;
    }

    async stop() {
        if (this.browser) {
            await this.browser.close();
            this.browser = null;
        }
    }

    async injectInitScripts(context: BrowserContext, browserType: string, deviceName: string | null, emulatedAs: string | null) {
        await context.addInitScript(({ browserType, deviceName, emulatedAs }: { browserType: string, deviceName: string | null, emulatedAs: string | null }) => {
            const initElements = () => {
                // Mouse cursor
                const box = document.createElement('div');
                box.classList.add('selenium-mouse-helper');

                // Browser indicator badge
                const browserBadge = document.createElement('div');
                browserBadge.classList.add('browser-indicator');
                browserBadge.textContent = browserType.toUpperCase();

                const styleElement = document.createElement('style');
                styleElement.innerHTML = `
                    .selenium-mouse-helper {
                        pointer-events: none;
                        position: absolute;
                        top: 0;
                        left: 0;
                        width: 20px;
                        height: 20px;
                        border: 1px solid white;
                        border-radius: 50%;
                        background: rgba(255, 0, 0, 0.7);
                        margin: -10px 0 0 -10px;
                        padding: 0;
                        transition: background .2s, border-radius .2s, border-color .2s;
                        box-shadow: 0 0 4px rgba(0,0,0,0.8);
                        z-index: 100000;
                    }
                    .selenium-mouse-helper.button-pressed {
                        background: rgba(255, 0, 0, 1);
                        transform: scale(0.9);
                    }
                    .browser-indicator {
                        pointer-events: none !important;
                        position: fixed !important;
                        top: 20px !important;
                        right: 20px !important;
                        padding: 8px 16px !important;
                        background: rgba(0, 0, 0, 0.85) !important;
                        color: white !important;
                        font-family: 'Courier New', monospace !important;
                        font-size: 12px !important;
                        font-weight: bold !important;
                        border-radius: 6px !important;
                        z-index: 2147483647 !important;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
                        border: 2px solid ${browserType === 'chromium' ? '#4285f4' : browserType === 'firefox' ? '#ff7139' : '#00d4ff'} !important;
                        display: flex !important;
                        flex-direction: column !important;
                        gap: 4px !important;
                        min-width: 150px !important;
                        opacity: 1 !important;
                        transition: opacity 0.5s ease-out !important;
                    }
                    .browser-indicator.hidden {
                        opacity: 0 !important;
                    }
                    .browser-indicator .browser-type {
                        font-size: 14px !important;
                        color: ${browserType === 'chromium' ? '#4285f4' : browserType === 'firefox' ? '#ff7139' : '#00d4ff'} !important;
                    }
                    .browser-indicator .device-name {
                        font-size: 10px !important;
                        color: #888 !important;
                        font-weight: normal !important;
                    }
                    .browser-indicator .emulated-note {
                        font-size: 10px !important;
                        color: rgba(255, 255, 255, 0.5) !important;
                        font-weight: normal !important;
                        font-style: italic !important;
                    }
                    .browser-indicator .test-name {
                        font-size: 11px !important;
                        color: #aaa !important;
                        font-weight: normal !important;
                    }
                `;
                document.head.appendChild(styleElement);
                document.body.appendChild(box);

                // Create badge with browser type, device, and test name sections
                browserBadge.innerHTML = `
                    <div class="browser-type">
                        ${browserType.toUpperCase()}
                        ${emulatedAs && emulatedAs !== browserType ? `<span class="emulated-note"> (as ${emulatedAs})</span>` : ''}
                    </div>
                    ${deviceName ? `<div class="device-name">${deviceName}</div>` : ''}
                    <div class="test-name" id="test-name-display">${(window as any).__TRACEIQ_TEST_NAME__ || 'Loading...'}</div>
                `;
                document.body.appendChild(browserBadge);

                // Watch for test name changes in window property
                let lastTestName = '';
                let hideTimeout: any = null;
                setInterval(() => {
                    const testNameEl = document.getElementById('test-name-display');
                    const currentTestName = (window as any).__TRACEIQ_TEST_NAME__ || 'Loading...';

                    if (testNameEl && currentTestName !== lastTestName) {
                        testNameEl.textContent = currentTestName;
                        lastTestName = currentTestName;

                        // Reset hide timeout when test name changes
                        if (hideTimeout) clearTimeout(hideTimeout);
                        browserBadge.classList.remove('hidden');

                        // Auto-hide after 3 seconds (only if not "Loading...")
                        if (currentTestName !== 'Loading...') {
                            hideTimeout = setTimeout(() => {
                                browserBadge.classList.add('hidden');
                            }, 3000);
                        }
                    }
                }, 100);

                document.addEventListener('mousemove', event => {
                    box.style.left = event.pageX + 'px';
                    box.style.top = event.pageY + 'px';
                }, true);

                document.addEventListener('mousedown', event => {
                    box.classList.add('button-pressed');
                }, true);

                document.addEventListener('mouseup', event => {
                    box.classList.remove('button-pressed');
                }, true);
            };

            // Wait for body to exist
            if (document.body) {
                initElements();
            } else {
                const observer = new MutationObserver(() => {
                    if (document.body) {
                        observer.disconnect();
                        initElements();
                    }
                });
                observer.observe(document.documentElement, { childList: true });
            }
        }, { browserType, deviceName: deviceName || null, emulatedAs: emulatedAs || null });
    }
}
