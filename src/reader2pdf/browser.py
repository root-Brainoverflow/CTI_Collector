# src/reader2pdf/browser.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from playwright.sync_api import (
    sync_playwright,
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

from .constants import VIEWPORT, PDF_MARGIN
from .html import render_article_html
from .readability import make_injection_script


def _new_context(play) -> BrowserContext:
    """
    Create a new browser context with predefined settings.
    """
    browser = play.chromium.launch(headless=True)
    return browser.new_context(viewport=VIEWPORT, java_script_enabled=True)


def _readerize(page: Page, url: str, timeout_s: int) -> None:
    """
    Use Readability.js to extract and render the main content of the page.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
    try:
        # Wait for network to be idle (no more than 2 connections for at least 500 ms)
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PWTimeoutError:
        pass

    page.evaluate(make_injection_script())
    page.wait_for_function("() => window.Readability !== undefined", timeout=10_000)

    # NOTE: Extract the article using Readability.js
    article: Optional[Dict] = page.evaluate(
        """
        () => {
          const doc = document.cloneNode(true);
          try {
            const reader = new Readability(doc);
            return reader.parse(); // {title, content, ...}
          } catch (_) {
            return null;
          }
        }
        """
    )

    # Render the article content into a clean HTML template
    if article and article.get("content"):
        clean_html = render_article_html(
            title=article.get("title") or "Untitled",
            content_html=article["content"],  # type: ignore[index]
            source_url=url,
        )
        page.set_content(clean_html, wait_until="load")


def render_url_to_pdf(url: str, pdf_path: Path, timeout_s: int) -> None:
    """
    Render the main content of a web page to a PDF file.
    """
    with sync_playwright() as p:
        ctx = _new_context(p)
        page = ctx.new_page()
        try:
            _readerize(page, url, timeout_s)
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin=PDF_MARGIN,  # type: ignore[arg-type]
            )
        finally:
            page.close()
            ctx.close()
            ctx.browser.close()  # type: ignore[attr-defined]
