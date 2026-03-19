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
    AutonomyConfig,
    Config,
    DeterministicRulesConfig,
    GitHubConfig,
    LabelConfig,
    LLMConfig,
    MemoryConfig,
    NotificationsConfig,
    PipelineConfig,
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

    # Phase 3c: Auto-merge and PR rules
    rules_config = _configure_rules()

    # Phase 3d: Autonomy
    autonomy_config = _configure_autonomy()

    # Phase 3e: Pipeline
    pipeline_config = _configure_pipeline()

    # Phase 3f: LLM command
    llm_config = _configure_llm()

    # Phase 3g: Memory backend
    memory_config = _configure_memory()

    # Phase 3h: Relay (for sandboxed environments)
    relay_config = _configure_relay()

    # Phase 3i: Notifications
    notifications_config = _configure_notifications()

    # Phase 4: Confirm and bootstrap
    console.print()
    _show_summary(
        project_dir, repos, system, routing, label_config, llm_config,
        memory_config, relay_config, rules_config, autonomy_config,
        pipeline_config, notifications_config,
    )

    if not questionary.confirm("\nProceed with setup?", default=True).ask():
        raise SystemExit(0)

    config = Config(
        project_dir=project_dir,
        system=system,
        repositories=repos,
        llm=llm_config,
        github=GitHubConfig(labels=label_config, routing=routing),
        deterministic_rules=rules_config,
        memory=memory_config,
        relay=relay_config,
        autonomy=autonomy_config,
        pipeline=pipeline_config,
        notifications=notifications_config,
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
        rc = build_repo_config(
            repo["name"],
            project_config=repo["config"] if repo["has_config"] else None,
            repo_path=repo["path"],
        )
        # Let user override the auto-detected default branch
        branch = questionary.text(
            f"  {repo['name']} base branch:", default=rc.default_branch
        ).ask()
        if branch:
            rc.default_branch = branch
        repos.append(rc)

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


def _configure_llm() -> LLMConfig:
    """Prompt for LLM CLI command configuration."""
    import shutil

    console.print("\n[bold]LLM command[/bold]\n")

    defaults = LLMConfig()

    # Check if claude is available
    has_claude = shutil.which("claude") is not None
    if has_claude:
        console.print("  [green]✓[/green] `claude` CLI found")
    else:
        console.print("  [yellow]![/yellow] `claude` CLI not found on PATH")

    console.print("  Commands use [cyan]{prompt}[/cyan] as a placeholder for the prompt text.")
    console.print(f"  Default: [dim]{defaults.cmd_one_shot}[/dim]\n")

    if not questionary.confirm("Customize LLM commands?", default=not has_claude).ask():
        return defaults

    one_shot = questionary.text(
        "One-shot command (summarizer):",
        default=defaults.cmd_one_shot,
    ).ask()

    session = questionary.text(
        "Session command (manager):",
        default=defaults.cmd_session,
    ).ask()

    subagent = questionary.text(
        "Subagent command (workers):",
        default=defaults.cmd_subagent,
    ).ask()

    summarizer = questionary.text(
        "Summarizer command (blank = same as one-shot):",
        default="",
    ).ask()

    return LLMConfig(
        cmd_one_shot=one_shot or defaults.cmd_one_shot,
        cmd_session=session or defaults.cmd_session,
        cmd_subagent=subagent or defaults.cmd_subagent,
        cmd_summarizer=summarizer or "",
    )


def _detect_github_user() -> str:
    """Detect the current GitHub username via gh CLI."""
    import subprocess

    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _configure_routing(label_config: LabelConfig) -> RoutingConfig:
    """Prompt for issue routing settings."""
    from claudette.core.config import _primary_label

    console.print("\n[bold]Issue routing[/bold]\n")

    # Detect and confirm owner
    detected_user = _detect_github_user()
    if detected_user:
        console.print(f"  Detected GitHub user: [cyan]{detected_user}[/cyan]")
        console.print("  When set, claudette only picks up issues you created.")
        console.print("  Leave blank to pick up all issues.\n")
        owner = questionary.text(
            "Owner (GitHub username):",
            default=detected_user,
        ).ask()
    else:
        console.print("  Set an owner to only pick up issues you created.")
        console.print("  Leave blank to pick up all issues.\n")
        owner = questionary.text(
            "Owner (GitHub username, or blank for all):",
            default="",
        ).ask()

    owner = (owner or "").strip()

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
        owner=owner,
        require_ready_label=require_label if require_label is not None else True,
        ignore_labels=ignore_labels,
    )


