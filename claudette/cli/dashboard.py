"""Rich TUI dashboard for `claudette watch`.

Shows real state only — no guessing, no fake pipeline stages.
Reads: manager_session.json, session logs, metrics.json, worktree dirs, process status.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from claudette.core.config import Config

# ── Color scheme ──────────────────────────────────────────────────────────

GREEN = "#00ff00"
CYAN = "cyan"
YELLOW = "yellow"
RED = "red"
DIM = "dim"


def _is_process_alive(pid: int) -> bool:
    """Check whether a process is alive (not zombie, not dead)."""
    try:
        status_file = Path(f"/proc/{pid}/status")
        if status_file.exists():
            for line in status_file.read_text().splitlines():
                if line.startswith("State:"):
                    return "Z" not in line  # Z = zombie
            return True
        # /proc not available (macOS) — fall back to kill(0) + waitpid
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_session_info(state_dir: Path) -> dict:
    """Read manager_session.json."""
    session_file = state_dir / "manager_session.json"
    if not session_file.exists():
        return {}
    try:
        return json.loads(session_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _read_metrics(state_dir: Path) -> dict:
    """Read metrics.json if it exists."""
    metrics_file = state_dir / "metrics.json"
    if not metrics_file.exists():
        return {}
    try:
        return json.loads(metrics_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _read_latest_log(log_dir: Path, max_lines: int = 30) -> tuple[list[str], float]:
    """Read the most recent session log. Returns (lines, last_modified_timestamp)."""
    session_log_dir = log_dir / "sessions"
    if not session_log_dir.exists():
        return [], 0.0

    log_files = sorted(session_log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    if not log_files:
        return [], 0.0

    latest = log_files[-1]
    try:
        mtime = latest.stat().st_mtime
        text = latest.read_text(errors="replace")
        lines = text.strip().splitlines()
        return lines[-max_lines:], mtime
    except OSError:
        return [], 0.0


def _list_worktrees(worktree_dir: Path) -> list[dict]:
    """List active worktrees with basic info."""
    if not worktree_dir.exists():
        return []
    results = []
    for entry in sorted(worktree_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Check if it's a valid git worktree
        git_file = entry / ".git"
        if not git_file.exists():
            continue
        results.append({
            "name": entry.name,
            "path": str(entry),
        })
    return results


def _format_elapsed(seconds: float) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 0:
        return "--"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def _format_ago(timestamp: float) -> str:
    """Format a unix timestamp as '3s ago', '2m ago', etc."""
    if timestamp <= 0:
        return "--"
    ago = time.time() - timestamp
    if ago < 0:
        return "just now"
    if ago < 60:
        return f"{int(ago)}s ago"
    if ago < 3600:
        return f"{int(ago // 60)}m ago"
    return f"{int(ago // 3600)}h {int((ago % 3600) // 60)}m ago"


class Dashboard:
    """Rich TUI dashboard showing real claudette state."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.console = Console()
        self._tick = 0

    def _make_header(self, session_alive: bool, log_active: bool) -> Panel:
        """Top bar with real status."""
        if session_alive and log_active:
            status = f"[bold {GREEN}]RUNNING[/bold {GREEN}]"
        elif session_alive:
            status = f"[{YELLOW}]RUNNING (no recent output)[/{YELLOW}]"
        else:
            status = "[dim]IDLE[/dim]"

        title_text = Text.from_markup(
            f"  [bold cyan]claudette[/bold cyan]  [dim]autonomous dev pipeline[/dim]"
            f"  session: {status}"
        )
        return Panel(title_text, style="cyan", height=3)

    def _make_session_panel(self, session: dict, session_alive: bool,
                            log_mtime: float) -> Panel:
        """Shows real session state: PID, uptime, issues, last activity."""
        lines: list[str] = []

        if not session:
            lines.append("[dim]No session recorded.[/dim]")
            return Panel(
                Text.from_markup("\n".join(lines)),
                title="[bold cyan]SESSION[/bold cyan]",
                border_style="cyan",
            )

        # PID + status
        pid = session.get("pid", "?")
        if session_alive:
            lines.append(f"  [cyan]PID:[/cyan]      [{GREEN}]{pid}[/{GREEN}] [green]alive[/green]")
        else:
            lines.append(f"  [cyan]PID:[/cyan]      [{RED}]{pid}[/{RED}] [red]dead[/red]")

        # Uptime
        started = session.get("started_at", "")
        if started:
            try:
                ts = datetime.fromisoformat(started)
                elapsed = (datetime.now(UTC) - ts).total_seconds()
                age = _format_elapsed(elapsed)
                lines.append(f"  [cyan]Started:[/cyan]  {ts.strftime('%H:%M:%S')} ({age} ago)")
            except ValueError:
                lines.append(f"  [cyan]Started:[/cyan]  {started}")

        # Last log activity (real heartbeat proxy)
        if log_mtime > 0:
            ago_str = _format_ago(log_mtime)
            ago_secs = time.time() - log_mtime
            if ago_secs < 30:
                color = GREEN
            elif ago_secs < 120:
                color = YELLOW
            else:
                color = RED
            lines.append(f"  [cyan]Last output:[/cyan] [{color}]{ago_str}[/{color}]")
        else:
            lines.append("  [cyan]Last output:[/cyan] [dim]no log[/dim]")

        # Log path
        log_path = session.get("log_path", "")
        if log_path:
            lines.append(f"  [cyan]Log:[/cyan]      [dim]{log_path}[/dim]")

        # Issues
        issues = session.get("issues_included", [])
        if issues:
            lines.append("")
            lines.append(f"  [cyan]Issues ({len(issues)}):[/cyan]")
            for issue in issues:
                lines.append(f"    {issue}")

        return Panel(
            Text.from_markup("\n".join(lines)),
            title="[bold cyan]SESSION[/bold cyan]",
            border_style="cyan",
        )

    def _make_worktrees_panel(self) -> Panel:
        """Shows active worktrees — real evidence of work in progress."""
        worktrees = _list_worktrees(self.config.worktree_dir)

        if not worktrees:
            content = Text.from_markup("  [dim]No active worktrees.[/dim]")
        else:
            lines: list[str] = []
            for wt in worktrees:
                lines.append(f"  [{GREEN}]●[/{GREEN}] {wt['name']}")
            content = Text.from_markup("\n".join(lines))

        return Panel(
            content,
            title=f"[bold cyan]WORKTREES ({len(worktrees)})[/bold cyan]",
            border_style="cyan",
        )

    def _make_repos_panel(self) -> Panel:
        """Shows configured repos with pause status."""
        lines: list[str] = []
        for repo in self.config.repositories:
            paused = repo.name in self.config.paused_repos
            if paused:
                lines.append(f"  [{YELLOW}]⏸[/{YELLOW}]  {repo.name} [dim](paused)[/dim]")
            else:
                lines.append(f"  [green]●[/green]  {repo.name} [dim]({repo.default_branch})[/dim]")

        return Panel(
            Text.from_markup("\n".join(lines))
            if lines
            else Text("  No repos configured.", style=DIM),
            title="[bold cyan]REPOS[/bold cyan]",
            border_style="cyan",
        )

    def _make_metrics_panel(self, metrics: dict) -> Panel:
        """Shows real metrics from metrics.json."""
        if not metrics:
            return Panel(
                Text.from_markup(
                    "  [dim]No metrics recorded yet.[/dim]\n"
                    "  [dim]Metrics populate after ticks run.[/dim]"
                ),
                title="[bold cyan]METRICS[/bold cyan]",
                border_style="cyan",
            )

        # Counters are plain ints: {"tick": 3, "error": 1, "pr_opened:owner/repo": 2}
        counters = metrics.get("counters", {})
        events = metrics.get("events", [])

        total_ticks = counters.get("tick", 0)
        total_sessions = counters.get("session_launched", 0)
        total_errors = counters.get("error", 0)
        prs_opened = counters.get("pr_opened", 0)
        prs_merged = counters.get("pr_merged", 0)

        # Merge rate
        rate = f"{int(100 * prs_merged / prs_opened)}%" if prs_opened > 0 else "n/a"

        # Uptime since first event
        uptime = "--"
        if events:
            first_ts = events[0].get("timestamp", "")
            if first_ts:
                try:
                    ft = datetime.fromisoformat(first_ts)
                    uptime = _format_elapsed(
                        (datetime.now(UTC) - ft).total_seconds()
                    )
                except ValueError:
                    pass

        lines = [
            f"  [cyan]Ticks:[/cyan]        {total_ticks}",
            f"  [cyan]Sessions:[/cyan]     {total_sessions}",
            f"  [cyan]PRs opened:[/cyan]   {prs_opened}",
            f"  [cyan]PRs merged:[/cyan]   {prs_merged}",
            f"  [cyan]Merge rate:[/cyan]   {rate}",
            f"  [cyan]Errors:[/cyan]       {total_errors}",
            f"  [cyan]Tracking since:[/cyan] {uptime}",
        ]

        return Panel(
            Text.from_markup("\n".join(lines)),
            title="[bold cyan]METRICS[/bold cyan]",
            border_style="cyan",
        )

    def _make_log_panel(self, log_lines: list[str]) -> Panel:
        """Shows actual session log output — no parsing, no guessing."""
        if not log_lines:
            return Panel(
                Text.from_markup("  [dim]No session log output.[/dim]"),
                title="[bold cyan]SESSION LOG[/bold cyan]",
                border_style="cyan",
            )

        display_lines: list[str] = []
        for line in log_lines[-15:]:
            # Truncate long lines
            display = line[:140] + "…" if len(line) > 140 else line
            # Escape rich markup in log content
            display = display.replace("[", "\\[")
            display_lines.append(f"  [dim]{display}[/dim]")

        return Panel(
            Text.from_markup("\n".join(display_lines)),
            title="[bold cyan]SESSION LOG[/bold cyan]",
            border_style="cyan",
        )

    def _make_system_panel(self) -> Panel:
        """Shows system config — factual, no guessing."""
        cmd = self.config.llm.cmd_session.split()[0] if self.config.llm.cmd_session else "?"
        repos = ", ".join(r.name for r in self.config.repositories) or "none"
        mode = "dry-run" if self.config.system.dry_run else "live"
        relay = "on" if self.config.relay.enabled else "off"
        interval = f"{self.config.system.polling_interval_minutes}m"
        pipeline = "on" if self.config.pipeline.enabled else "off"

        lines = [
            f"  [cyan]CLI:[/cyan]       {cmd}",
            f"  [cyan]Mode:[/cyan]      {mode}",
            f"  [cyan]Relay:[/cyan]     {relay}",
            f"  [cyan]Pipeline:[/cyan]  {pipeline}",
            f"  [cyan]Interval:[/cyan]  {interval}",
            f"  [cyan]Repos:[/cyan]     {repos}",
        ]

        return Panel(
            Text.from_markup("\n".join(lines)),
            title="[bold cyan]SYSTEM[/bold cyan]",
            border_style="cyan",
        )

    def render(self) -> Layout:
        """Build the full dashboard from real state."""
        state_dir = self.config.state_dir
        log_dir = self.config.log_dir

        # Read real state
        session = _read_session_info(state_dir)
        metrics = _read_metrics(state_dir)
        log_lines, log_mtime = _read_latest_log(log_dir)

        # Real process check (zombie-aware)
        pid = session.get("pid")
        session_alive = bool(pid and _is_process_alive(pid))

        # Log activity: was the log file written to in the last 60s?
        log_active = (time.time() - log_mtime) < 60 if log_mtime > 0 else False

        # Build layout
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=3),
            Layout(name="footer", ratio=2),
        )

        # Header
        layout["header"].update(self._make_header(session_alive, log_active))

        # Body: session info (left) + sidebar (right)
        layout["body"].split_row(
            Layout(name="main", ratio=3),
            Layout(name="sidebar", ratio=2, minimum_size=35),
        )

        layout["main"].update(self._make_session_panel(session, session_alive, log_mtime))

        # Sidebar: worktrees + repos + metrics
        layout["sidebar"].split_column(
            Layout(name="worktrees", size=8),
            Layout(name="repos", size=6),
            Layout(name="metrics", ratio=1),
        )
        layout["sidebar"]["worktrees"].update(self._make_worktrees_panel())
        layout["sidebar"]["repos"].update(self._make_repos_panel())
        layout["sidebar"]["metrics"].update(self._make_metrics_panel(metrics))

        # Footer: log + system
        layout["footer"].split_row(
            Layout(name="log", ratio=3),
            Layout(name="system", ratio=1, minimum_size=30),
        )

        layout["footer"]["log"].update(self._make_log_panel(log_lines))
        layout["footer"]["system"].update(self._make_system_panel())

        return layout

    def run(self, interval: float = 2.0) -> None:
        """Run the live-updating dashboard loop."""
        try:
            with Live(
                self.render(),
                console=self.console,
                refresh_per_second=1,
                screen=True,
            ) as live:
                while True:
                    time.sleep(interval)
                    live.update(self.render())
        except KeyboardInterrupt:
            pass
