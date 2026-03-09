"""Interactive init flow — discovers repos, validates access, bootstraps."""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.console import Console

from claudette.core.bootstrap import (
    bootstrap,
    build_repo_config,
    discover_repos,
    install_cron,
    validate_github_access,
)
from claudette.core.config import (
    PROJECT_DIR_NAME,
    Config,
    GitHubConfig,
    LabelConfig,
    RelayConfig,
    RepoConfig,
    RoutingConfig,
    SystemConfig,
)
from claudette.core.skills import install_skills

console = Console()


def run_init(project_dir: Path) -> None:
    """Main init flow. Discovers repos, sets up .claudette/."""
    from claudette.cli.commands import BANNER

    console.print(BANNER)
    console.print(
        "[bold]claudette init[/bold] — Why is everything so hard — just make me a sandwich.\n"
    )

    already_exists = project_dir.exists()
    project_dir.mkdir(parents=True, exist_ok=True)
    dot_dir = project_dir / PROJECT_DIR_NAME

    # Check for existing project
    existing = Config.load(project_dir)
    if existing is not None:
        console.print(f"[yellow]Existing project found at {project_dir}[/yellow]")
        if not questionary.confirm(
            "Reinitialize? (config will be updated, not deleted)", default=True
        ).ask():
            raise SystemExit(0)

    # Phase 1: Discover repos
    # If project_dir already existed, scan it. If we just created it, scan cwd.
    scan_dir = project_dir if already_exists else Path.cwd()
    console.print(f"Scanning [bold]{scan_dir}[/bold] for git repos...\n")
    discovered = discover_repos(scan_dir)

    repos: list[RepoConfig] = []

    if discovered:
        repos = _select_repos(discovered)
    else:
        console.print("[dim]No git repos found in this directory.[/dim]\n")

    # Allow adding repos manually
    while True:
        if repos:
            if not questionary.confirm("Add another repo?", default=False).ask():
                break
        else:
            console.print("Let's add a repo to manage.\n")

        name = questionary.text(
            "GitHub repo (owner/repo):",
            validate=lambda x: "/" in x or "Must be in owner/repo format",
        ).ask()

        if name is None:  # Ctrl+C
            raise SystemExit(0)

        branch = questionary.text("Default branch:", default="main").ask()
        repos.append(RepoConfig(name=name, default_branch=branch or "main"))

    if not repos:
        console.print("[red]No repos selected. Nothing to do.[/red]")
        raise SystemExit(1)

    # Phase 2: Validate GitHub access
    console.print("\n[bold]Validating GitHub access...[/bold]\n")
    _validate_access(repos)

    # Phase 3: System settings
    system = _configure_system()

    # Phase 3b: Labels and routing
    label_config = _configure_labels()
    routing = _configure_routing(label_config)

    # Phase 3c: Relay (for sandboxed environments)
    relay_config = _configure_relay()

    # Phase 4: Confirm and bootstrap
    console.print()
    _show_summary(project_dir, repos, system, routing, label_config, relay_config)

    if not questionary.confirm("\nProceed with setup?", default=True).ask():
        raise SystemExit(0)

    config = Config(
        project_dir=project_dir,
        system=system,
        repositories=repos,
        github=GitHubConfig(labels=label_config, routing=routing),
        relay=relay_config,
    )

    console.print()
    with console.status("Setting up project..."):
        bootstrap(config)

    console.print("[green]Project ready.[/green]\n")

    # Install Claude Code skills (skip when relay is enabled — docs go in AGENTS.md)
    if relay_config.enabled:
        console.print("[dim]Relay enabled — CLI docs injected into AGENTS.md[/dim]")
    else:
        try:
            installed = install_skills(project_dir, scope="manager")
            if installed:
                console.print(f"[green]Installed skills:[/green] {', '.join(installed)}")
        except Exception as e:
            console.print(f"[yellow]Skill install failed:[/yellow] {e}")

    # Cron
    if questionary.confirm("Install cron for automatic polling?", default=True).ask():
        try:
            install_cron(config)
            console.print(
                f"[green]Cron installed:[/green] every {system.polling_interval_minutes}m"
            )
        except Exception as e:
            console.print(f"[yellow]Cron install failed:[/yellow] {e}")

    console.print(f"\n[bold]Done![/bold] Project initialized at [cyan]{project_dir}[/cyan]")
    console.print(f"  Config:  {dot_dir / 'config.yaml'}")
    console.print(f"  Prompts: {dot_dir / 'prompts/'} [dim](edit to customize)[/dim]")
    console.print("\nNext steps:")
    console.print(f"  cd {project_dir}")
    console.print("  claudette                 # system status")
    console.print("  claudette tick --dry-run  # preview a tick\n")


