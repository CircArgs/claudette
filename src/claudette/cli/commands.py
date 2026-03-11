"""CLI command implementations.

Each function reads state/config, formats output with Rich, and exits.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.tree import Tree

from claudette.core.config import Config, _label_match, _primary_label

console = Console()


def _resolve_token() -> str:
    """Get GitHub token from env or gh CLI auth."""
    import os

    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _require_token() -> str:
    token = _resolve_token()
    if not token:
        console.print("[red]No GitHub token. Set GITHUB_TOKEN or run `gh auth login`.[/red]")
        sys.exit(1)
    return token


def _make_github_client(token: str):
    """Create the best available GitHub client.

    Prefers GhCliGitHubClient (uses `gh api`, no Python SSL) when the
    gh CLI is available.  Falls back to LiveGitHubClient (httpx).
    """
    try:
        result = subprocess.run(["gh", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            from claudette.core.gh_cli_client import GhCliGitHubClient

            return GhCliGitHubClient()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    from claudette.core.github_client import LiveGitHubClient

    return LiveGitHubClient(token)


def _fetch_all_issues(config: Config, token: str):
    """Fetch all open issues across configured repos."""
    gh = _make_github_client(token)
    all_issues = []
    for repo in config.repositories:
        all_issues.extend(gh.fetch_issues(repo.name, datetime(2000, 1, 1, tzinfo=UTC)))
    return all_issues


# ── Status (the hero command) ─────────────────────────────────────────────


BANNER = """\
[bold bright_magenta]  ╭─────╮[/]
[bold bright_magenta]  │ ◉ ◉ │[/]  [bold]claudette[/]
[bold bright_magenta]  │  ▽  │[/]  [dim]why is everything so hard[/]
[bold bright_magenta]  ╰─────╯[/]
"""


def cmd_refresh(config: Config) -> None:
    """Regenerate all derived files from current config."""
    from claudette.core.bootstrap import _ensure_labels, regenerate_agents_md
    from claudette.core.skills import install_skills

    # 1. Regenerate AGENTS.md + symlinks
    regenerate_agents_md(config)
    console.print("[green]✓[/green] AGENTS.md regenerated")

    # 2. Reinstall skills (skip when relay is enabled — docs are in AGENTS.md)
    if config.relay.enabled:
        console.print("[green]✓[/green] CLI docs injected into AGENTS.md (relay mode)")
    else:
        installed = install_skills(config.project_dir, scope="manager")
        console.print(f"[green]✓[/green] Skills installed: {', '.join(installed)}")

    # 3. Ensure labels exist on GitHub
    _ensure_labels(config)
    console.print("[green]✓[/green] GitHub labels ensured")

    # 4. Copy any new default prompt templates
    from claudette.core.bootstrap import _copy_default_prompts

    _copy_default_prompts(config.prompts_dir)
    console.print("[green]✓[/green] Prompt templates updated")

    console.print("\n[bold]Project refreshed.[/bold]")


def cmd_update() -> None:
    """Self-update claudette to the latest version."""
    import shutil
    import subprocess

    repo = "git+https://github.com/CircArgs/claudette.git"

    # Detect installer
    if shutil.which("uv"):
        installer = "uv"
        cmd = ["uv", "tool", "install", "--force", repo]
    elif shutil.which("pipx"):
        installer = "pipx"
        cmd = ["pipx", "install", "--force", f"claudette @ {repo}"]
    elif shutil.which("pip"):
        installer = "pip"
        cmd = ["pip", "install", "--upgrade", f"claudette @ {repo}"]
    elif shutil.which("pip3"):
        installer = "pip3"
        cmd = ["pip3", "install", "--upgrade", f"claudette @ {repo}"]
    else:
        console.print("[red]No package installer found (uv, pipx, or pip).[/red]")
        raise SystemExit(1)

    console.print(f"Updating claudette via {installer}...")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode == 0:
        console.print("[green]Updated successfully.[/green]")
    else:
        console.print("[red]Update failed.[/red]")
        raise SystemExit(result.returncode)


def cmd_status(config: Config) -> None:
    """One screenful of everything you need to know."""
    from claudette.core.dag import build_dag, get_blocked_issues, get_ready_issues

    console.print(BANNER)

    state = config.state_dir
    labels = config.github.labels

    # Header
    lock = state / "manager.lock"
    if lock.exists():
        console.print("[yellow]● Tick in progress[/yellow]", end="  ")
    else:
        console.print("[green]● Idle[/green]", end="  ")

    # Last tick
    last_tick = "never"
    last_tick_ts = None
    for repo in config.repositories:
        safe = repo.name.replace("/", "_")
        cursor = state / f"{safe}_sync.txt"
        if cursor.exists():
            try:
                ts = datetime.fromisoformat(cursor.read_text().strip())
                ago = (datetime.now(UTC) - ts).total_seconds()
                last_tick = f"{int(ago)}s ago" if ago < 60 else f"{int(ago // 60)}m ago"
                last_tick_ts = ts
            except ValueError:
                pass

    # Next tick
    from claudette.core.bootstrap import get_cron_status

    cron_line = get_cron_status(config)
    if cron_line and last_tick_ts is not None:
        interval_secs = config.system.polling_interval_minutes * 60
        next_at = last_tick_ts.timestamp() + interval_secs
        remaining = next_at - datetime.now(UTC).timestamp()
        if remaining <= 0:
            next_tick = "overdue"
        elif remaining < 60:
            next_tick = f"{int(remaining)}s"
        else:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            next_tick = f"{mins}m {secs}s"
        console.print(f"last tick: {last_tick}  next: {next_tick}")
    elif cron_line:
        console.print(f"last tick: {last_tick}  next: ≤{config.system.polling_interval_minutes}m")
    else:
        console.print(f"last tick: {last_tick}  next: [dim]cron not installed[/dim]")
    console.print()

    # Session
    session_file = state / "manager_session.json"
    if session_file.exists():
        try:
            s = json.loads(session_file.read_text())
            pid = s.get("pid", "?")
            started = s.get("started_at", "")
            issues = s.get("issues_included", [])
            try:
                elapsed = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
                time_str = (
                    f"running {int(elapsed // 60)}m"
                    if elapsed >= 60
                    else f"running {int(elapsed)}s"
                )
            except ValueError:
                time_str = "running"
            console.print(
                f"[bold]SESSION[/bold]  PID {pid}  {time_str}  issues: {', '.join(issues)}"
            )
        except (json.JSONDecodeError, OSError):
            console.print("[bold]SESSION[/bold]  [dim]unknown state[/dim]")
    else:
        console.print("[bold]SESSION[/bold]  [dim]none active[/dim]")
    console.print()

    # Fetch issues and build graph
    token = _resolve_token()
    if not token:
        console.print("[dim]Set GITHUB_TOKEN or run `gh auth login` to see queue.[/dim]")
        return

    try:
        all_issues = _fetch_all_issues(config, token)
        graph = build_dag(all_issues, config.github.dependency_pattern)
        blocked_map = get_blocked_issues(graph)
        get_ready_issues(graph)

        # Session issues
        session_issues: set[str] = set()
        if session_file.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                session_issues = set(
                    json.loads(session_file.read_text()).get("issues_included", [])
                )

        # Categorize
        working = []
        ready = []
        blocked = []
        waiting = []
        untriaged = []
        prs_review = []  # PRs that will be auto-reviewed
        prs_waiting = []  # PRs waiting for labels/action

        routing = config.github.routing
        auto_review = config.deterministic_rules.auto_review_new_prs
        for key, issue in graph.nodes.items():
            if issue.state == "closed":
                continue
            if any(lbl in issue.labels for lbl in routing.ignore_labels):
                continue

            if issue.is_pull_request:
                if _label_match(issue.labels, labels.needs_review):
                    prs_review.append((key, issue, "needs review"))
                elif auto_review and not _label_match(issue.labels, labels.in_progress):
                    prs_review.append((key, issue, "will auto-flag for review"))
                else:
                    prs_waiting.append((key, issue))
            elif key in session_issues or _label_match(issue.labels, labels.in_progress):
                working.append((key, issue))
            elif key in blocked_map:
                blockers = ", ".join(sorted(blocked_map[key]))
                blocked.append((key, issue, blockers))
            elif _label_match(issue.labels, labels.waiting_on_user):
                waiting.append((key, issue))
            elif routing.require_ready_label and not _label_match(
                issue.labels, labels.ready_for_dev
            ):
                untriaged.append((key, issue))
            else:
                ready.append((key, issue))

        def _print_section(title: str, icon: str, color: str, items: list) -> None:
            if not items:
                return
            console.print(f"[bold {color}]{icon} {title}[/bold {color}]")
            for item in items:
                key, issue = item[0], item[1]
                line = f"  {key:<35} {issue.title[:50]}"
                if len(item) > 2:
                    line += f"\n  {'':35} [dim]{item[2]}[/dim]"
                console.print(line)
            console.print()

        lbl_ip = _primary_label(labels.in_progress) or ""
        lbl_wait = _primary_label(labels.waiting_on_user) or ""
        lbl_block = _primary_label(labels.blocked) or ""
        lbl_ready = _primary_label(labels.ready_for_dev) or ""
        lbl_review = _primary_label(labels.needs_review) or ""

        _print_section(f"WORKING ({lbl_ip})", "◉", "blue", working)
        _print_section(f"WAITING ON YOU ({lbl_wait})", "◆", "yellow", waiting)
        _print_section(f"BLOCKED ({lbl_block})", "■", "red", blocked)
        _print_section(f"READY ({lbl_ready})", "●", "green", ready)
        _print_section(f"PULL REQUESTS ({lbl_review})", "⎇", "magenta", prs_review)
        _print_section("PRs (no action)", "⎇", "dim", prs_waiting)
        _print_section(
            f"UNTRIAGED (need `{lbl_ready}` label)",
            "○",
            "dim",
            untriaged,
        )

        if not any([working, waiting, blocked, ready, prs_review, prs_waiting, untriaged]):
            console.print("[dim]No open issues.[/dim]")

    except Exception as e:
        console.print(f"[red]Error fetching issues: {e}[/red]")


# ── Why ───────────────────────────────────────────────────────────────────


def cmd_why(config: Config, issue_ref: str) -> None:
    """Explain why an issue is in its current state."""
    from claudette.core.dag import build_dag, get_blocked_issues

    # Parse ref: "owner/repo#42" or just "42" or "#42"
    ref = issue_ref.strip().lstrip("#")
    if "#" in ref:
        repo_name, num_str = ref.rsplit("#", 1)
    else:
        num_str = ref
        repo_name = config.repositories[0].name if config.repositories else ""

    if not repo_name:
        console.print("[red]No repo specified and no default repo configured[/red]")
        sys.exit(1)

    try:
        number = int(num_str)
    except ValueError:
        console.print(f"[red]Invalid issue number: {num_str}[/red]")
        sys.exit(1)

    key = f"{repo_name}#{number}"
    token = _require_token()
    labels = config.github.labels
    state = config.state_dir

    all_issues = _fetch_all_issues(config, token)
    graph = build_dag(all_issues, config.github.dependency_pattern)
    blocked_map = get_blocked_issues(graph)

    issue = graph.nodes.get(key)
    if not issue:
        console.print(f"[red]{key} not found in any configured repo[/red]")
        sys.exit(1)

    console.print(f"[bold]{key}[/bold]  {issue.title}")
    console.print(f"  State: {issue.state}  Labels: {', '.join(issue.labels) or 'none'}")
    console.print()

    if issue.state == "closed":
        console.print("  [dim]Closed — nothing to do.[/dim]")
        return

    # Check if repo is paused
    if issue.repo in config.paused_repos:
        console.print(f"  [yellow]Repo {issue.repo} is paused.[/yellow]")
        return

    # Check session
    session_file = state / "manager_session.json"
    if session_file.exists():
        try:
            s = json.loads(session_file.read_text())
            if key in s.get("issues_included", []):
                pid = s.get("pid", "?")
                started = s.get("started_at", "?")
                console.print(
                    f"  [blue]◉ Being worked on right now[/blue] — "
                    f"Manager session PID {pid} (started {started})"
                )
                return
        except (json.JSONDecodeError, OSError):
            pass

    if _label_match(issue.labels, labels.in_progress):
        console.print("  [blue]◉ Labeled as in-progress.[/blue]")
        return

    if _label_match(issue.labels, labels.waiting_on_user):
        console.print("  [yellow]◆ Waiting on you.[/yellow]")
        return

    if key in blocked_map:
        console.print("  [red]■ Blocked by:[/red]")
        for dep_key in sorted(blocked_map[key]):
            dep = graph.nodes.get(dep_key)
            if dep:
                dep_status = "open"
                if _label_match(dep.labels, labels.in_progress):
                    dep_status = "in-progress"
                console.print(f"    {dep_key} ({dep_status}) — {dep.title}")
            else:
                console.print(f"    {dep_key} (unknown — not in any configured repo)")
        return

    console.print("  [green]● Ready to work — no blockers.[/green]")
    console.print("  It will be picked up on the next tick if no session is active.")


# ── Open ──────────────────────────────────────────────────────────────────


def cmd_open(config: Config, issue_ref: str) -> None:
    """Open an issue or PR in the browser."""
    ref = issue_ref.strip().lstrip("#")
    if "#" in ref:
        repo_name, num_str = ref.rsplit("#", 1)
    else:
        num_str = ref
        repo_name = config.repositories[0].name if config.repositories else ""

    if not repo_name:
        console.print("[red]No repo specified and no default repo configured[/red]")
        sys.exit(1)

    try:
        number = int(num_str)
    except ValueError:
        console.print(f"[red]Invalid issue number: {num_str}[/red]")
        sys.exit(1)

    url = f"https://github.com/{repo_name}/issues/{number}"
    console.print(f"[dim]Opening {url}[/dim]")
    webbrowser.open(url)


# ── Queue ─────────────────────────────────────────────────────────────────


def cmd_queue(
    config: Config,
    ready: bool = False,
    blocked: bool = False,
    waiting: bool = False,
) -> None:
    from claudette.core.dag import build_dag, get_blocked_issues, get_ready_issues

    token = _require_token()
    all_issues = _fetch_all_issues(config, token)
    graph = build_dag(all_issues, config.github.dependency_pattern)
    blocked_map = get_blocked_issues(graph)
    ready_keys = get_ready_issues(graph)
    show_all = not (ready or blocked or waiting)

    routing = config.github.routing
    labels = config.github.labels

    if show_all or ready:
        console.print("[bold green]● Ready[/bold green]")
        for key in ready_keys:
            issue = graph.nodes.get(key)
            if not issue or issue.state != "open" or issue.is_pull_request:
                continue
            if any(lbl in issue.labels for lbl in routing.ignore_labels):
                continue
            if routing.require_ready_label and labels.ready_for_dev not in issue.labels:
                continue
            console.print(f"  {key:<35} {issue.title[:50]}")
        console.print()

    if show_all or blocked:
        console.print("[bold red]■ Blocked[/bold red]")
        for key, blockers in blocked_map.items():
            issue = graph.nodes.get(key)
            if issue and issue.state == "open":
                console.print(f"  {key:<35} blocked by {', '.join(blockers)}")
        console.print()

    if show_all or waiting:
        console.print("[bold yellow]◆ Waiting on Human[/bold yellow]")
        for key, issue in graph.nodes.items():
            if issue.state == "open" and _label_match(
                issue.labels, config.github.labels.waiting_on_user
            ):
                console.print(f"  {key:<35} {issue.title[:50]}")
        console.print()


# ── Graph ─────────────────────────────────────────────────────────────────


def cmd_graph(config: Config, blocked_only: bool = False, repo: str | None = None) -> None:
    from claudette.core.dag import build_dag, get_blocked_issues

    token = _require_token()
    all_issues = _fetch_all_issues(config, token)
    graph = build_dag(all_issues, config.github.dependency_pattern)
    blocked_map = get_blocked_issues(graph)
    labels = config.github.labels

    tree = Tree("[bold]Dependency Graph[/bold]")
    shown: set[str] = set()

    routing = config.github.routing

    def _status_tag(key: str) -> str:
        issue = graph.nodes.get(key)
        if not issue or issue.state == "closed":
            return "[dim]closed[/dim]"
        if issue.is_pull_request:
            return "[magenta]PR[/magenta]"
        if _label_match(issue.labels, labels.in_progress):
            return "[blue]working[/blue]"
        if key in blocked_map:
            return "[red]blocked[/red]"
        if _label_match(issue.labels, labels.waiting_on_user):
            return "[yellow]waiting[/yellow]"
        if routing.require_ready_label and not _label_match(issue.labels, labels.ready_for_dev):
            return "[dim]untriaged[/dim]"
        return "[green]ready[/green]"

    def _add_node(parent, key: str) -> None:
        if key in shown:
            return
        issue = graph.nodes.get(key)
        if not issue or issue.state == "closed":
            return
        if repo and issue.repo != repo:
            return
        if (
            blocked_only
            and key not in blocked_map
            and not any(key in deps for deps in graph.edges.values())
        ):
            return

        shown.add(key)
        label = f"{key} {_status_tag(key)}  {issue.title[:50]}"
        node = parent.add(label)
        # Show dependents (nodes that this one blocks)
        for other_key, deps in graph.edges.items():
            if key in deps and other_key not in shown:
                other = graph.nodes.get(other_key)
                if other and other.state == "open":
                    _add_node(node, other_key)

    for key in graph.nodes:
        if key not in shown:
            _add_node(tree, key)

    console.print(tree)


# ── Tick ──────────────────────────────────────────────────────────────────


def cmd_tick(config: Config, dry_run: bool = False, extra_prompt: str | None = None) -> None:
    from claudette.core.budget import BudgetTracker
    from claudette.core.clock import SystemClock
    from claudette.core.llm_client import ClaudeCLIClient
    from claudette.core.poll import run_tick

    token = _require_token()
    state = config.state_dir
    gh = _make_github_client(token)
    llm = ClaudeCLIClient(config.llm, prompts_dir=config.prompts_dir)

    with console.status("Running tick..."):
        result = run_tick(
            github=gh,
            llm=llm,
            clock=SystemClock(),
            config=config,
            budget=BudgetTracker(state),
            state_dir=state,
            dry_run=dry_run,
            extra_prompt=extra_prompt,
        )

    if result.lock_failed:
        console.print("[yellow]Another tick is already running[/yellow]")
        sys.exit(1)
    if result.session_launched:
        console.print(f"[green]Launched manager session PID {result.session_pid}[/green]")
        console.print(f"  Issues: {', '.join(result.issues_in_prompt)}")
        if result.prs_in_prompt:
            console.print(f"  PRs for review: {', '.join(result.prs_in_prompt)}")
    if result.session_active:
        console.print("[yellow]Manager session already active, skipping[/yellow]")
    if result.dispatched_reviews:
        console.print(
            f"[blue]PRs flagged for review:[/blue] {', '.join(result.dispatched_reviews)}"
        )
    if result.newly_blocked:
        console.print(f"[yellow]Blocked:[/yellow] {', '.join(result.newly_blocked)}")
    if result.dry_run_actions:
        for action in result.dry_run_actions:
            console.print(f"[dim][DRY-RUN] {action}[/dim]")
    if result.warnings:
        for w in result.warnings:
            console.print(f"[yellow]Warning:[/yellow] {w}")
    if result.errors:
        for e in result.errors:
            console.print(f"[red]Error:[/red] {e}")
    if not any(
        [
            result.session_launched,
            result.session_active,
            result.dispatched_reviews,
            result.newly_blocked,
            result.dry_run_actions,
        ]
    ):
        console.print("[dim]Nothing to do[/dim]")


# ── Session ───────────────────────────────────────────────────────────────


def cmd_session(config: Config, follow: bool = False) -> None:
    session_file = config.state_dir / "manager_session.json"

    if not session_file.exists():
        console.print("[dim]No active manager session[/dim]")
        return

    try:
        session = json.loads(session_file.read_text())
    except (json.JSONDecodeError, OSError):
        console.print("[dim]No active manager session[/dim]")
        return

    pid = session.get("pid", "?")
    started = session.get("started_at", "?")
    issues = ", ".join(session.get("issues_included", []))
    log_path = session.get("log_path", "")

    console.print(f"PID {pid}  started {started}")
    console.print(f"Issues: {issues}")
    if log_path:
        console.print(f"Log: {log_path}")

    if follow and log_path and Path(log_path).exists():
        console.print(f"\n[dim]Tailing {log_path} (Ctrl+C to stop)[/dim]")
        with contextlib.suppress(KeyboardInterrupt):
            subprocess.run(["tail", "-f", log_path])


# ── Log ───────────────────────────────────────────────────────────────────


def cmd_log(
    config: Config,
    repo: str | None = None,
    issue: int | None = None,
    level: str | None = None,
) -> None:
    log_base = config.log_dir

    if repo:
        safe = repo.replace("/", "_")
        log_dirs = [log_base / safe]
    else:
        log_dirs = [d for d in log_base.iterdir() if d.is_dir()] if log_base.exists() else []

    entries: list[tuple[str, str]] = []
    for log_dir in log_dirs:
        if not log_dir.exists():
            continue
        for log_file in sorted(log_dir.glob("*.jsonl")):
            for line in log_file.read_text().splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if issue and entry.get("issue") != issue:
                    continue
                if level and entry.get("level") != level:
                    continue
                ts = entry.get("timestamp", "")
                e_repo = entry.get("repo", "")
                e_issue = entry.get("issue", "")
                action = entry.get("action", "")
                outcome = entry.get("outcome", "")
                entries.append((ts, f"[dim]{ts}[/dim] [{e_repo}#{e_issue}] {action} {outcome}"))

    entries.sort(key=lambda x: x[0])
    for _, text in entries[-50:]:
        console.print(text)

    if not entries:
        console.print("[dim]No log entries yet.[/dim]")


# ── Claim / Unclaim ───────────────────────────────────────────────────────


def _parse_issue_ref(config: Config, issue_ref: str) -> tuple[str, int]:
    """Parse 'owner/repo#42', '#42', or '42' into (repo_name, number)."""
    ref = issue_ref.strip().lstrip("#")
    if "#" in ref:
        repo_name, num_str = ref.rsplit("#", 1)
    else:
        num_str = ref
        repo_name = config.repositories[0].name if config.repositories else ""

    if not repo_name:
        console.print("[red]No repo specified and no default repo configured[/red]")
        sys.exit(1)

    try:
        number = int(num_str)
    except ValueError:
        console.print(f"[red]Invalid issue number: {num_str}[/red]")
        sys.exit(1)

    return repo_name, number


def cmd_claim(config: Config, issue_ref: str) -> None:
    """Mark an issue as claimed by the user so claudette won't touch it."""
    repo_name, number = _parse_issue_ref(config, issue_ref)
    token = _require_token()
    gh = _make_github_client(token)
    label = _primary_label(config.github.labels.in_progress)
    if not label:
        console.print("[red]No in-progress label configured[/red]")
        sys.exit(1)
    gh.apply_label(repo_name, number, label)
    console.print(f"[blue]Claimed {repo_name}#{number}[/blue] — claudette will skip it")


