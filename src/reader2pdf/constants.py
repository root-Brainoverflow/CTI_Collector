# src/reader2pdf/constants.py
from __future__ import annotations

# Minimal CSS for better readability
MINIMAL_CSS: str = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Ubuntu, Cantarell, 'Helvetica Neue', Arial, sans-serif;
       line-height: 1.6; max-width: 780px; margin: 2.5rem auto; padding: 0 1rem; }
h1 { line-height: 1.25; font-size: 1.8rem; margin: 0 0 1rem; }
article img, article video, article figure { max-width: 100%; height: auto; }
article pre, article code { white-space: pre-wrap; word-break: break-word; }
a { text-decoration: none; }
hr { border: none; border-top: 1px solid #ccc; margin: 2rem 0; }
header { margin-bottom: 1.25rem; color: #666; font-size: 0.9rem; }
footer { margin-top: 2rem; font-size: 0.8rem; color: #888; }
"""

VIEWPORT = {"width": 1280, "height": 2000}
DEFAULT_TIMEOUT_S: int = 45
PDF_MARGIN = {"top": "12mm", "right": "12mm", "bottom": "16mm", "left": "12mm"}
