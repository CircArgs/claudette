"""CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click

from claudette.core.config import Config


def load_config(ctx: click.Context) -> Config:
    project_dir = ctx.obj.get("project_dir")
    config = Config.load(Path(project_dir)) if project_dir else Config.find_from_cwd()

    if config is None:
        click.echo("Not inside a claudette project. Run `claudette init <dir>` first.", err=True)
        raise SystemExit(1)
    return config


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--project",
    "-p",
    type=click.Path(path_type=Path),
    default=None,
    envvar="CLAUDETTE_PROJECT",
    help="Project directory (default: detect from cwd)",
)
@click.pass_context
def main(ctx: click.Context, project: Path | None) -> None:
    """claudette — autonomous GitHub orchestration.

    Run with no arguments to see system status.
    """
    ctx.ensure_object(dict)
    if project:
        ctx.obj["project_dir"] = str(project)

    if ctx.invoked_subcommand is None:
        from claudette.cli.commands import cmd_status

        cmd_status(load_config(ctx))


# ── Core commands ─────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """System status at a glance (same as running with no args)."""
    from claudette.cli.commands import cmd_status

    cmd_status(load_config(ctx))


@main.command()
@click.argument("issue_ref")
@click.pass_context
def why(ctx: click.Context, issue_ref: str) -> None:
    """Explain why an issue is in its current state.

    ISSUE_REF can be: owner/repo#42, #42, or just 42
    """
    from claudette.cli.commands import cmd_why

    cmd_why(load_config(ctx), issue_ref)


@main.command(name="open")
@click.argument("issue_ref")
@click.pass_context
def open_cmd(ctx: click.Context, issue_ref: str) -> None:
    """Open an issue or PR in the browser.

    ISSUE_REF can be: owner/repo#42, #42, or just 42
    """
    from claudette.cli.commands import cmd_open

    cmd_open(load_config(ctx), issue_ref)


@main.command()
@click.option("--dry-run", is_flag=True, help="Preview what a tick would do")
@click.argument("extra_prompt", required=False, default=None)
@click.pass_context
def tick(ctx: click.Context, dry_run: bool, extra_prompt: str | None) -> None:
    """Force an immediate polling cycle.

    Optionally pass an EXTRA_PROMPT to append to the manager session prompt.
    """
    from claudette.cli.commands import cmd_tick

    cmd_tick(load_config(ctx), dry_run=dry_run, extra_prompt=extra_prompt)


# ── Inspection commands ───────────────────────────────────────────────────


@main.command()
@click.option("--ready", is_flag=True, help="Show only ready issues")
@click.option("--blocked", is_flag=True, help="Show only blocked issues")
@click.option("--waiting", is_flag=True, help="Show only waiting-on-human issues")
@click.pass_context
def queue(ctx: click.Context, ready: bool, blocked: bool, waiting: bool) -> None:
    """Show ready / blocked / waiting issues."""
    from claudette.cli.commands import cmd_queue

    cmd_queue(load_config(ctx), ready=ready, blocked=blocked, waiting=waiting)


@main.command()
@click.option("--blocked", is_flag=True, help="Only show blocked dependency chains")
@click.option("--repo", help="Filter by repo (owner/name)")
@click.pass_context
def graph(ctx: click.Context, blocked: bool, repo: str | None) -> None:
    """Print the dependency tree."""
    from claudette.cli.commands import cmd_graph

    cmd_graph(load_config(ctx), blocked_only=blocked, repo=repo)


@main.command()
@click.option("--follow", "-f", is_flag=True, help="Tail the session log")
@click.pass_context
def session(ctx: click.Context, follow: bool) -> None:
    """Show the active manager session."""
    from claudette.cli.commands import cmd_session

    cmd_session(load_config(ctx), follow=follow)


@main.command()
@click.option("--repo", help="Filter by repo")
@click.option("--issue", type=int, help="Filter by issue number")
@click.option("--level", help="Filter by severity")
@click.pass_context
def log(ctx: click.Context, repo: str | None, issue: int | None, level: str | None) -> None:
    """Activity log."""
    from claudette.cli.commands import cmd_log

    cmd_log(load_config(ctx), repo=repo, issue=issue, level=level)


# ── Intervention commands ─────────────────────────────────────────────────


@main.command()
@click.argument("issue_ref")
@click.pass_context
def claim(ctx: click.Context, issue_ref: str) -> None:
    """Claim an issue so claudette won't work on it.

    ISSUE_REF can be: owner/repo#42, #42, or just 42
    """
    from claudette.cli.commands import cmd_claim

    cmd_claim(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def unclaim(ctx: click.Context, issue_ref: str) -> None:
    """Release a claimed issue back to claudette.

    ISSUE_REF can be: owner/repo#42, #42, or just 42
    """
    from claudette.cli.commands import cmd_unclaim

    cmd_unclaim(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def ready(ctx: click.Context, issue_ref: str) -> None:
    """Mark an issue as ready for claudette to pick up."""
    from claudette.cli.commands import cmd_ready

    cmd_ready(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def unready(ctx: click.Context, issue_ref: str) -> None:
    """Remove the ready label from an issue."""
    from claudette.cli.commands import cmd_unready

    cmd_unready(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def block(ctx: click.Context, issue_ref: str) -> None:
    """Mark an issue as blocked."""
    from claudette.cli.commands import cmd_block

    cmd_block(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def unblock(ctx: click.Context, issue_ref: str) -> None:
    """Remove the blocked label from an issue."""
    from claudette.cli.commands import cmd_unblock

    cmd_unblock(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def wait(ctx: click.Context, issue_ref: str) -> None:
    """Mark an issue as waiting on human input."""
    from claudette.cli.commands import cmd_wait

    cmd_wait(load_config(ctx), issue_ref)


@main.command()
@click.argument("issue_ref")
@click.pass_context
def unwait(ctx: click.Context, issue_ref: str) -> None:
    """Remove the waiting-on-human label from an issue."""
    from claudette.cli.commands import cmd_unwait

    cmd_unwait(load_config(ctx), issue_ref)


@main.command()
@click.argument("repo_name")
@click.pass_context
def pause(ctx: click.Context, repo_name: str) -> None:
    """Pause automation on a repo."""
    from claudette.cli.commands import cmd_pause

    cmd_pause(load_config(ctx), repo_name)


@main.command()
@click.argument("repo_name")
@click.pass_context
def resume(ctx: click.Context, repo_name: str) -> None:
    """Resume a paused repo."""
    from claudette.cli.commands import cmd_resume

    cmd_resume(load_config(ctx), repo_name)


# ── Issue commands ───────────────────────────────────────────────────────


@main.group(name="issue")
def issue_group() -> None:
    """Issue management."""
    pass


@issue_group.command(name="create")
@click.argument("title")
@click.option("--body", "-b", default="", help="Issue body")
@click.option("--repo", "-r", default=None, help="Target repo (default: first configured)")
@click.option("--ready", is_flag=True, help="Also apply the ready-for-dev label")
@click.pass_context
def issue_create(ctx: click.Context, title: str, body: str, repo: str | None, ready: bool) -> None:
    """Create a new GitHub issue."""
    from claudette.cli.commands import cmd_issue_create

    cmd_issue_create(load_config(ctx), title, body=body, repo=repo, ready=ready)


@issue_group.command(name="depends")
@click.argument("issue_ref")
@click.option(
    "--on", "on_ref", required=True, help="The issue this depends on (e.g. #38, owner/repo#38)"
)
@click.pass_context
def issue_depends(ctx: click.Context, issue_ref: str, on_ref: str) -> None:
    """Declare that ISSUE_REF depends on another issue.

    Example: claudette issue depends 42 --on 38
    """
    from claudette.cli.commands import cmd_issue_depends

    cmd_issue_depends(load_config(ctx), issue_ref, on_ref)


# ── Setup commands ────────────────────────────────────────────────────────


@main.command(name="init")
@click.argument("project_dir", type=click.Path(path_type=Path))
@click.pass_context
def init_cmd(ctx: click.Context, project_dir: Path) -> None:
    """Initialize a project directory.

    PROJECT_DIR is the directory containing your repos.
    """
    from claudette.cli.init import run_init

    run_init(project_dir.resolve())


@main.command(name="list")
def list_cmd() -> None:
    """List all registered projects."""
    from claudette.core.config import ProjectRegistry

    registry = ProjectRegistry.load()
    if not registry.projects:
        click.echo("No projects registered. Run `claudette init <dir>` to add one.")
        return
    for entry in registry.projects:
        marker = "*" if Path(entry.path).resolve() == Path.cwd().resolve() else " "
        click.echo(f"  {marker} {entry.name:20s} {entry.path}")


@main.command()
@click.option("--interval", "-n", default=5, type=int, help="Refresh interval in seconds")
@click.pass_context
def watch(ctx: click.Context, interval: int) -> None:
    """Refresh status on a loop (like watch(1))."""
    import time

    from claudette.cli.commands import cmd_status

    config = load_config(ctx)
    try:
        while True:
            click.clear()
            cmd_status(config)
            click.echo(f"\n[every {interval}s — Ctrl+C to stop]")
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


@main.group(name="config")
def config_group() -> None:
    """Configuration management."""
    pass


@config_group.command(name="set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str) -> None:
    """Update a config value (e.g. system.polling_interval_minutes 10)."""
    from claudette.cli.commands import cmd_config_set

    config = load_config(ctx)
    cmd_config_set(config, key, value, config.config_file)


@main.group(name="repo")
def repo_group() -> None:
    """Repository management."""
    pass


@repo_group.command(name="add")
@click.argument("name")
@click.option(
    "--path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Local path to the repo (default: discover in project dir)",
)
@click.option("--branch", default="main", help="Default branch name")
@click.pass_context
def repo_add(ctx: click.Context, name: str, path: Path | None, branch: str) -> None:
    """Add a repo to this project."""
    from claudette.cli.commands import cmd_repo_add

    config = load_config(ctx)
    cmd_repo_add(config, name, path=path, branch=branch)


@repo_group.command(name="remove")
@click.argument("name")
@click.pass_context
def repo_remove(ctx: click.Context, name: str) -> None:
    """Remove a repo from config."""
    from claudette.cli.commands import cmd_repo_remove

    config = load_config(ctx)
    cmd_repo_remove(config, name)


# ── Relay commands ────────────────────────────────────────────────────────


@main.group(name="relay")
def relay_group() -> None:
    """Command relay for sandboxed environments."""
    pass


@relay_group.command(name="start")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground")
@click.pass_context
def relay_start(ctx: click.Context, foreground: bool) -> None:
    """Start the relay watchdog."""
    from claudette.cli.commands import cmd_relay_start

    cmd_relay_start(load_config(ctx), foreground=foreground)


@relay_group.command(name="stop")
@click.pass_context
def relay_stop(ctx: click.Context) -> None:
    """Stop the relay watchdog."""
    from claudette.cli.commands import cmd_relay_stop

    cmd_relay_stop(load_config(ctx))


@relay_group.command(name="status")
@click.pass_context
def relay_status(ctx: click.Context) -> None:
    """Show relay status."""
    from claudette.cli.commands import cmd_relay_status

    cmd_relay_status(load_config(ctx))


# ── Memory commands ───────────────────────────────────────────────────────


@main.group(name="memory")
def memory_group() -> None:
    """Semantic index over issues and PRs."""
    pass


@memory_group.command(name="sync")
@click.pass_context
def memory_sync(ctx: click.Context) -> None:
    """Fetch all issues/PRs and index them locally."""
    from claudette.cli.commands import cmd_memory_sync

    cmd_memory_sync(load_config(ctx))


@memory_group.command(name="search")
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Max results")
@click.option(
    "--state", type=click.Choice(["open", "closed"]), default=None, help="Filter by state"
)
@click.pass_context
def memory_search(ctx: click.Context, query: str, limit: int, state: str | None) -> None:
    """Semantic search across all indexed issues and PRs."""
    from claudette.cli.commands import cmd_memory_search

    cmd_memory_search(load_config(ctx), query, limit=limit, state=state)


@memory_group.command(name="status")
@click.pass_context
def memory_status(ctx: click.Context) -> None:
    """Show index stats."""
    from claudette.cli.commands import cmd_memory_status

    cmd_memory_status(load_config(ctx))


@memory_group.command(name="clear")
@click.pass_context
def memory_clear(ctx: click.Context) -> None:
    """Wipe the local index."""
    from claudette.cli.commands import cmd_memory_clear

    cmd_memory_clear(load_config(ctx))