def cmd_unclaim(config: Config, issue_ref: str) -> None:
    """Release a claimed issue so claudette can pick it up."""
    repo_name, number = _parse_issue_ref(config, issue_ref)
    token = _require_token()
    gh = _make_github_client(token)
    label = _primary_label(config.github.labels.in_progress)
    if not label:
        console.print("[red]No in-progress label configured[/red]")
        sys.exit(1)
    gh.remove_label(repo_name, number, label)
    console.print(f"[green]Unclaimed {repo_name}#{number}[/green] — claudette can pick it up")


# ── Pause / Resume ────────────────────────────────────────────────────────


def cmd_pause(config: Config, repo_name: str) -> None:
    if not any(r.name == repo_name for r in config.repositories):
        console.print(f"[red]{repo_name} not in config[/red]")
        sys.exit(1)
    if repo_name in config.paused_repos:
        console.print(f"[yellow]{repo_name} is already paused[/yellow]")
        return
    config.paused_repos.append(repo_name)
    config.save()
    console.print(f"[yellow]Paused {repo_name}[/yellow]")


def cmd_resume(config: Config, repo_name: str) -> None:
    if repo_name not in config.paused_repos:
        console.print(f"[dim]{repo_name} is not paused[/dim]")
        return
    config.paused_repos.remove(repo_name)
    config.save()
    console.print(f"[green]Resumed {repo_name}[/green]")


