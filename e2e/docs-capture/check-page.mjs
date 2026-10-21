// Renders one guide page from disk and reports what a reader would trip over.
// Usage (via check-page.sh): node docs-capture/check-page.mjs <group>/<page> <screenshot.png>
import { chromium } from '@playwright/test';

const [slug, shot] = process.argv.slice(2);
const url = `file:///work/frontend/public/docs/${slug}.html`;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const problems = [];
p.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); });
p.on('pageerror', (e) => problems.push(`page error: ${e}`));
// Playwright's Chromium has no H.264, so the MP4 never loads here (real browsers play it).
p.on('requestfailed', (r) => {
    if (!r.url().startsWith('https://fonts.') && !r.url().endsWith('.mp4')) problems.push(`failed: ${r.url()}`);
});
await p.goto(url, { waitUntil: 'load' });
for (let y = 0; y < 30000; y += 700) { await p.mouse.wheel(0, 700); await p.waitForTimeout(60); }
await p.waitForTimeout(800);
const report = await p.evaluate(() => ({
    title: document.title,
    h1: document.querySelector('main h1')?.textContent?.trim(),
    sections: [...document.querySelectorAll('main h2')].map((h) => h.textContent.trim()),
    brokenImages: [...document.querySelectorAll('main img')].filter((i) => !i.naturalWidth).map((i) => i.getAttribute('src')),
    missingAlt: [...document.querySelectorAll('main img')].filter((i) => !i.alt).map((i) => i.getAttribute('src')),
    videos: [...document.querySelectorAll('main video source')].map((s) => s.getAttribute('src')),
    activeNav: document.querySelector('.nav-group li a.active')?.textContent?.trim() ?? null,
    pager: document.getElementById('pager')?.innerText.replace(/\s+/g, ' ').trim(),
    tocEntries: document.querySelectorAll('#toc a').length,
    horizontalScroll: document.documentElement.scrollWidth > window.innerWidth,
}));
await p.setViewportSize({ width: 390, height: 844 });
await p.waitForTimeout(300);
report.phoneHorizontalScroll = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
await p.setViewportSize({ width: 1440, height: 900 });
if (shot) await p.screenshot({ path: shot, fullPage: true });
await b.close();
console.log(JSON.stringify({ ...report, problems }, null, 2));
