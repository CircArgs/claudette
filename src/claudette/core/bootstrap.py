"""Bootstrapper — discovers repos, validates access, initializes project."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from claudette.core.config import (
    Config,
    ProjectRegistry,
    RepoConfig,
    _normalize_label,
    _primary_label,
    load_repo_config,
)

logger = logging.getLogger("claudette.bootstrap")


def discover_repos(project_dir: Path) -> list[dict]:
    """Scan a project directory for git repos with GitHub remotes.

    Returns a list of dicts with keys: path, name (owner/repo), has_config, config.
    """
    results = []
    for entry in sorted(project_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        git_dir = entry / ".git"
        if not git_dir.exists():
            continue

        name = _get_github_remote(entry)
        if not name:
            continue

        repo_config = load_repo_config(entry)
        results.append(
            {
                "path": entry,
                "name": name,
                "has_config": repo_config is not None,
                "config": repo_config or {},
            }
        )

    return results


def _get_github_remote(repo_path: Path) -> str | None:
    """Extract owner/repo from a git remote URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
    except FileNotFoundError:
        return None

    # Handle SSH: git@github.com:owner/repo.git
    if "github.com:" in url:
        parts = url.split("github.com:")[-1]
        return parts.removesuffix(".git")

    # Handle HTTPS: https://github.com/owner/repo.git
    if "github.com/" in url:
        parts = url.split("github.com/")[-1]
        return parts.removesuffix(".git")

    return None