# ── Label shortcuts ──────────────────────────────────────────────────────


def _apply_config_label(config: Config, issue_ref: str, label_value, action: str) -> None:
    """Helper: apply or remove a label from config onto an issue."""
    repo_name, number = _parse_issue_ref(config, issue_ref)
    label = _primary_label(label_value)
    if not label:
        console.print(f"[red]No {action} label configured[/red]")
        sys.exit(1)
    token = _require_token()
    gh = _make_github_client(token)
    gh.apply_label(repo_name, number, label)
    console.print(f"[green]Applied '{label}' to {repo_name}#{number}[/green]")


def _remove_config_label(config: Config, issue_ref: str, label_value, action: str) -> None:
    """Helper: remove a label from config from an issue."""
    repo_name, number = _parse_issue_ref(config, issue_ref)
    label = _primary_label(label_value)
    if not label:
        console.print(f"[red]No {action} label configured[/red]")
        sys.exit(1)
    token = _require_token()
    gh = _make_github_client(token)
    gh.remove_label(repo_name, number, label)
    console.print(f"[green]Removed '{label}' from {repo_name}#{number}[/green]")


def cmd_ready(config: Config, issue_ref: str) -> None:
    _apply_config_label(config, issue_ref, config.github.labels.ready_for_dev, "ready-for-dev")


