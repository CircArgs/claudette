"""Pydantic models for claudette configuration."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

# The file name claudette looks for inside individual repos
REPO_CONFIG_FILE = ".claudette.yaml"

# Global registry directory
GLOBAL_HOME = Path("~/.claudette")

# Project-local directory name
PROJECT_DIR_NAME = ".claudette"


# Labels can be a single string, a list of strings, or empty list (disabled).
LabelValue = str | list[str]


def _normalize_label(value: LabelValue) -> list[str]:
    """Convert any label value to a list of strings."""
    if isinstance(value, str):
        return [value] if value else []
    return [v for v in value if v]


def _label_match(issue_labels: list[str], config_labels: LabelValue) -> bool:
    """Check if any of the issue's labels match the configured label(s)."""
    normalized = _normalize_label(config_labels)
    if not normalized:
        return False
    return any(lbl in normalized for lbl in issue_labels)


def _primary_label(value: LabelValue) -> str | None:
    """Get the first/primary label (used when applying labels). Returns None if disabled."""
    normalized = _normalize_label(value)
    return normalized[0] if normalized else None


class LabelConfig(BaseModel):
    model_config = {"extra": "ignore"}

    in_progress: LabelValue = Field(default_factory=lambda: ["claudette: in-progress"])
    blocked: LabelValue = Field(default_factory=lambda: ["claudette: blocked"])
    waiting_on_user: LabelValue = Field(default_factory=lambda: ["claudette: waiting-on-user"])
    needs_review: LabelValue = Field(default_factory=lambda: ["claudette: needs-review"])
    ready_for_dev: LabelValue = Field(default_factory=lambda: ["claudette: ready-for-dev"])


class BudgetConfig(BaseModel):
    max_tokens_per_issue: int = 500_000
    max_tokens_per_repo_per_day: int = 5_000_000
    pause_on_budget_exceeded: bool = True


class NotificationsConfig(BaseModel):
    """Outgoing webhook notifications."""

    webhook_url: str = ""  # Slack/Discord/generic webhook URL
    events: list[str] = Field(
        default_factory=lambda: [
            "session_launched",
            "pr_opened",
            "issue_completed",
            "error",
        ]
    )


class DeterministicRulesConfig(BaseModel):
    auto_review_new_prs: bool = True
    default_reviewer_agent: str = "peer-review"
    auto_merge_approved_prs: bool = True  # on by default
    auto_merge_method: str = "squash"  # merge, squash, rebase


