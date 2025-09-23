# src/reader2pdf/readability.py
from __future__ import annotations

from importlib.resources import files
from typing import Final

_ASSET: Final[str] = "assets/Readability.js"


def load_readability_js() -> str:
    """
    Return the vendored Readability.js source as a string.
    """
    data = files("reader2pdf").joinpath(_ASSET).read_text(encoding="utf-8")
    return data


def make_injection_script() -> str:
    """
    JS snippet that ensures Readability is available in page context.
    """
    code = load_readability_js()

    # We inject via a <script> tag to avoid CSP 'eval' issues.
    return f"""
(() => {{
    if (!window.Readability) {{
        const s = document.createElement('script');
        s.type = 'text/javascript';
        s.text = {code!r};
        document.documentElement.appendChild(s);
    }}
}})();
"""