def cmd_unready(config: Config, issue_ref: str) -> None:
    _remove_config_label(config, issue_ref, config.github.labels.ready_for_dev, "ready-for-dev")


def cmd_block(config: Config, issue_ref: str) -> None:
    _apply_config_label(config, issue_ref, config.github.labels.blocked, "blocked")


def cmd_unblock(config: Config, issue_ref: str) -> None:
    _remove_config_label(config, issue_ref, config.github.labels.blocked, "blocked")


def cmd_wait(config: Config, issue_ref: str) -> None:
    _apply_config_label(config, issue_ref, config.github.labels.waiting_on_user, "waiting-on-user")


def cmd_unwait(config: Config, issue_ref: str) -> None:
    _remove_config_label(config, issue_ref, config.github.labels.waiting_on_user, "waiting-on-user")


# ── Issue management ─────────────────────────────────────────────────────


def cmd_issue_create(
    config: Config,
    title: str,
    body: str = "",
    repo: str | None = None,
    ready: bool = False,
) -> None:
    """Create a new issue on GitHub."""
    repo_name = repo or (config.repositories[0].name if config.repositories else None)
    if not repo_name:
        console.print("[red]No repo specified and no default repo configured[/red]")
        sys.exit(1)

    token = _require_token()
    gh = _make_github_client(token)

    labels = []
    if ready:
        label = _primary_label(config.github.labels.ready_for_dev)
        if label:
            labels.append(label)

    issue = gh.create_issue(repo_name, title, body=body, labels=labels or None)
    console.print(f"[green]Created {repo_name}#{issue.number}[/green]  {issue.title}")
    console.print(f"  https://github.com/{repo_name}/issues/{issue.number}")