_BACKEND_DESCRIPTIONS = {
    "dense": "Semantic (model2vec) — best for meaning-based search, requires model download (~8MB)",
    "bm25": "Keyword (BM25) — fast exact keyword matching, no model download needed",
    "hybrid": "Hybrid (both) — combines semantic + keyword search for best results",
}


def _configure_memory() -> MemoryConfig:
    """Prompt for memory search backend."""
    from claudette.core.memory import available_backends

    console.print("\n[bold]Memory search[/bold]\n")
    console.print("  Claudette indexes issues/PRs for semantic search.")
    console.print("  Choose a search backend based on your environment:\n")

    installed = available_backends()

    if not installed:
        console.print("  [yellow]No search backends installed.[/yellow]")
        console.print("  Install one:")
        console.print("    pip install claudette[dense]   # model2vec embeddings")
        console.print("    pip install claudette[bm25]    # BM25 keyword search")
        console.print("    pip install claudette[search]  # both (hybrid)\n")
        console.print("  [dim]Defaulting to 'dense' — install model2vec before first sync.[/dim]")
        return MemoryConfig(backend="dense")

    choices = []
    for backend in ["dense", "bm25", "hybrid"]:
        desc = _BACKEND_DESCRIPTIONS[backend]
        if backend in installed:
            choices.append(questionary.Choice(title=f"{backend} — {desc}", value=backend))
        else:
            choices.append(
                questionary.Choice(
                    title=f"{backend} — {desc} [not installed]",
                    value=backend,
                    disabled="missing dependencies",
                )
            )

    # Default to the best available
    default = "hybrid" if "hybrid" in installed else installed[0]

    selected = questionary.select(
        "Search backend:",
        choices=choices,
        default=default,
    ).ask()

    if selected is None:
        raise SystemExit(0)

    return MemoryConfig(backend=selected)


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


def _configure_rules() -> DeterministicRulesConfig:
    """Prompt for PR merge and review rules."""
    console.print("\n[bold]PR rules[/bold]\n")

    auto_merge = questionary.confirm(
        "Auto-merge approved PRs with passing CI?",
        default=True,
    ).ask()

    merge_method = "squash"
    if auto_merge:
        merge_method = questionary.select(
            "Merge method:",
            choices=["squash", "merge", "rebase"],
            default="squash",
        ).ask() or "squash"

    auto_review = questionary.confirm(
        "Auto-flag new PRs for review?",
        default=True,
    ).ask()

    return DeterministicRulesConfig(
        auto_merge_approved_prs=auto_merge if auto_merge is not None else True,
        auto_merge_method=merge_method,
        auto_review_new_prs=auto_review if auto_review is not None else True,
    )


def _configure_autonomy() -> AutonomyConfig:
    """Prompt for autonomous work generation settings."""
    console.print("\n[bold]Autonomous work[/bold]\n")
    console.print("  When enabled, claudette can discover and create its own issues —")
    console.print("  finding TODOs, proposing improvements, and dreaming up features.\n")

    enabled = questionary.confirm(
        "Enable autonomous work generation?",
        default=False,
    ).ask()

    if not enabled:
        return AutonomyConfig()

    modes = questionary.checkbox(
        "Which modes?",
        choices=[
            questionary.Choice(
                "discover — find TODOs, coverage gaps",
                value="discover", checked=True,
            ),
            questionary.Choice(
                "improve — targeted code improvements",
                value="improve", checked=True,
            ),
            questionary.Choice(
                "ideate — dream up new features",
                value="ideate", checked=True,
            ),
        ],
    ).ask()

    if modes is None:
        raise SystemExit(0)

    max_issues = questionary.text(
        "Max issues to create per tick:",
        default="3",
        validate=lambda x: x.isdigit() or "Must be a number",
    ).ask()

    cooldown = questionary.text(
        "Cooldown between autonomous runs (minutes):",
        default="30",
        validate=lambda x: x.isdigit() or "Must be a number",
    ).ask()

    run_on_idle = questionary.confirm(
        "Run autonomous mode on idle ticks (no human issues)?",
        default=True,
    ).ask()

    return AutonomyConfig(
        enabled=True,
        modes=modes or ["discover", "improve", "ideate"],
        max_issues_per_tick=int(max_issues or "3"),
        cooldown_minutes=int(cooldown or "30"),
        run_on_idle=run_on_idle if run_on_idle is not None else True,
    )


