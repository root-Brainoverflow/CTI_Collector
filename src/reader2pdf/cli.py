# src/reader2pdf/cli.py

from __future__ import annotations

import typer
import asyncio
from typing import Callable
from pathlib import Path
from .constants import DEFAULT_TIMEOUT_S
from .utils import read_url_lines, sha256_hex
from .browser_async import launch_browser, close_browser, render_url_to_pdf_async
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TaskProgressColumn,
    MofNCompleteColumn,
)


app = typer.Typer(add_completion=False)


async def _worker(
    sem: asyncio.Semaphore,
    ctx,
    url: str,
    pdf_path: Path,
    timeout_s: int,
    retries: int,
    echo: Callable[[str], None],
    event_q: asyncio.Queue,
) -> None:
    """
    Worker task that processes a single URL with retries and concurrency control.
    """
    attempt = 0
    while True:
        attempt += 1
        async with sem:
            try:
                await render_url_to_pdf_async(ctx, url, pdf_path, timeout_s)
                # NOTE: report success
                await event_q.put(("ok", url, None))
                return
            except Exception as exc:
                if attempt <= retries + 1:
                    # echo(f"    [!] retry {attempt - 1}/{retries} after error: {exc}")
                    await asyncio.sleep(min(2 * attempt, 5))
                else:
                    # final failure counts as "processed" too
                    await event_q.put(("fail", url, str(exc)))
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
    """
    Main async runner.
    """
    urls = read_url_lines(url_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    browser, ctx = await launch_browser()
    try:
        sem = asyncio.Semaphore(max(1, max_concurrency))

        # NOTE: UI widgets
        total = len(urls)
        processed_lines: list[str] = []

        progress = Progress(
            TextColumn("[bold]Overall job status[/bold]"),
            BarColumn(),
            TaskProgressColumn(),  # shows percentage like "50%"
            TextColumn("•"),
            MofNCompleteColumn(),  # "200/400"
            TextColumn("URLs processed"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn(" ETA "),
            TimeRemainingColumn(),
            expand=True,
        )
        task_id = progress.add_task("render", total=total)

        def _render_ui() -> Group:
            """
            Render the live UI panel.
            """
            # newest entries will naturally appear at the bottom as we append
            body = (
                "\n".join(processed_lines)
                if processed_lines
                else "[dim]No URLs processed yet...[/dim]"
            )
            return Group(
                progress,
                Panel(
                    body, title="Processed URLs", border_style="green", padding=(1, 1)
                ),
            )

        # event queue for workers -> UI
        event_q: asyncio.Queue = asyncio.Queue()

        # launch workers
        tasks = []
        for url in urls:
            pdf_path = out_dir / f"{sha256_hex(url)}.pdf"
            # typer.echo(f"[+] {url} -> {pdf_path}")
            tasks.append(
                _worker(
                    sem, ctx, url, pdf_path, timeout_s, retries, typer.echo, event_q
                )
            )

        # run workers and UI together
        async def ui_loop() -> None:
            """
            UI loop that updates the live display based on events from workers.
            """
            completed = 0
            # drive the live display
            with Live(_render_ui(), refresh_per_second=8, transient=False) as live:
                with progress:
                    while completed < total:
                        status, url, err = await event_q.get()
                        completed += 1
                        progress.update(task_id, advance=1)

                        if status == "ok":
                            processed_lines.append(f"[green]✓[/green] {url}")
                        else:
                            processed_lines.append(
                                f"[red]✗[/red] {url}  [dim]{err}[/dim]"
                            )

                        live.update(_render_ui())

        await asyncio.gather(asyncio.create_task(ui_loop()), *tasks)

    finally:
        await close_browser(browser, ctx)


@app.command(help="Install Chromium browser for Playwright (one-time).")
def install_browser() -> None:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    typer.echo("Chromium installed for Playwright.")