def cmd_issue_depends(config: Config, issue_ref: str, on_ref: str) -> None:
    """Add a dependency between two issues."""
    repo_name, number = _parse_issue_ref(config, issue_ref)
    dep_repo, dep_number = _parse_issue_ref(config, on_ref)

    token = _require_token()
    gh = _make_github_client(token)

    issue = gh.get_issue(repo_name, number)

    # Build dependency string
    dep_str = f"#{dep_number}" if dep_repo == repo_name else f"{dep_repo}#{dep_number}"

    depends_line = f"Depends on {dep_str}"

    # Check if already declared
    if depends_line in issue.body:
        console.print(f"[yellow]{repo_name}#{number} already depends on {dep_str}[/yellow]")
        return

    # Append to body
    new_body = (
        issue.body.rstrip() + f"\n\n{depends_line}\n" if issue.body.strip() else f"{depends_line}\n"
    )
    gh.update_issue_body(repo_name, number, new_body)
    console.print(f"[green]{repo_name}#{number} now depends on {dep_str}[/green]")


# ── Config / Repo management ─────────────────────────────────────────────


def cmd_config_set(config: Config, key: str, value: str, config_path: Path) -> None:
    import yaml

    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        sys.exit(1)

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    parts = key.split(".")
    target = data
    for part in parts[:-1]:
        target = target.setdefault(part, {})

    try:
        typed_value: object = int(value)
    except ValueError:
        try:
            typed_value = float(value)
        except ValueError:
            typed_value = value.lower() == "true" if value.lower() in ("true", "false") else value

    target[parts[-1]] = typed_value

    with open(config_path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)

    console.print(f"[green]Set {key} = {typed_value}[/green]")


