# src/reader2pdf/browser.py
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    BrowserContext,
    TimeoutError as PWTimeoutError,
)

from .constants import VIEWPORT, PDF_MARGIN
from .readability import load_readability_js


def _new_context(play) -> BrowserContext:
    """
    Create a new browser context with predefined settings.
    """
    browser = play.chromium.launch(headless=True)
    return browser.new_context(viewport=VIEWPORT, java_script_enabled=True)


def render_url_to_pdf(url: str, pdf_path: Path, timeout_s: int) -> None:
    """
    Render a web page to a PDF using Playwright and Readability.js.
    This function fetches the page, processes it to extract the main content,
    and saves it as a PDF.
    """
    with sync_playwright() as p:
        ctx = _new_context(p)  # can leave bypass_csp=False here
        src = ctx.new_page()
        try:
            src.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            try:
                src.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeoutError:
                pass

            # 1) Pull the fully-hydrated HTML as a string
            html = src.content()
        finally:
            src.close()

        # 2) Process in a sandbox page we control
        proc = ctx.new_page()
        try:
            # blank doc, no CSP(Content Security Policy) that might block our script
            proc.set_content("<!doctype html><meta charset='utf-8'><title>proc</title>")
            # Inject our vendored Readability.js
            proc.add_script_tag(content=load_readability_js())

            # 3) Parse the grabbed HTML in this clean context
            article = proc.evaluate(
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
                # 4) Render final clean HTML with <base> so images resolve
                from .html import render_article_html

                clean = render_article_html(
                    title=article.get("title") or "Untitled",
                    content_html=f"<base href='{url}'>" + article["content"],
                    source_url=url,
                )
                proc.set_content(clean, wait_until="load")

            # 5) Print to PDF from sandbox page
            proc.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin=PDF_MARGIN,  # type: ignore[arg-type]
            )
        finally:
            proc.close()
            ctx.close()
            ctx.browser.close()  # type: ignore[attr-defined]
