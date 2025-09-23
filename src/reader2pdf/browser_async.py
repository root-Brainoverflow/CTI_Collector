# src/reader2pdf/browser_async.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

from .constants import VIEWPORT, PDF_MARGIN
from .readability import load_readability_js
from .html import render_article_html


async def launch_browser() -> tuple[Browser, BrowserContext]:
    """
    Launch Chromium and create a context suitable for concurrent pages.
    """
    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-features=LazyImageLoading,LazyFrameLoading"
        ],  # Force lazy loading to be off
    )
    ctx = await browser.new_context(
        viewport=VIEWPORT,  # type: ignore[arg-type]
        java_script_enabled=True,
    )
    setattr(ctx, "_playwright", p)
    return browser, ctx


async def close_browser(browser: Browser, ctx: BrowserContext) -> None:
    """
    Close the asynchronously used browser and context.
    """
    try:
        await ctx.close()
    finally:
        await browser.close()
        # Stop Playwright driver
        p = getattr(ctx, "_playwright", None)
        if p:
            await p.stop()


async def _read_source_html(page: Page, url: str, timeout_s: int) -> str:
    """
    Navigate to the URL and return the page content.
    This does not wait for full load, just DOMContentLoaded and network idle.
    """
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PWTimeoutError:
        pass
    return await page.content()


async def _readerize_in_sandbox(ctx: BrowserContext, url: str, html: str) -> Page:
    """
    Create a clean page in our origin-less sandbox, inject Readability, parse,
    and replace with our minimal printable HTML (with <base>).
    Returns the prepared Page ready for PDF generation.
    """
    proc = await ctx.new_page()
    await proc.set_content("<!doctype html><meta charset='utf-8'><title>proc</title>")
    await proc.add_script_tag(content=load_readability_js())

    article: Optional[Dict[str, Any]] = await proc.evaluate(
        """
        (html) => {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const rd = new Readability(doc);
            try { return rd.parse(); } catch { return null; }
        }
        """,
        html,
    )

    if article and article.get("content"):
        clean = render_article_html(
            title=article.get("title") or "Untitled",
            content_html="<base href='%s'>%s" % (url, article["content"]),
            source_url=url,
        )
        await proc.set_content(clean, wait_until="load")

        # Make images eager, trigger loading, and wait for decode before printing
        await proc.evaluate(
            """
            (async () => {
              // 1) De-lazy images
              for (const img of document.querySelectorAll('img')) {
                try {
                  // Make sure the browser fetches them now
                  img.loading = 'eager';
                  img.decoding = 'sync';
                  // Common lazy patterns we might still see even after Readability:
                  if (!img.getAttribute('src')) {
                    const ds = img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-url');
                    if (ds) img.setAttribute('src', ds);
                  }
                } catch {}
              }

              // 2) Nudge viewport so intersection/viewport-based lazy-loaders fire
              window.scrollTo(0, document.body.scrollHeight);
              await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
              window.scrollTo(0, 0);

              // 3) Wait for all images to finish loading/decoding
              const imgs = Array.from(document.images);
              await Promise.all(imgs.map(img => {
                if (img.complete && img.naturalWidth > 0) return Promise.resolve();
                return (img.decode ? img.decode() : Promise.resolve()).catch(() => {});
              }));
            })();
            """
        )

        # Optional: also wait for any last network fetches
        try:
            await proc.wait_for_load_state("networkidle", timeout=10_000)
        except PWTimeoutError:
            pass

    # else: fall back to the empty sandbox page; PDF will still be produced
    return proc


async def render_url_to_pdf_async(
    ctx: BrowserContext, url: str, pdf_path: Path, timeout_s: int
) -> None:
    """
    Concurrent-safe rendering routine:
      - open a source page -> grab HTML
      - parse in sandbox -> set minimal HTML
      - print to PDF
    """
    src = await ctx.new_page()
    try:
        html = await _read_source_html(src, url, timeout_s)
    finally:
        await src.close()

    proc = await _readerize_in_sandbox(ctx, url, html)
    try:
        await proc.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin=PDF_MARGIN,  # type: ignore[arg-type]
        )
    finally:
        await proc.close()