def _configure_pipeline() -> PipelineConfig:
    """Prompt for pipeline stage configuration."""
    console.print("\n[bold]Pipeline[/bold]\n")
    console.print("  The agent pipeline runs stages for each issue:")
    console.print("  scout → architect → builder → tester → reviewer\n")

    enabled = questionary.confirm(
        "Enable multi-stage pipeline?",
        default=True,
    ).ask()

    if not enabled:
        return PipelineConfig(enabled=False)

    all_stages = ["scout", "architect", "builder", "tester", "reviewer"]
    skip = questionary.checkbox(
        "Skip any stages? (uncheck to skip)",
        choices=[
            questionary.Choice(s, value=s, checked=True) for s in all_stages
        ],
    ).ask()

    if skip is None:
        raise SystemExit(0)

    # skip_stages = stages NOT in the selected list
    skip_stages = [s for s in all_stages if s not in skip]

    return PipelineConfig(enabled=True, skip_stages=skip_stages)


def _configure_notifications() -> NotificationsConfig:
    """Prompt for webhook notification settings."""
    console.print("\n[bold]Notifications[/bold]\n")
    console.print("  Send events to a Slack/Discord webhook.\n")

    webhook = questionary.text(
        "Webhook URL (blank to skip):",
        default="",
    ).ask()

    if not webhook or not webhook.strip():
        return NotificationsConfig()

    return NotificationsConfig(webhook_url=webhook.strip())


def _show_summary(
    project_dir: Path,
    repos: list[RepoConfig],
    system: SystemConfig,
    routing: RoutingConfig | None = None,
    label_config: LabelConfig | None = None,
    llm_config: LLMConfig | None = None,
    memory_config: MemoryConfig | None = None,
    relay_config: RelayConfig | None = None,
    rules_config: DeterministicRulesConfig | None = None,
    autonomy_config: AutonomyConfig | None = None,
    pipeline_config: PipelineConfig | None = None,
    notifications_config: NotificationsConfig | None = None,
) -> None:
    """Show what we're about to set up."""
    console.print("[bold]Summary[/bold]\n")
    console.print(f"  Project: [cyan]{project_dir}[/cyan]")
    console.print(f"  Polling: every {system.polling_interval_minutes}m")
    console.print(f"  Timeout: {system.session_timeout_minutes}m")
    if llm_config:
        base_cmd = llm_config.cmd_one_shot.split()[0]
        defaults = LLMConfig()
        if llm_config.cmd_one_shot != defaults.cmd_one_shot:
            console.print(f"  LLM:     [cyan]{base_cmd}[/cyan] (custom)")
        else:
            console.print(f"  LLM:     {base_cmd}")
    if memory_config:
        console.print(f"  Search:  {memory_config.backend}")
    if routing:
        if routing.owner:
            console.print(f"  Owner:   [cyan]{routing.owner}[/cyan] (only your issues)")
        else:
            console.print("  Owner:   (all users)")
        mode = "require 'ready-for-dev' label" if routing.require_ready_label else "all open issues"
        console.print(f"  Routing: {mode}")
        if routing.ignore_labels:
            console.print(f"  Ignore:  {', '.join(routing.ignore_labels)}")
    if rules_config:
        if rules_config.auto_merge_approved_prs:
            merge_str = f"auto-merge ({rules_config.auto_merge_method})"
        else:
            merge_str = "manual merge"
        review_str = "on" if rules_config.auto_review_new_prs else "off"
        console.print(f"  PRs:     {merge_str}, auto-review: {review_str}")
    if autonomy_config and autonomy_config.enabled:
        modes = ", ".join(autonomy_config.modes)
        limit = autonomy_config.max_issues_per_tick
        console.print(f"  Autonomy: {modes} (max {limit}/tick)")
    if pipeline_config:
        if pipeline_config.enabled:
            active = [s for s in pipeline_config.stages if s not in pipeline_config.skip_stages]
            console.print(f"  Pipeline: {' → '.join(active)}")
        else:
            console.print("  Pipeline: disabled (single worker)")
    if notifications_config and notifications_config.webhook_url:
        console.print(f"  Webhook: {notifications_config.webhook_url[:40]}...")
    if label_config:
        lc_defaults = LabelConfig()
        is_default = label_config.model_dump() == lc_defaults.model_dump()
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