def _select_repos(discovered: list[dict]) -> list[RepoConfig]:
    """Show discovered repos and let user pick with checkboxes."""
    choices = []
    for repo in discovered:
        label = repo["name"]
        if repo["has_config"]:
            label += " (.claudette.yaml)"
        choices.append(
            questionary.Choice(
                title=label,
                value=repo,
                checked=repo["has_config"],
            )
        )

    selected = questionary.checkbox(
        "Select repos to manage:",
        choices=choices,
    ).ask()

    if selected is None:  # Ctrl+C
        raise SystemExit(0)

    repos = []
    for repo in selected:
        repos.append(
            build_repo_config(
                repo["name"],
                project_config=repo["config"] if repo["has_config"] else None,
                repo_path=repo["path"],
            )
        )

    return repos


def _validate_access(repos: list[RepoConfig]) -> None:
    """Validate GitHub access for all repos."""
    failures = []
    for repo in repos:
        result = validate_github_access(repo.name)
        if result["error"]:
            console.print(f"  [red]✗[/red] {repo.name}: {result['error']}")
            failures.append(result)
        elif not result["can_write"]:
            console.print(
                f"  [yellow]![/yellow] {repo.name}: read-only access "
                f"(claudette needs push access to create branches and PRs)"
            )
            failures.append(result)
        else:
            console.print(f"  [green]✓[/green] {repo.name}: full access")

    if failures:
        console.print()
        console.print("[yellow]Some repos have access issues.[/yellow]")
        console.print("To fix: ensure your GITHUB_TOKEN or gh CLI auth has 'repo' scope.")
        console.print("Create a token at: https://github.com/settings/tokens/new?scopes=repo")
        if not questionary.confirm("Continue anyway?", default=False).ask():
            raise SystemExit(1)


def _configure_system() -> SystemConfig:
    """Prompt for system settings with sensible defaults."""
    console.print("\n[bold]System settings[/bold]\n")

    interval = questionary.text(
        "Polling interval (minutes):",
        default="5",
        validate=lambda x: x.isdigit() or "Must be a number",
    ).ask()

    timeout = questionary.text(
        "Session timeout (minutes):",
        default="45",
        validate=lambda x: x.isdigit() or "Must be a number",
    ).ask()

    return SystemConfig(
        polling_interval_minutes=int(interval or "5"),
        session_timeout_minutes=int(timeout or "45"),
    )


_LABEL_DESCRIPTIONS = {
    "in_progress": "In progress (claimed/being worked on)",
    "blocked": "Blocked (waiting on dependency)",
    "waiting_on_user": "Waiting on human input",
    "needs_review": "PR needs review",
    "ready_for_dev": "Ready for claudette to pick up",
}