def _get_default_branch(repo_path: Path) -> str:
    """Get the default branch of a git repo."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("/")[-1]
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["git", "branch", "-r"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            branches = result.stdout
            if "origin/main" in branches:
                return "main"
            if "origin/master" in branches:
                return "master"
    except FileNotFoundError:
        pass

    return "main"


def validate_github_access(repo_name: str) -> dict:
    """Check GitHub access for a repo via gh CLI."""
    result = {
        "repo": repo_name,
        "accessible": False,
        "can_read_issues": False,
        "can_write": False,
        "error": None,
    }

    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo_name}", "--jq", ".permissions"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            if "404" in proc.stderr:
                result["error"] = f"Repository not found or no access: {repo_name}"
            elif "401" in proc.stderr:
                result["error"] = "GITHUB_TOKEN is invalid or expired"
            else:
                result["error"] = proc.stderr.strip()
            return result

        result["accessible"] = True

        perms_text = proc.stdout.strip()
        if perms_text:
            perms = json.loads(perms_text)
            result["can_read_issues"] = perms.get("pull", False) or perms.get("triage", False)
            result["can_write"] = perms.get("push", False) or perms.get("admin", False)

    except FileNotFoundError:
        result["error"] = "gh CLI not installed (install from https://cli.github.com)"
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out checking access"
    except json.JSONDecodeError:
        result["accessible"] = True
        result["can_read_issues"] = True

    return result


def build_repo_config(
    name: str, project_config: dict | None = None, repo_path: Path | None = None
) -> RepoConfig:
    """Build a RepoConfig from discovered info and .claudette.yaml overrides."""
    data: dict = {"name": name}

    if repo_path:
        data["path"] = str(repo_path.resolve())
        data["default_branch"] = _get_default_branch(repo_path)

    if project_config:
        if "default_branch" in project_config:
            data["default_branch"] = project_config["default_branch"]
        if "labels" in project_config:
            data["labels"] = project_config["labels"]
        if "budget" in project_config:
            data["budget"] = project_config["budget"]

    return RepoConfig.model_validate(data)


def clone_repo(name: str, dest: Path) -> Path:
    """Clone a GitHub repo into dest/<repo_name>. Returns the clone path."""
    repo_dir_name = name.replace("/", "_")
    clone_path = dest / repo_dir_name
    if clone_path.exists():
        logger.info("Clone already exists: %s", clone_path)
        return clone_path

    url = f"https://github.com/{name}.git"
    logger.info("Cloning %s into %s", url, clone_path)
    result = subprocess.run(
        ["git", "clone", url, str(clone_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone {name}: {result.stderr.strip()}")
    return clone_path


def bootstrap(config: Config) -> None:
    """Set up the .claudette/ directory tree inside the project."""

    # Create directory tree
    for d in [
        config.state_dir,
        config.log_dir,
        config.log_dir / "sessions",
        config.memory_dir,
        config.prompts_dir,
        config.worktree_dir,
        config.relay_dir / "requests",
        config.relay_dir / "responses",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        logger.info("Ensured directory: %s", d)

    # Clone repos that have no local path
    for repo_config in config.repositories:
        if not repo_config.path:
            clone_path = clone_repo(repo_config.name, config.project_dir)
            repo_config.path = str(clone_path.resolve())
            logger.info("Set repo path for %s: %s", repo_config.name, repo_config.path)

    # Copy default prompt templates (don't overwrite user edits)
    _copy_default_prompts(config.prompts_dir)

    # Create AGENTS.md (canonical) and symlink CLAUDE.md → AGENTS.md
    _init_agent_instructions(config)

    # Init state files for each repository
    for repo_config in config.repositories:
        safe_name = repo_config.name.replace("/", "_")

        # Init sync cursor if missing
        cursor_file = config.state_dir / f"{safe_name}_sync.txt"
        if not cursor_file.exists():
            cursor_file.write_text(datetime.now(UTC).isoformat())
            logger.info("Initialized sync cursor for %s", repo_config.name)

        # Init budget file if missing
        budget_file = config.state_dir / f"budget_{safe_name}.json"
        if not budget_file.exists():
            budget_file.write_text(
                json.dumps({"date": str(date.today()), "total_tokens": 0, "by_issue": {}})
            )
            logger.info("Initialized budget for %s", repo_config.name)

    # Ensure required labels exist on each repo
    _ensure_labels(config)

    # Save config
    config.save()

    # Register in global registry
    project_name = config.project_dir.resolve().name
    registry = ProjectRegistry.load()
    registry.register(project_name, config.project_dir)
    registry.save()

    logger.info("Bootstrap complete. Project at %s", config.project_dir)


def _init_agent_instructions(config: Config) -> None:
    """Create AGENTS.md (canonical) and symlink tool-specific files to it.

    AGENTS.md is the cross-tool standard (Codex, Cursor, Gemini CLI, etc.).
    CLAUDE.md is symlinked so Claude Code picks it up too.

    This is the MANAGER-level instruction file. It runs in the project root
    where claudette launches the manager session. Workers run in worktrees
    created from individual repos, so they pick up the repo's own files.
    """
    agents_md = config.project_dir / "AGENTS.md"
    claude_md = config.project_dir / "CLAUDE.md"

    if agents_md.exists():
        # Already initialized — just ensure symlinks are in place
        _ensure_agent_symlinks(config.project_dir)
        return

    # Migrate: if CLAUDE.md exists as a regular file (from older init), rename it
    if claude_md.exists() and not claude_md.is_symlink():
        claude_md.rename(agents_md)
        logger.info("Migrated existing CLAUDE.md → AGENTS.md")
        _ensure_agent_symlinks(config.project_dir)
        return

    repo_lines = "\n".join(
        f"- **{r.name}** at `{r.path or r.name.replace('/', '_')}` (branch: `{r.default_branch}`)"
        for r in config.repositories
    )
    worktree_dir = config.worktree_dir
    labels = config.github.labels

    agents_md.write_text(f"""\
# Claudette Manager

You are the manager session. You orchestrate work across repositories by dispatching sub-agents.

## Repositories

{repo_lines}

## Worktree isolation

Never work directly in the repository directories above — they are shared base clones.
Create a git worktree for each unit of work:

```bash
cd <repo_path>
git fetch origin
git worktree add {worktree_dir}/<safe_name>-issue-<N> -b agent/<N>-<slug> origin/<branch>
```

Dispatch each sub-agent into its own worktree. The sub-agent will pick up the repo's
own AGENTS.md automatically. Clean up when done:

```bash
git worktree remove {worktree_dir}/<safe_name>-issue-<N>
```

## For each issue

1. Apply `{_primary_label(labels.in_progress) or "in-progress"}` label to claim it
2. Create a worktree (see above)
3. Dispatch a sub-agent to the worktree to implement and test
4. Sub-agent commits, pushes, opens PR with "Closes #N"
5. Remove the worktree

## For each PR review

1. Fetch the PR branch and create a worktree
2. Dispatch a sub-agent to review, run tests, post review
3. Remove the worktree

## Memory

Before starting each issue, search for related work:
```bash
claudette memory search "<issue title or keywords>"
```

## Labels