class RepoConfig(BaseModel):
    """Per-repository config. Can come from .claudette.yaml in the repo or from init."""

    name: str  # owner/repo
    path: str | None = None  # local filesystem path (absolute), set during init
    default_branch: str = "main"
    labels: LabelConfig = Field(default_factory=LabelConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


class LLMConfig(BaseModel):
    manager_prompt: str = "manager.jinja2"
    summarizer_prompt: str = "summarizer.jinja2"
    # Command templates — {prompt} is replaced with the actual prompt text.
    # one_shot: non-interactive single response (used for summarizer)
    # session: long-running autonomous session (used for manager)
    # subagent: worker agent in a worktree (used by relay)
    cmd_one_shot: str = "claude -p {prompt}"
    cmd_session: str = "claude -p --dangerously-skip-permissions {prompt}"
    cmd_subagent: str = "claude -p --dangerously-skip-permissions {prompt}"
    cmd_summarizer: str = ""  # when empty, falls back to cmd_one_shot


class RoutingConfig(BaseModel):
    """Controls how claudette interprets issue labels for routing."""

    # GitHub username of the operator. When set, only issues created by this user
    # are picked up. Cross-user dependencies are respected (claudette waits for
    # another user's blocking issue to close before scheduling yours).
    owner: str = ""
    # If true, issues must have the ready_for_dev label to be picked up.
    # If false (default), any open issue without a blocking label is considered ready.
    require_ready_label: bool = True
    # Labels that mean "ignore this issue entirely" (claudette won't show or touch them)
    ignore_labels: list[str] = Field(default_factory=list)


class GitHubConfig(BaseModel):
    dependency_pattern: str = r"Depends on\s+(?:([\w-]+/[\w-]+))?#(\d+)"
    labels: LabelConfig = Field(default_factory=LabelConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)


class MemoryConfig(BaseModel):
    """Search backend for the semantic memory index."""

    # "dense" = model2vec only, "bm25" = BM25 only, "hybrid" = both with RRF
    backend: str = "dense"


class RelayConfig(BaseModel):
    enabled: bool = False
    subagents_enabled: bool = False
    command_timeout: int = 30
    max_pending: int = 5
    poll_interval: float = 0.3
    gc_age_seconds: int = 600
    allowed_commands: list[str] = Field(
        default_factory=lambda: [
            "git ",
            "gh ",
            "npm ",
            "npx ",
            "cargo ",
            "make ",
            "pytest ",
            "python ",
            "pip ",
            "ls ",
            "cat ",
            "find ",
            "grep ",
            "ruff ",
            "mypy ",
            "docker ",
            "kubectl ",
        ]
    )
    blocked_patterns: list[str] = Field(
        default_factory=lambda: [
            r"rm\s+-rf\s+/",
            r"sudo\s+",
            r"curl.*\|.*sh",
            r">\s*/etc/",
        ]
    )


class PipelineConfig(BaseModel):
    enabled: bool = True  # when False, use simple single-worker dispatch
    stages: list[str] = Field(
        default_factory=lambda: ["scout", "architect", "builder", "tester", "reviewer"]
    )
    skip_stages: list[str] = Field(default_factory=list)  # e.g. ["scout"] to skip for simple issues


class DiscoveryConfig(BaseModel):
    enabled: bool = False  # opt-in
    sources: list[str] = Field(default_factory=lambda: ["todos", "coverage"])
    min_coverage_threshold: float = 50.0
    file_extensions: list[str] = Field(
        default_factory=lambda: [".py", ".js", ".ts", ".go", ".rs", ".java", ".rb"]
    )


class AutonomyConfig(BaseModel):
    """Controls autonomous work generation — claudette dreams up its own work."""

    enabled: bool = False  # opt-in, off by default
    # Which autonomous modes are active
    modes: list[str] = Field(
        default_factory=lambda: ["discover", "improve", "ideate"]
    )
    # Max issues to auto-create per tick (prevents spam)
    max_issues_per_tick: int = 3
    # Max total open auto-created issues per repo (prevents backlog flood)
    max_open_issues_per_repo: int = 10
    # Cooldown between autonomous runs (minutes) — prevents running every tick
    cooldown_minutes: int = 30
    # When no human issues exist, run autonomous mode on idle ticks
    run_on_idle: bool = True
    # Label applied to auto-created issues so humans can identify them
    auto_label: str = "claudette: auto"
    # Types of improvements to look for
    improve_targets: list[str] = Field(
        default_factory=lambda: [
            "test_coverage",
            "error_handling",
            "performance",
            "documentation",
            "dead_code",
            "type_safety",
        ]
    )
    # Types of features to ideate
    ideate_targets: list[str] = Field(
        default_factory=lambda: [
            "developer_experience",
            "observability",
            "security",
            "accessibility",
        ]
    )


class CronConfig(BaseModel):
    """Settings for the cron job that runs `claudette tick`."""

    # Env var names to capture from the current shell when installing cron.
    # Values are read at `claudette cron on` time, never stored in config.
    capture_env: list[str] = Field(
        default_factory=lambda: [
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "ANTHROPIC_API_KEY",
            "HOME",
            "PATH",
        ]
    )


class SystemConfig(BaseModel):
    polling_interval_minutes: int = 5
    session_timeout_minutes: int = 45
    stale_issue_timeout_minutes: int = 120
    dry_run: bool = False
    max_retries_per_issue: int = 1


class Config(BaseModel):
    """Root config — stored in <project_dir>/.claudette/config.yaml."""

    project_dir: Path
    system: SystemConfig = Field(default_factory=SystemConfig)
    repositories: list[RepoConfig] = Field(default_factory=list)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    deterministic_rules: DeterministicRulesConfig = Field(default_factory=DeterministicRulesConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    relay: RelayConfig = Field(default_factory=RelayConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    paused_repos: list[str] = Field(default_factory=list)

    @property
    def dot_dir(self) -> Path:
        """The .claudette/ directory inside the project."""
        return self.project_dir / PROJECT_DIR_NAME

    @property
    def state_dir(self) -> Path:
        return self.dot_dir / "state"

    @property
    def log_dir(self) -> Path:
        return self.dot_dir / "logs"

    @property
    def memory_dir(self) -> Path:
        return self.dot_dir / "memory"

    @property
    def prompts_dir(self) -> Path:
        return self.dot_dir / "prompts"

    @property
    def worktree_dir(self) -> Path:
        return self.dot_dir / "worktrees"

    @property
    def relay_dir(self) -> Path:
        return self.dot_dir / "relay"

    @property
    def config_file(self) -> Path:
        return self.dot_dir / "config.yaml"

    @property
    def budget(self) -> BudgetConfig:
        """Global budget defaults. Per-repo overrides are on RepoConfig."""
        return BudgetConfig()

    def save(self) -> None:
        """Write config to <project_dir>/.claudette/config.yaml."""
        import yaml

        self.dot_dir.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        data["project_dir"] = str(self.project_dir)
        with open(self.config_file, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False)

    @classmethod
    def load(cls, project_dir: Path) -> Config | None:
        """Load config from a project directory. Returns None if not found."""
        import yaml

        config_file = project_dir / PROJECT_DIR_NAME / "config.yaml"
        if not config_file.exists():
            return None
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}
        data["project_dir"] = str(project_dir)
        return cls.model_validate(data)

    @classmethod
    def find_from_cwd(cls, start: Path | None = None) -> Config | None:
        """Walk up from start (default: cwd) looking for .claudette/config.yaml."""
        if start is None:
            start = Path.cwd()
        current = start.resolve()
        while True:
            config = cls.load(current)
            if config is not None:
                return config
            parent = current.parent
            if parent == current:
                return None
            current = parent


# ── Global project registry ──────────────────────────────────────────────


class ProjectEntry(BaseModel):
    name: str  # human-friendly name (e.g. "my-saas")
    path: str  # absolute path to project dir


class ProjectRegistry(BaseModel):
    projects: list[ProjectEntry] = Field(default_factory=list)

    @classmethod
    def load(cls) -> ProjectRegistry:
        registry_file = GLOBAL_HOME.expanduser() / "projects.json"
        if not registry_file.exists():
            return cls()
        try:
            data = json.loads(registry_file.read_text())
            return cls.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            return cls()

    def save(self) -> None:
        registry_dir = GLOBAL_HOME.expanduser()
        registry_dir.mkdir(parents=True, exist_ok=True)
        registry_file = registry_dir / "projects.json"
        registry_file.write_text(json.dumps(self.model_dump(), indent=2))

    def register(self, name: str, project_dir: Path) -> None:
        abs_path = str(project_dir.resolve())
        # Update existing or add new
        for entry in self.projects:
            if entry.name == name or entry.path == abs_path:
                entry.name = name
                entry.path = abs_path
                return
        self.projects.append(ProjectEntry(name=name, path=abs_path))

    def unregister(self, name: str) -> bool:
        before = len(self.projects)
        self.projects = [p for p in self.projects if p.name != name]
        return len(self.projects) < before

    def find_by_path(self, project_dir: Path) -> ProjectEntry | None:
        abs_path = str(project_dir.resolve())
        for entry in self.projects:
            if entry.path == abs_path:
                return entry
        return None


def load_repo_config(repo_path: Path) -> dict | None:
    """Load .claudette.yaml from an individual repo directory. Returns raw dict or None."""
    import yaml

    config_file = repo_path / REPO_CONFIG_FILE
    if not config_file.exists():
        return None
    with open(config_file) as f:
        return yaml.safe_load(f) or {}
