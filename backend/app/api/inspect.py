"""Page inspection for the Test Builder's element picker.

POST /api/inspect/page {url} renders the page in headless Chromium and returns
a full-page screenshot plus a map of visible elements with pre-computed
selectors. The frontend shows the screenshot; the user clicks an element and
its selector is filled into the step — no cross-origin iframe problems, and
JS-rendered SPAs work because this is the real rendered DOM.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import AuthPrincipal, get_current_principal

router = APIRouter()

MAX_CAPTURE_HEIGHT = 4000  # px — cap screenshots of infinite-scroll pages
VIEWPORT = {"width": 1280, "height": 800}

# Runs inside the page: collect visible elements with a robust, unique
# selector each. Priority: data-testid > id > name > compact CSS path.
_COLLECT_ELEMENTS_JS = """
() => {
  const esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : v;

  function unique(sel) {
    try { return document.querySelectorAll(sel).length === 1; }
    catch { return false; }
  }

  function selectorFor(el) {
    if (el.dataset && el.dataset.testid) {
      const s = `[data-testid="${el.dataset.testid}"]`;
      if (unique(s)) return s;
    }
    if (el.id) {
      const s = `#${esc(el.id)}`;
      if (unique(s)) return s;
    }
    if (el.name && ['INPUT','SELECT','TEXTAREA'].includes(el.tagName)) {
      const s = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
      if (unique(s)) return s;
    }
    const aria = el.getAttribute('aria-label');
    if (aria) {
      const s = `${el.tagName.toLowerCase()}[aria-label="${aria}"]`;
      if (unique(s)) return s;
    }
    // Compact structural path, anchored at the nearest id if one exists.
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 6; depth++) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(`#${esc(node.id)}`); break; }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
      if (parts.length && unique(parts.join(' > '))) break;
    }
    return parts.join(' > ');
  }

  const out = [];
  const all = document.querySelectorAll('*');
  for (const el of all) {
    if (out.length >= 4000) break;
    const tag = el.tagName.toLowerCase();
    if (['script','style','svg','path','meta','link','head','html','body'].includes(tag)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
    const x = rect.left + window.scrollX;
    const y = rect.top + window.scrollY;
    if (y > %(max_height)d) continue;
    // Skip pure layout wrappers (unidentifiable divs/spans/sections with no
    // text of their own) — they burn the element budget without ever being a
    // useful pick, and the budget must reach the bottom of the page.
    const ownText = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3)
      .map(n => n.textContent.trim())
      .join(' ').trim();
    const identified = el.id || (el.dataset && el.dataset.testid) ||
      el.getAttribute('aria-label') || el.getAttribute('role') || el.onclick;
    if (['div','span','section','ul','li','header','footer','nav','main','article','aside','table','tbody','tr','td'].includes(tag)
        && !identified && !ownText) continue;
    const text = (ownText || el.innerText || el.value || '').trim().slice(0, 60);
    out.push({
      selector: selectorFor(el),
      tag,
      text,
      x: Math.round(x), y: Math.round(y),
      width: Math.round(rect.width), height: Math.round(rect.height),
    });
  }
  return out;
}
""" % {"max_height": MAX_CAPTURE_HEIGHT}


class InspectRequest(BaseModel):
    url: str


@router.post("/inspect/page")
async def inspect_page(
    body: InspectRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    # SSRF guard: this endpoint renders the URL server-side and returns the
    # screenshot + DOM, so without validation any authenticated principal (incl.
    # a low-privilege API key) could read cloud metadata or internal services.
    from app.core.net_guard import validate_outbound_url, UnsafeUrlError
    try:
        await validate_outbound_url(url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"Refusing to inspect this URL: {exc}")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise HTTPException(status_code=501, detail="Playwright is not installed in the backend image")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport=VIEWPORT)
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                # Give SPAs a moment to render; ignore pages that never go idle.
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:  # noqa: BLE001
                    pass

                elements = await page.evaluate(_COLLECT_ELEMENTS_JS)
                page_height = await page.evaluate("document.documentElement.scrollHeight")
                capture_height = min(int(page_height or VIEWPORT["height"]), MAX_CAPTURE_HEIGHT)
                # full_page=True is required for the clip to extend beyond the
                # viewport — without it Playwright silently clamps to 800px.
                shot = await page.screenshot(
                    clip={"x": 0, "y": 0, "width": VIEWPORT["width"], "height": capture_height},
                    full_page=True,
                    animations="disabled",
                    caret="hide",
                    timeout=30000,
                )
            finally:
                await browser.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not render {url}: {exc}")

    return {
        "url": url,
        "screenshot": base64.b64encode(shot).decode(),
        "width": VIEWPORT["width"],
        "height": capture_height,
        "elements": elements,
    }