- `{_primary_label(labels.in_progress) or "(disabled)"}` — claimed, being worked on
- `{_primary_label(labels.blocked) or "(disabled)"}` — waiting on a dependency
- `{_primary_label(labels.waiting_on_user) or "(disabled)"}` — needs human input (apply and comment what you need)
- `{_primary_label(labels.needs_review) or "(disabled)"}` — PR ready for review
- `{_primary_label(labels.ready_for_dev) or "(disabled)"}` — issue is ready to pick up

## Command Relay

If direct shell access is restricted (sandbox mode), use the command relay.
Run `claudette relay start` in a separate terminal, then use file-based IPC:

1. Write a request file (write to .tmp first, then rename for atomicity):
   Path: `{config.relay_dir}/requests/<id>.json`
   ```json
   {{"id": "<unique-id>", "command": "git status", "cwd": "<repo_path>", "timeout": 30}}
   ```

2. Read the response (poll until it appears, ~1s):
   Path: `{config.relay_dir}/responses/<id>.json`
   ```json
   {{"id": "<id>", "returncode": 0, "stdout": "...", "stderr": "...", "timed_out": false, "error": null}}
   ```

Allowed command prefixes: git, gh, npm, npx, cargo, make, pytest, python, pip, ls, cat, find, grep, ruff, mypy, docker, kubectl.
""")
    logger.info("Created AGENTS.md")
    _ensure_agent_symlinks(config.project_dir)


# Map of tool-specific filenames that should symlink to AGENTS.md
_AGENT_SYMLINKS = [
    "CLAUDE.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
]


def _ensure_agent_symlinks(project_dir: Path) -> None:
    """Create symlinks from tool-specific config files to AGENTS.md."""
    agents_md = project_dir / "AGENTS.md"
    if not agents_md.exists():
        return

    for rel_path in _AGENT_SYMLINKS:
        target = project_dir / rel_path
        if target.exists() or target.is_symlink():
            # Don't overwrite existing files (user may have customized them)
            if target.is_symlink():
                # Re-point if it's already a symlink (idempotent)
                current = target.resolve()
                if current != agents_md.resolve():
                    target.unlink()
                    _make_relative_symlink(target, agents_md)
                    logger.info("Updated symlink: %s → AGENTS.md", rel_path)
            continue

        # Create parent dirs if needed (e.g., .github/)
        target.parent.mkdir(parents=True, exist_ok=True)
        _make_relative_symlink(target, agents_md)
        logger.info("Created symlink: %s → AGENTS.md", rel_path)


def _make_relative_symlink(link: Path, target: Path) -> None:
    """Create a relative symlink from link to target."""
    import os

    rel = os.path.relpath(target, link.parent)
    link.symlink_to(rel)


def _copy_default_prompts(prompts_dir: Path) -> None:
    """Copy default prompt templates from the package to the project."""
    pkg_prompts = Path(__file__).parent.parent / "prompts"
    if not pkg_prompts.exists():
        return

    for template_file in pkg_prompts.glob("*.jinja2"):
        dest = prompts_dir / template_file.name
        if not dest.exists():
            shutil.copy2(template_file, dest)
            logger.info("Copied default prompt: %s", template_file.name)


def _ensure_labels(config: Config) -> None:
    """Create required labels on each repo via gh CLI (if available)."""
    labels = config.github.labels
    all_label_values = [
        labels.in_progress,
        labels.blocked,
        labels.waiting_on_user,
        labels.needs_review,
        labels.ready_for_dev,
    ]
    # Flatten: each LabelValue can be a string or list of strings
    flat_labels = []
    for lv in all_label_values:
        flat_labels.extend(_normalize_label(lv))

    for repo_config in config.repositories:
        for label in flat_labels:
            try:
                subprocess.run(
                    ["gh", "label", "create", label, "--repo", repo_config.name, "--force"],
                    capture_output=True,
                    check=False,
                )
            except FileNotFoundError:
                logger.warning("gh CLI not found, skipping label creation")
                return


def install_cron(config: Config, script_path: str = "claudette tick") -> None:
    """Install a cron entry for periodic ticking."""
    project_flag = f"--project {config.project_dir}"
    full_cmd = f"{script_path} {project_flag}"
    interval = config.system.polling_interval_minutes
    cron_line = f"*/{interval} * * * * cd {config.project_dir} && {full_cmd}\n"

    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    if full_cmd in existing:
        logger.info("Cron entry already exists")
        return

    new_crontab = existing.rstrip("\n") + "\n" + cron_line
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_crontab,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        logger.info("Installed cron: %s", cron_line.strip())
    else:
        logger.error("Failed to install cron: %s", proc.stderr)
