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
@click.option("--days", "-d", default=7, type=int, help="Days of history to show")
@click.pass_context
def metrics(ctx: click.Context, days: int) -> None:
    """Show metrics: PRs, sessions, errors, uptime, daily breakdown."""
    from claudette.cli.commands import cmd_metrics

    cmd_metrics(load_config(ctx), days=days)


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
@click.argument("title", required=False, default=None)
@click.option("--body", "-b", default=None, help="Issue body")
@click.option("--repo", "-r", default=None, help="Target repo (default: first configured)")
@click.option("--ready/--no-ready", default=None, help="Apply the ready-for-dev label")
@click.option("--depends", "depends_on", default=None, help="Issue this depends on (e.g. #38)")
@click.pass_context
def issue_create(
    ctx: click.Context,
    title: str | None,
    body: str | None,
    repo: str | None,
    ready: bool | None,
    depends_on: str | None,
) -> None:
    """Create a new GitHub issue. Interactive when called without args."""
    from claudette.cli.commands import cmd_issue_create

    cmd_issue_create(
        load_config(ctx), title=title, body=body, repo=repo,
        ready=ready, depends_on=depends_on,
    )


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


@main.command()
@click.pass_context
def refresh(ctx: click.Context) -> None:
    """Regenerate AGENTS.md, skills, labels, and prompts from current config.

    Run this after editing config.yaml to apply changes.
    """
    from claudette.cli.commands import cmd_refresh

    cmd_refresh(load_config(ctx))


@main.command()
def update() -> None:
    """Self-update claudette to the latest version from GitHub."""
    from claudette.cli.commands import cmd_update

    cmd_update()


@main.command()
@click.option("--repo", default=None, help="Path to a specific repo (default: all repos)")
@click.option("--create", is_flag=True, help="File GitHub issues for discovered work")
@click.option("--dry-run", is_flag=True, help="Show what issues would be created")
@click.pass_context
def discover(ctx: click.Context, repo: str | None, create: bool, dry_run: bool) -> None:
    """Discover work: TODOs, coverage gaps, and dependencies."""
    from claudette.cli.commands import cmd_discover

    cmd_discover(load_config(ctx), repo=repo, create=create, dry_run=dry_run)


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
@click.option("--interval", "-n", default=2, type=int, help="Refresh interval in seconds")
@click.option("--simple", is_flag=True, help="Use simple text output instead of TUI dashboard")
@click.pass_context
def watch(ctx: click.Context, interval: int, simple: bool) -> None:
    """Live dashboard showing pipeline status (Ctrl+C to stop)."""
    config = load_config(ctx)

    if not simple:
        try:
            from claudette.cli.dashboard import Dashboard

            dashboard = Dashboard(config)
            dashboard.run(interval=float(interval))
            return
        except ImportError:
            pass  # Fall back to simple mode

    # Simple fallback (no rich.live or --simple flag)
    import time

    from claudette.cli.commands import cmd_status

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


# ── Cron commands ─────────────────────────────────────────────────────────


@main.group(name="cron")
def cron_group() -> None:
    """Manage the automatic polling cron job."""
    pass


@cron_group.command(name="on")
@click.pass_context
def cron_on(ctx: click.Context) -> None:
    """Install the cron job to run ticks automatically."""
    from claudette.core.bootstrap import install_cron

    config = load_config(ctx)
    result = install_cron(config)
    if result:
        click.echo(f"Cron installed: {result}")
    else:
        click.echo("Failed to install cron", err=True)


@cron_group.command(name="off")
@click.pass_context
def cron_off(ctx: click.Context) -> None:
    """Remove the cron job."""
    from claudette.core.bootstrap import remove_cron

    config = load_config(ctx)
    if remove_cron(config):
        click.echo("Cron removed")
    else:
        click.echo("No cron entry found for this project")


@cron_group.command(name="status")
@click.pass_context
def cron_status(ctx: click.Context) -> None:
    """Show whether the cron job is installed."""
    from claudette.core.bootstrap import get_cron_status

    config = load_config(ctx)
    line = get_cron_status(config)
    if line:
        click.echo(f"Active: {line}")
    else:
        click.echo("Not installed. Run `claudette cron on` to enable.")


# ── Autonomy commands ─────────────────────────────────────────────────────


@main.group(name="autonomy")
def autonomy_group() -> None:
    """Manage autonomous work generation (discovery, improvement, ideation)."""
    pass


@autonomy_group.command(name="on")
@click.option(
    "--modes",
    "-m",
    multiple=True,
    type=click.Choice(["discover", "improve", "ideate"]),
    help="Which autonomous modes to enable (default: all)",
)
@click.pass_context
def autonomy_on(ctx: click.Context, modes: tuple[str, ...]) -> None:
    """Enable autonomous work generation."""
    config = load_config(ctx)
    config.autonomy.enabled = True
    if modes:
        config.autonomy.modes = list(modes)
    config.save()
    active = ", ".join(config.autonomy.modes)
    click.echo(f"Autonomy enabled. Active modes: {active}")
    click.echo(f"  Max issues per tick: {config.autonomy.max_issues_per_tick}")
    click.echo(f"  Cooldown: {config.autonomy.cooldown_minutes} minutes")
    click.echo(f"  Auto-label: {config.autonomy.auto_label}")


@autonomy_group.command(name="off")
@click.pass_context
def autonomy_off(ctx: click.Context) -> None:
    """Disable autonomous work generation."""
    config = load_config(ctx)
    config.autonomy.enabled = False
    config.save()
    click.echo("Autonomy disabled.")


@autonomy_group.command(name="status")
@click.pass_context
def autonomy_status(ctx: click.Context) -> None:
    """Show autonomy configuration."""
    config = load_config(ctx)
    a = config.autonomy
    click.echo(f"Enabled: {a.enabled}")
    click.echo(f"Modes: {', '.join(a.modes)}")
    click.echo(f"Max issues/tick: {a.max_issues_per_tick}")
    click.echo(f"Max open issues/repo: {a.max_open_issues_per_repo}")
    click.echo(f"Cooldown: {a.cooldown_minutes} minutes")
    click.echo(f"Run on idle: {a.run_on_idle}")
    click.echo(f"Auto-label: {a.auto_label}")
    if "improve" in a.modes:
        click.echo(f"Improve targets: {', '.join(a.improve_targets)}")
    if "ideate" in a.modes:
        click.echo(f"Ideate targets: {', '.join(a.ideate_targets)}")


@autonomy_group.command(name="run")
@click.option("--dry-run", is_flag=True, help="Preview what would be created")
@click.pass_context
def autonomy_run(ctx: click.Context, dry_run: bool) -> None:
    """Run autonomous discovery now (one-shot, outside of tick)."""
    from claudette.core.autonomy import run_autonomous_discovery
    from claudette.core.github_client import GitHubAPIClient

    config = load_config(ctx)
    if not config.autonomy.enabled:
        click.echo("Autonomy is not enabled. Run `claudette autonomy on` first.", err=True)
        raise SystemExit(1)

    try:
        from claudette.core.gh_cli_client import GhCliClient
        github = GhCliClient()
    except Exception:
        github = GitHubAPIClient()

    created = run_autonomous_discovery(
        config, github, config.state_dir, dry_run=dry_run,
    )
    if created:
        for c in created:
            prefix = "[DRY-RUN] " if dry_run else ""
            click.echo(f"{prefix}Created {c['repo']}#{c['number']}: {c['title']} ({c['mode']})")
    else:
        click.echo("No issues created (cooldown active, or nothing discovered)")


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