def cmd_repo_add(
    config: Config, repo_name: str, path: Path | None = None, branch: str = "main"
) -> None:
    from claudette.core.config import RepoConfig

    for r in config.repositories:
        if r.name == repo_name:
            console.print(f"[yellow]{repo_name} already in config[/yellow]")
            return

    repo_path = str(path.resolve()) if path else None
    config.repositories.append(RepoConfig(name=repo_name, path=repo_path, default_branch=branch))
    config.save()
    console.print(f"[green]Added {repo_name}[/green]")


def cmd_repo_remove(config: Config, repo_name: str) -> None:
    original_len = len(config.repositories)
    config.repositories = [r for r in config.repositories if r.name != repo_name]

    if len(config.repositories) == original_len:
        console.print(f"[yellow]{repo_name} not found in config[/yellow]")
        return

    config.save()
    console.print(f"[green]Removed {repo_name}[/green]")


# ── Relay commands ────────────────────────────────────────────────────────


def cmd_relay_start(config: Config, foreground: bool = False) -> None:
    import os as _os

    from claudette.core.relay import RelayWatchdog

    watchdog = RelayWatchdog(config)
    info = watchdog.status()

    if info["running"]:
        console.print(f"[yellow]Relay already running (PID {info['pid']})[/yellow]")
        return

    if foreground:
        console.print("[green]Starting relay watchdog (foreground)...[/green]")
        console.print(f"  Requests:  {config.relay_dir / 'requests'}")
        console.print(f"  Responses: {config.relay_dir / 'responses'}")
        watchdog.start()
    else:
        # Fork to background
        pid = _os.fork()
        if pid > 0:
            # Parent
            console.print(f"[green]Relay watchdog started (PID {pid})[/green]")
            console.print(f"  Requests:  {config.relay_dir / 'requests'}")
            console.print(f"  Responses: {config.relay_dir / 'responses'}")
            return
        else:
            # Child — detach and run
            _os.setsid()
            try:
                watchdog.start()
            except Exception:
                _os._exit(1)
            _os._exit(0)


