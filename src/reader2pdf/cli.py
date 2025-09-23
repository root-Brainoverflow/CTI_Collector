# src/reader2pdf/cli.py

from __future__ import annotations

import typer
from pathlib import Path
from .constants import DEFAULT_TIMEOUT_S
from .utils import read_url_lines, sha256_hex
from .browser import render_url_to_pdf

app = typer.Typer(add_completion=False)


@app.command(help="Process URLs line-by-line, save PDFs as sha256(url).pdf")
def run(
    url_file: Path = typer.Option(
        ..., "--url-file", "-i", help="Text file with one URL per line"
    ),
    out_dir: Path = typer.Option(
        Path("output"), "--out-dir", "-o", help="Output directory"
    ),
    timeout_s: int = typer.Option(
        DEFAULT_TIMEOUT_S, help="Per-URL navigation timeout (seconds)"
    ),
) -> None:
    urls = read_url_lines(url_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Sequentially process each URL
    for url in urls:
        pdf_path = out_dir / f"{sha256_hex(url)}.pdf"
        typer.echo(f"[+] {url} -> {pdf_path}")
        try:
            render_url_to_pdf(url, pdf_path, timeout_s)
        except Exception as exc:
            typer.echo(f"    [x] Failed: {exc}")


@app.command(help="Install Chromium browser for Playwright (one-time).")
def install_browser() -> None:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    typer.echo("Chromium installed for Playwright.")
