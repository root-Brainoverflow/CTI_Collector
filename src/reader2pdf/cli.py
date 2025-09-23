# src/reader2pdf/cli.py

from __future__ import annotations

import typer
import asyncio
from typing import Callable
from pathlib import Path
from .constants import DEFAULT_TIMEOUT_S
from .utils import read_url_lines, sha256_hex
from .browser_async import launch_browser, close_browser, render_url_to_pdf_async
from rich.live import Live
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
    SpinnerColumn,
)
from rich.table import Table

app = typer.Typer(add_completion=False)


async def _worker(
    sem: asyncio.Semaphore,
    ctx,
    url: str,
    pdf_path: Path,
    timeout_s: int,
    retries: int,
    echo: Callable[[str], None],
) -> None:
    """
    Worker coroutine to render a single URL to PDF with retries and semaphore control.
    """
    attempt = 0
    while True:
        attempt += 1
        async with sem:
            try:
                await render_url_to_pdf_async(ctx, url, pdf_path, timeout_s)
                # echo(f"    [ok] {url}")
                return
            except Exception as _:
                if attempt <= retries + 1:
                    # echo(f"    [!] retry {attempt - 1}/{retries} after error: {exc}")
                    await asyncio.sleep(min(2 * attempt, 5))  # tiny backoff
                else:
                    # echo(f"    [x] Failed after {retries} retries: {exc}")
                    return


@app.command(help="Process URLs concurrently, save PDFs as sha256(url).pdf")
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
    max_concurrency: int = typer.Option(
        6, "--max-concurrency", "-c", help="Max concurrent pages"
    ),
    retries: int = typer.Option(
        1, "--retries", "-r", help="Retries per URL on failure"
    ),
) -> None:
    """
    Thin sync wrapper that runs the async pipeline.
    """
    asyncio.run(_run_async(url_file, out_dir, timeout_s, max_concurrency, retries))


async def _run_async(
    url_file: Path,
    out_dir: Path,
    timeout_s: int,
    max_concurrency: int,
    retries: int,
) -> None:
    urls = read_url_lines(url_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    # NOTE: Rich progress bar setup
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]Overall"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        MofNCompleteColumn(),  # "200/400"
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        transient=False,  # keep bar after finish
    )
    now_tbl = Table(show_edge=False, box=None)
    now_tbl.add_column("Now processing", style="cyan", no_wrap=True)

    def render_now(items: list[str]) -> Table:
        """
        Render the "Now processing" table, while preserving layout.
        """
        tbl = Table(show_edge=False, box=None)
        tbl.add_column("Now processing", style="cyan", no_wrap=True)
        for it in items:
            tbl.add_row(it)
        return tbl

    browser, ctx = await launch_browser()
    try:
        sem = asyncio.Semaphore(max(1, max_concurrency))
        total = len(urls)
        in_flight: list[str] = []

        # NOTE: Using Live to manage dynamic display of progress + current tasks
        with Live(progress, refresh_per_second=8, transient=False) as live:
            # Add a second panel under the bar that we update the "current processing" list
            task_id = progress.add_task("overall", total=total)

            async def run_one(url: str, idx: int) -> None:
                """
                Run one URL processing task, updating progress and live display.
                """
                label = f"{idx + 1}/{total} {url}"
                in_flight.append(label)
                # swap Live's renderable: progress + the table
                live.update(renderable=progress)  # ensure progress stays visible
                live.console.print()  # spacer above table (optional)
                live.console.print(render_now(in_flight))

                try:
                    await _worker(
                        sem,
                        ctx,
                        url,
                        out_dir / f"{sha256_hex(url)}.pdf",
                        timeout_s,
                        retries,
                        lambda s: live.console.log(s),
                    )
                finally:
                    # remove from "now processing", advance bar
                    if label in in_flight:
                        in_flight.remove(label)
                    progress.advance(task_id)
                    # refresh the table view
                    live.console.print(render_now(in_flight))

            await asyncio.gather(*(run_one(url, i) for i, url in enumerate(urls)))

            progress.update(task_id, advance=0)  # final paint

    finally:
        await close_browser(browser, ctx)


@app.command(help="Install Chromium browser for Playwright (one-time).")
def install_browser() -> None:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    typer.echo("Chromium installed for Playwright.")