def cmd_relay_stop(config: Config) -> None:
    from claudette.core.relay import RelayWatchdog

    watchdog = RelayWatchdog(config)
    info = watchdog.status()

    if not info["running"]:
        console.print("[dim]Relay is not running[/dim]")
        return

    if watchdog.stop_remote():
        console.print(f"[green]Relay stopped (was PID {info['pid']})[/green]")
    else:
        console.print("[red]Failed to stop relay[/red]")


def cmd_relay_status(config: Config) -> None:
    from claudette.core.relay import RelayWatchdog

    watchdog = RelayWatchdog(config)
    info = watchdog.status()

    if info["running"]:
        console.print(
            f"[green]Relay running[/green]  PID {info['pid']}  pending: {info['pending_requests']}"
        )
        sa_count = info.get("active_subagents", 0)
        sa_pending = info.get("pending_subagent_requests", 0)
        if sa_count or sa_pending:
            console.print(f"  Subagents: {sa_count} active, {sa_pending} pending")
    else:
        console.print("[dim]Relay not running[/dim]")
    console.print(f"  Dir: {info['relay_dir']}")


# ── Memory commands ───────────────────────────────────────────────────────


def cmd_memory_sync(config: Config) -> None:
    from claudette.core.memory import MemoryIndex

    token = _require_token()
    memory = MemoryIndex(config.memory_dir, backend=config.memory.backend)

    with console.status("Fetching issues and PRs..."):
        all_issues = _fetch_all_issues(config, token)

    with console.status(f"Indexing {len(all_issues)} items..."):
        stats = memory.sync(all_issues)

    console.print(
        f"[green]Synced:[/green] {stats['added']} added, {stats['updated']} updated, "
        f"{stats['total']} total indexed"
    )