def _configure_labels() -> LabelConfig:
    """Prompt for label customization."""
    console.print("\n[bold]Labels[/bold]\n")

    defaults = LabelConfig()
    console.print("  Claudette uses GitHub labels to track issue state.")
    console.print("  Each can be one or more labels (comma-separated), or empty to disable.\n")

    if not questionary.confirm("Customize labels?", default=False).ask():
        console.print("[dim]  Using defaults.[/dim]")
        return defaults

    result = {}
    for field_name, description in _LABEL_DESCRIPTIONS.items():
        current = getattr(defaults, field_name)
        default_str = ", ".join(current) if isinstance(current, list) else current

        answer = questionary.text(
            f"  {description}:",
            default=default_str,
        ).ask()

        if answer is None:  # Ctrl+C
            raise SystemExit(0)

        answer = answer.strip()
        if not answer:
            result[field_name] = []
        elif "," in answer:
            result[field_name] = [v.strip() for v in answer.split(",") if v.strip()]
        else:
            result[field_name] = [answer]

    return LabelConfig(**result)


def _configure_routing(label_config: LabelConfig) -> RoutingConfig:
    """Prompt for issue routing settings."""
    from claudette.core.config import _primary_label

    console.print("\n[bold]Issue routing[/bold]\n")

    ready_label = _primary_label(label_config.ready_for_dev)
    if ready_label:
        require_label = questionary.confirm(
            f"Only pick up issues labeled '{ready_label}'?"
            " (Otherwise all open issues are fair game)",
            default=True,
        ).ask()
    else:
        # ready_for_dev is disabled, so require_ready_label makes no sense
        require_label = False

    ignore_input = questionary.text(
        "Labels to ignore (comma-separated, or leave blank):",
        default="",
    ).ask()

    ignore_labels = [v.strip() for v in (ignore_input or "").split(",") if v.strip()]

    return RoutingConfig(
        require_ready_label=require_label if require_label is not None else True,
        ignore_labels=ignore_labels,
    )


def _configure_relay() -> RelayConfig:
    """Prompt for relay configuration."""
    console.print("\n[bold]Command relay[/bold]\n")
    console.print("  The relay lets a sandboxed Claude execute commands and spawn")
    console.print("  sub-agents via file-based IPC. Enable this if your Claude")
    console.print("  environment restricts direct shell access.\n")

    enabled = questionary.confirm(
        "Enable command relay?",
        default=False,
    ).ask()

    if not enabled:
        return RelayConfig()

    subagents = questionary.confirm(
        "Enable sub-agent relay? (lets sandboxed Claude spawn worker sessions)",
        default=True,
    ).ask()

    return RelayConfig(
        enabled=True,
        subagents_enabled=subagents if subagents is not None else True,
    )


def _show_summary(
    project_dir: Path,
    repos: list[RepoConfig],
    system: SystemConfig,
    routing: RoutingConfig | None = None,
    label_config: LabelConfig | None = None,
    relay_config: RelayConfig | None = None,
) -> None:
    """Show what we're about to set up."""
    console.print("[bold]Summary[/bold]\n")
    console.print(f"  Project: [cyan]{project_dir}[/cyan]")
    console.print(f"  Polling: every {system.polling_interval_minutes}m")
    console.print(f"  Timeout: {system.session_timeout_minutes}m")
    if routing:
        mode = "require 'ready-for-dev' label" if routing.require_ready_label else "all open issues"
        console.print(f"  Routing: {mode}")
        if routing.ignore_labels:
            console.print(f"  Ignore:  {', '.join(routing.ignore_labels)}")
    if label_config:
        defaults = LabelConfig()
        is_default = label_config.model_dump() == defaults.model_dump()
        if not is_default:
            console.print("  Labels:")
            for field_name, description in _LABEL_DESCRIPTIONS.items():
                value = getattr(label_config, field_name)
                labels = value if isinstance(value, list) else [value] if value else []
                display = ", ".join(labels) if labels else "(disabled)"
                console.print(f"    {description}: [cyan]{display}[/cyan]")
    if relay_config and relay_config.enabled:
        parts = ["commands"]
        if relay_config.subagents_enabled:
            parts.append("sub-agents")
        console.print(f"  Relay:   {' + '.join(parts)}")
    console.print("  Repos:")
    for r in repos:
        path_info = f" at {r.path}" if r.path else ""
        console.print(f"    - {r.name} (branch: {r.default_branch}){path_info}")
