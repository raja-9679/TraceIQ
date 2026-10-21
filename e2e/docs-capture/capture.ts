import type { Browser, Locator, Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

// Helpers shared by the page captures. Pictures land in
// frontend/public/docs/media/<section>/<page>/ — the guide references them by
// those paths, so a re-run refreshes the guide without touching its HTML.

export const MEDIA = join(__dirname, '../../frontend/public/docs/media');

export function project(): { id: number; name: string } {
    return JSON.parse(readFileSync('.auth/docs-project.json', 'utf8'));
}

export function out(path: string): string {
    const full = join(MEDIA, path);
    mkdirSync(dirname(full), { recursive: true });
    return full;
}

/** Waits until the page has stopped fetching (no request for 750 ms) — data-driven pages render late. */
export async function settle(page: Page, quietMs = 750, maxMs = 20_000) {
    let last = Date.now();
    const bump = () => { last = Date.now(); };
    page.on('request', bump);
    page.on('requestfinished', bump);
    const start = Date.now();
    while (Date.now() - last < quietMs && Date.now() - start < maxMs) {
        await page.waitForTimeout(100);
    }
    page.off('request', bump);
    page.off('requestfinished', bump);
}

// Pastel callout colours, same family as the app (tailwind.config.js pastel-*).
const BADGE = { fill: '#E9E7FD', ink: '#3A31A8', ring: '#A9A2EE' };

/**
 * Draws numbered badges + a soft outline on the given elements, so the guide
 * can say "① is …". Returns a cleanup that removes them again.
 */
export async function annotate(page: Page, marks: { n: number; target: Locator; inset?: boolean; side?: 'left' | 'right' }[]) {
    for (const m of marks) {
        await m.target.first().scrollIntoViewIfNeeded();
        const box = await m.target.first().boundingBox();
        if (!box) throw new Error(`annotation ${m.n}: element not visible`);
        await page.evaluate(({ n, box, inset, right, c }) => {
            const sx = window.scrollX, sy = window.scrollY;
            const ring = document.createElement('div');
            ring.dataset.docsMark = '1';
            Object.assign(ring.style, {
                position: 'absolute', left: `${box.x + sx - 4}px`, top: `${box.y + sy - 4}px`,
                width: `${box.width + 8}px`, height: `${box.height + 8}px`,
                border: `2px dashed ${c.ring}`, borderRadius: '14px', zIndex: '99998', pointerEvents: 'none',
            });
            const badge = document.createElement('div');
            badge.dataset.docsMark = '1';
            badge.textContent = String(n);
            const edge = right ? box.x + sx + box.width - 12 : box.x + sx - 16;
            const left = inset ? (right ? edge - 24 : box.x + sx + 8) : edge;
            const top = inset ? box.y + sy + 8 : box.y + sy - 16;
            Object.assign(badge.style, {
                position: 'absolute', left: `${Math.max(2, left)}px`, top: `${Math.max(2, top)}px`,
                width: '28px', height: '28px', borderRadius: '999px', background: c.fill, color: c.ink,
                border: `2px solid ${c.ring}`, font: '700 14px/24px Geist, system-ui, sans-serif',
                textAlign: 'center', zIndex: '99999', pointerEvents: 'none',
                boxShadow: '0 2px 6px rgba(28,27,34,.12)',
            });
            document.body.append(ring, badge);
        }, { n: m.n, box, inset: !!m.inset, right: m.side === 'right', c: BADGE });
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    return async () => {
        await page.evaluate(() => document.querySelectorAll('[data-docs-mark]').forEach((e) => e.remove()));
    };
}

/** A visible pointer for recordings — Playwright's videos don't show the mouse. */
const POINTER = `
(() => {
  const add = () => {
    if (document.getElementById('docs-pointer')) return;
    const p = document.createElement('div');
    p.id = 'docs-pointer';
    Object.assign(p.style, {
      position: 'fixed', left: '-40px', top: '-40px', width: '22px', height: '22px',
      borderRadius: '999px', background: 'rgba(233,231,253,.85)', border: '2px solid #A9A2EE',
      boxShadow: '0 2px 8px rgba(28,27,34,.18)', transform: 'translate(-50%,-50%)',
      transition: 'transform .12s ease', zIndex: '2147483647', pointerEvents: 'none',
    });
    document.body.appendChild(p);
    addEventListener('mousemove', (e) => { p.style.left = e.clientX + 'px'; p.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { p.style.transform = 'translate(-50%,-50%) scale(.7)'; }, true);
    addEventListener('mouseup', () => { p.style.transform = 'translate(-50%,-50%)'; }, true);
  };
  if (document.body) add(); else addEventListener('DOMContentLoaded', add);
})();`;

/**
 * Records a short walkthrough. `script` drives the page; pauses between steps
 * are deliberate (this is a video for people, not a test), so use `beat()`.
 */
export async function record(browser: Browser, file: string, script: (page: Page) => Promise<void>) {
    const size = { width: 1280, height: 800 };
    const ctx = await browser.newContext({
        viewport: size,
        recordVideo: { dir: out('.tmp-video'), size },
        storageState: '.auth/docs-user.json',
    });
    await ctx.addInitScript(POINTER);
    const page = await ctx.newPage();
    // The recording starts with the page, so caption times are measured from here.
    const narration = VOICE.enabled ? new Narration() : null;
    if (narration) narrations.set(page, narration);
    await script(page);
    if (narration) await narration.finish();
    await beat(page, 1200);
    const video = page.video();
    await ctx.close();
    if (!video) throw new Error('no video recorded');
    renameSync(await video.path(), out(file));
    // run.sh mixes the spoken lines into the MP4 at these offsets (mux.py).
    if (narration) {
        writeFileSync(out(file).replace(/\.webm$/, '.narration.json'),
            JSON.stringify({ voice: VOICE.model, lines: narration.lines }, null, 1));
    }
}

/** A pause the viewer can follow. */
export const beat = (page: Page, ms = 900) => page.waitForTimeout(ms);

/** Moves the visible pointer to an element in a few steps, then pauses on it. */
export async function pointAt(page: Page, target: Locator, pause = 900) {
    const box = await target.first().boundingBox();
    if (!box) return;
    await page.mouse.move(box.x + box.width / 2, box.y + Math.min(box.height / 2, 40), { steps: 18 });
    await beat(page, pause);
}

// ---------- narration ----------
// Captions are also spoken, by Piper (offline text-to-speech, MIT) with the
// US English "kristin" voice: public domain, trained from scratch on LibriVox
// recordings. Many Piper voices are fine-tuned from the US "lessac" voice,
// whose dataset licence is research-only — don't switch to one of those
// (README lists what was checked). run.sh downloads Piper + the voice into e2e/.cache/piper.
// TRACEIQ_DOCS_VOICE=off records captions only.

const VOICE = {
    bin: '.cache/piper/piper/piper',
    model: '.cache/piper/en_US-kristin-medium.onnx',
    cache: '.cache/narration',
    // Speaking pace: Piper's length_scale (1 = the voice's natural speed,
    // higher = slower). 1.3 because viewers found the natural pace too fast to
    // follow; TRACEIQ_DOCS_VOICE_PACE overrides it for a one-off recording.
    pace: process.env.TRACEIQ_DOCS_VOICE_PACE || '1.3',
    sentenceSilence: '0.6',
    get enabled() {
        return process.env.TRACEIQ_DOCS_VOICE !== 'off' && existsSync(this.bin) && existsSync(this.model);
    },
};
const GAP_MS = 800; // silence between two spoken lines

/** Seconds of audio in a PCM WAV file (from its header). */
function wavSeconds(file: string): number {
    const b = readFileSync(file);
    const byteRate = b.readUInt32LE(28);
    let i = 12;
    while (i + 8 <= b.length) {
        const id = b.toString('ascii', i, i + 4);
        const size = b.readUInt32LE(i + 4);
        if (id === 'data') return size / byteRate;
        i += 8 + size + (size % 2);
    }
    throw new Error(`no data chunk in ${file}`);
}

/** Speaks `text` into a cached WAV (same text + voice + pace = same file) and returns it. */
function synthesize(text: string): Promise<{ file: string; seconds: number }> {
    const key = createHash('sha256').update(`${VOICE.model}\n${VOICE.pace}\n${VOICE.sentenceSilence}\n${text}`).digest('hex').slice(0, 20);
    const file = join(VOICE.cache, `${key}.wav`);
    if (existsSync(file)) return Promise.resolve({ file, seconds: wavSeconds(file) });
    mkdirSync(VOICE.cache, { recursive: true });
    return new Promise((resolve, reject) => {
        const p = spawn(VOICE.bin, ['--model', VOICE.model, '--output_file', file,
            '--length_scale', VOICE.pace, '--sentence_silence', VOICE.sentenceSilence],
            { stdio: ['pipe', 'ignore', 'pipe'] });
        let err = '';
        p.stderr.on('data', (d) => { err += d; });
        p.on('error', reject);
        p.on('close', (code) => code === 0 && existsSync(file)
            ? resolve({ file, seconds: wavSeconds(file) })
            : reject(new Error(`piper failed (${code}): ${err.slice(-400)}`)));
        p.stdin.end(text);
    });
}

class Narration {
    readonly t0 = Date.now();
    readonly lines: { at_ms: number; wav: string; seconds: number }[] = [];
    private pending: Promise<void> = Promise.resolve();
    private endsAt = 0;

    /** Starts speaking now (synthesis runs while the script goes on). */
    say(text: string) {
        const at = Date.now() - this.t0;
        this.pending = synthesize(text).then(({ file, seconds }) => {
            this.lines.push({ at_ms: at, wav: file, seconds });
            this.endsAt = this.t0 + at + seconds * 1000;
        });
    }

    /** Waits until the line being spoken has finished, plus a short gap. */
    async finish() {
        await this.pending;
        const wait = this.endsAt + GAP_MS - Date.now();
        if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    }
}
const narrations = new WeakMap<Page, Narration>();

/**
 * Shows a subtitle at the bottom of the recording and, when narration is on,
 * speaks it. A new caption first waits for the previous one to be spoken, so
 * the actions written after a caption (pointing, clicking) happen while it is
 * being said. Pass '' to hide the caption (also waits). `say` replaces the
 * spoken words when the written ones read badly aloud ("60–89%").
 * Set it again after a navigation — the subtitle lives in the page.
 */
export async function caption(page: Page, text: string, opts: number | { pause?: number; say?: string } = 0) {
    const { pause = 0, say = text } = typeof opts === 'number' ? { pause: opts } : opts;
    const n = narrations.get(page);
    if (n) await n.finish();
    await page.evaluate((t) => {
        let el = document.getElementById('docs-caption');
        if (!el) {
            el = document.createElement('div');
            el.id = 'docs-caption';
            Object.assign(el.style, {
                position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)',
                maxWidth: '78%', padding: '12px 22px', borderRadius: '16px',
                background: 'rgba(251,242,211,.97)', color: '#4A3A00', border: '1.5px solid #EAD9A0',
                font: '500 17px/1.45 Geist, system-ui, sans-serif', textAlign: 'center',
                boxShadow: '0 6px 24px rgba(28,27,34,.14)', zIndex: '2147483646', pointerEvents: 'none',
                transition: 'opacity .25s ease',
            });
            document.body.appendChild(el);
        }
        el.textContent = t;
        el.style.opacity = t ? '1' : '0';
    }, text);
    if (n && say) n.say(say);
    if (pause) await beat(page, pause);
}
