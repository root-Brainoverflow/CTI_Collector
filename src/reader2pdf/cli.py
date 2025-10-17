# src/reader2pdf/cli.py

from __future__ import annotations

import typer
import asyncio
from typing import Callable
from pathlib import Path
import re
import os
from .constants import DEFAULT_TIMEOUT_S
from .utils import read_url_lines, sha256_hex
from .browser_async import launch_browser, close_browser, render_url_to_pdf_async
from rich.console import Console, Group
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

console = Console()


def sanitize_filename(title: str) -> str:
    """
    제목을 안전한 파일명으로 변환합니다.
    """
    # 불법 문자 제거 및 공백을 언더스코어로 변경
    sanitized = re.sub(r'[<>:"/\\|?*]', '', title)
    sanitized = re.sub(r'\s+', '_', sanitized.strip())
    # 파일명 길이 제한 (확장자 제외 최대 200자)
    if len(sanitized) > 200:
        sanitized = sanitized[:200]
    return sanitized if sanitized else "untitled"


async def get_page_title(ctx, url: str) -> str:
    """
    페이지의 제목을 추출합니다.
    """
    try:
        page = await ctx.new_page()
        await page.goto(url, timeout=30000)
        title = await page.title()
        await page.close()
        return title.strip() if title else "untitled"
    except Exception:
        return "untitled"


async def _worker(
    sem: asyncio.Semaphore,
    ctx,
    url: str,
    out_dir: Path,
    timeout_s: int,
    retries: int,
    echo: Callable[[str], None],
    event_q: asyncio.Queue,
) -> None:
    """
    Worker task that processes a single URL with retries and concurrency control.
    """
    attempt = 0
    temp_pdf_path = out_dir / f"temp_{sha256_hex(url)}.pdf"

    while True:
        attempt += 1
        async with sem:
            try:
                # PDF 생성
                await render_url_to_pdf_async(ctx, url, temp_pdf_path, timeout_s)

                # 페이지 제목 추출
                title = await get_page_title(ctx, url)
                safe_title = sanitize_filename(title)

                # 최종 파일명 생성 (중복 방지)
                final_pdf_path = out_dir / f"{safe_title}.pdf"
                counter = 1
                while final_pdf_path.exists():
                    final_pdf_path = out_dir / f"{safe_title}_{counter}.pdf"
                    counter += 1

                # 임시 파일을 최종 파일명으로 이동
                os.rename(temp_pdf_path, final_pdf_path)

                # NOTE: report success
                await event_q.put(("ok", url, str(final_pdf_path.name)))
                return
            except Exception as exc:
                # 임시 파일 정리
                if temp_pdf_path.exists():
                    temp_pdf_path.unlink()

                if attempt <= retries + 1:
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
        processed_lines: list[str] = []  # we’ll store ALL; we’ll render only the tail

        progress = Progress(
            TextColumn("[bold]Overall[/bold]"),
            BarColumn(),
            TaskProgressColumn(),  # "50%"
            TextColumn("•"),
            MofNCompleteColumn(),  # "200/400"
            TextColumn("processed"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn(" ETA "),
            TimeRemainingColumn(),
            expand=True,
        )
        task_id = progress.add_task("render", total=total)

        def _render_ui():
            # compute how many lines can fit above the progress bar
            # leave some rows for borders/title and the progress bar itself
            h = console.size.height
            reserved_rows = 7  # ~2 for panel borders/title, ~3-4 for progress + spacing
            tail_cap = max(3, h - reserved_rows)

            # take the tail that fits, and show an ellipsis count if older lines exist
            over = max(0, len(processed_lines) - tail_cap)
            tail = processed_lines[-tail_cap:] if processed_lines else []
            if over > 0:
                head = f"[dim]… {over} older processed entries hidden …[/dim]"
                body_lines = [head, *tail]
            else:
                body_lines = tail

            body = (
                "\n".join(body_lines)
                if body_lines
                else "[dim]No URLs processed yet...[/dim]"
            )

            # IMPORTANT: progress last => stays at the bottom
            return Group(
                Panel(
                    body,
                    title="Processed (latest at bottom)",
                    border_style="green",
                    padding=(0, 1),
                ),
                progress,
            )

        # event queue for workers -> UI
        event_q: asyncio.Queue = asyncio.Queue()

        # launch workers
        tasks = []
        for url in urls:
            # PDF 경로는 worker 내에서 결정되도록 변경
            tasks.append(
                _worker(
                    sem, ctx, url, out_dir, timeout_s, retries, typer.echo, event_q
                )
            )

        # run workers and UI together
        async def ui_loop() -> None:
            """
            UI loop that updates the live display based on events from workers.
            """
            completed = 0

            try:
                # Drive the live display (which renders the Panel + progress)
                with Live(_render_ui(), refresh_per_second=8, transient=False) as live:
                    while completed < total:
                        status, url, result = await event_q.get()
                        completed += 1
                        progress.update(task_id, advance=1)

                        if status == "ok":
                            processed_lines.append(f"[green]✓[/green] {url}")
                        else:
                            processed_lines.append(
                                f"[red]✗[/red] {url}  [dim]{result}[/dim]"
                            )

                        # Re-render tail + progress at the bottom
                        live.update(_render_ui())
            finally:
                pass

        await asyncio.gather(asyncio.create_task(ui_loop()), *tasks)

    finally:
        await close_browser(browser, ctx)


@app.command(help="Install Chromium browser for Playwright (one-time).")
def install_browser() -> None:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    typer.echo("Chromium installed for Playwright.")