def cmd_memory_search(
    config: Config, query: str, limit: int = 10, state: str | None = None
) -> None:
    from claudette.core.memory import MemoryIndex

    memory = MemoryIndex(config.memory_dir, backend=config.memory.backend)
    results = memory.search(query, limit=limit, state=state)

    if not results:
        console.print("[dim]No results. Run `claudette memory sync` first.[/dim]")
        return

    for r in results:
        score = r["score"]
        kind = "PR" if r["is_pr"] else "Issue"
        state_tag = (
            f"[dim]{r['state']}[/dim]" if r["state"] == "closed" else f"[green]{r['state']}[/green]"
        )
        labels = ", ".join(r["labels"][:3]) if r["labels"] else ""

        console.print(f"  [bold]{r['key']}[/bold] ({kind}, {state_tag})  score: {score:.3f}")
        console.print(f"    {r['title']}")
        if labels:
            console.print(f"    [dim]{labels}[/dim]")
        console.print(f"    [dim]{r['url']}[/dim]")
        console.print()


def cmd_memory_status(config: Config) -> None:
    from claudette.core.memory import MemoryIndex

    memory = MemoryIndex(config.memory_dir, backend=config.memory.backend)
    stats = memory.stats()

    console.print("[bold]Memory Index[/bold]")
    console.print(f"  Backend: {stats['backend']}")
    console.print(f"  Total indexed: {stats['total']}")
    console.print(f"  Open: {stats['open']}  PRs: {stats['prs']}")
    console.print(f"  Last sync: {stats['last_sync']}")
    console.print(f"  Embeddings: {stats['embeddings_size_kb']} KB")
    console.print(f"  DB: {stats['db_path']}")


def cmd_memory_clear(config: Config) -> None:
    from claudette.core.memory import MemoryIndex

    memory = MemoryIndex(config.memory_dir, backend=config.memory.backend)
    memory.clear()
    console.print("[green]Memory index cleared.[/green]")
