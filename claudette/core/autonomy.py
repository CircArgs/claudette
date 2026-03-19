"""Autonomous work generation — claudette discovers and creates its own issues."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from claudette.core.config import Config, _primary_label
from claudette.core.discovery import discover_coverage_gaps, discover_todo_comments

if TYPE_CHECKING:
    from claudette.protocols.github import GitHubClient

logger = logging.getLogger("claudette.autonomy")

# State file tracking when autonomous mode last ran per repo
_AUTONOMY_STATE_FILE = "autonomy_state.json"


def _load_autonomy_state(state_dir: Path) -> dict:
    """Load the autonomy state (last run times, created issues, etc.)."""
    state_file = state_dir / _AUTONOMY_STATE_FILE
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_autonomy_state(state_dir: Path, state: dict) -> None:
    state_file = state_dir / _AUTONOMY_STATE_FILE
    state_file.write_text(json.dumps(state, indent=2))


def _is_on_cooldown(state: dict, repo: str, cooldown_minutes: int) -> bool:
    """Check if a repo is still in the autonomy cooldown period."""
    last_run = state.get("last_run", {}).get(repo)
    if not last_run:
        return False
    last_dt = datetime.fromisoformat(last_run)
    elapsed = (datetime.now(UTC) - last_dt).total_seconds() / 60
    return elapsed < cooldown_minutes


def _count_open_auto_issues(
    github: GitHubClient, repo: str, auto_label: str
) -> int:
    """Count how many open issues have the auto-created label."""
    try:
        return 1 if github.has_label(repo, auto_label) else 0
    except Exception:
        return 0


def _dedupe_title(title: str, existing_titles: set[str]) -> bool:
    """Check if a similar issue already exists (fuzzy match on title)."""
    normalized = title.lower().strip()
    for existing in existing_titles:
        if normalized == existing.lower().strip():
            return True
        # Check for substring containment (rough dedup)
        if len(normalized) > 20 and (
            normalized in existing.lower() or existing.lower() in normalized
        ):
            return True
    return False


def run_autonomous_discovery(
    config: Config,
    github: GitHubClient,
    state_dir: Path,
    dry_run: bool = False,
) -> list[dict]:
    """Run autonomous discovery and create issues. Returns list of created issues.

    Each returned dict has: {"repo": str, "number": int, "title": str, "mode": str}
    """
    autonomy = config.autonomy
    if not autonomy.enabled:
        return []

    state = _load_autonomy_state(state_dir)
    created: list[dict] = []

    for repo_config in config.repositories:
        repo = repo_config.name

        # Cooldown check
        if _is_on_cooldown(state, repo, autonomy.cooldown_minutes):
            logger.debug("Repo %s is on autonomy cooldown, skipping", repo)
            continue

        # Get existing issue titles for dedup
        existing_titles: set[str] = set()
        try:
            issues = github.fetch_issues(repo, None)
            existing_titles = {i.title for i in issues}
        except Exception as e:
            logger.warning("Failed to fetch issues for dedup on %s: %s", repo, e)

        repo_path = repo_config.path
        if not repo_path:
            repo_path = str(config.project_dir / repo_config.name.replace("/", "_"))

        issues_this_repo = 0

        # Mode: discover — find TODOs, coverage gaps, and file issues
        if "discover" in autonomy.modes:
            issues_this_repo += _discover_mode(
                config, github, repo, repo_path, existing_titles,
                autonomy, created, issues_this_repo, dry_run,
            )

        # Mode: improve — create improvement issues based on code analysis
        if "improve" in autonomy.modes and issues_this_repo < autonomy.max_issues_per_tick:
            issues_this_repo += _improve_mode(
                config, github, repo, repo_path, existing_titles,
                autonomy, created, issues_this_repo, dry_run,
            )

        # Mode: ideate — create feature ideation issues for the agent to explore
        if "ideate" in autonomy.modes and issues_this_repo < autonomy.max_issues_per_tick:
            issues_this_repo += _ideate_mode(
                config, github, repo, repo_path, existing_titles,
                autonomy, created, issues_this_repo, dry_run,
            )

        # Update last run time
        if not dry_run and issues_this_repo > 0:
            state.setdefault("last_run", {})[repo] = datetime.now(UTC).isoformat()

    if not dry_run:
        _save_autonomy_state(state_dir, state)

    return created


def _discover_mode(
    config: Config,
    github: GitHubClient,
    repo: str,
    repo_path: str,
    existing_titles: set[str],
    autonomy: Config.autonomy.__class__,
    created: list[dict],
    issues_this_repo: int,
    dry_run: bool,
) -> int:
    """Create issues from TODO/FIXME comments and coverage gaps. Returns count created."""
    count = 0

    # Group TODOs by file
    todos = discover_todo_comments(repo_path, config.discovery.file_extensions)
    if todos:
        # Group by file for consolidated issues
        by_file: dict[str, list[dict]] = {}
        for todo in todos:
            by_file.setdefault(todo["file"], []).append(todo)

        # Create one issue per file with multiple TODOs, or per TODO for singles
        for file, file_todos in sorted(by_file.items(), key=lambda x: -len(x[1])):
            if issues_this_repo + count >= autonomy.max_issues_per_tick:
                break

            if len(file_todos) > 1:
                title = f"Resolve {len(file_todos)} TODO/FIXME comments in {file}"
            else:
                t = file_todos[0]
                title = f"Resolve {t['type']} in {file}:{t['line']}: {t['text'][:60]}"

            if _dedupe_title(title, existing_titles):
                continue

            body_lines = [f"Auto-discovered TODO/FIXME comments in `{file}`:\n"]
            for t in file_todos:
                body_lines.append(f"- **{t['type']}** (line {t['line']}): {t['text']}")
            body_lines.append(
                "\n---\n*This issue was auto-created by claudette autonomous discovery.*"
            )
            body = "\n".join(body_lines)

            ready_label = _primary_label(config.github.labels.ready_for_dev)
            labels = [autonomy.auto_label]
            if ready_label:
                labels.append(ready_label)

            if dry_run:
                logger.info("[DRY-RUN] Would create issue: %s", title)
            else:
                try:
                    issue = github.create_issue(repo, title, body, labels=labels)
                    created.append({
                        "repo": repo, "number": issue.number,
                        "title": title, "mode": "discover",
                    })
                    existing_titles.add(title)
                    logger.info("Created discovery issue %s#%d: %s", repo, issue.number, title)
                except Exception as e:
                    logger.error("Failed to create issue on %s: %s", repo, e)

            count += 1

    # Coverage gaps
    coverage = discover_coverage_gaps(repo_path, config.discovery.min_coverage_threshold)
    if coverage and issues_this_repo + count < autonomy.max_issues_per_tick:
        low_files = sorted(coverage, key=lambda x: x["coverage"])[:5]
        title = f"Improve test coverage for {len(low_files)} under-covered files"
        if not _dedupe_title(title, existing_titles):
            body_lines = ["Auto-discovered files with low test coverage:\n"]
            for f in low_files:
                body_lines.append(
                    f"- `{f['file']}`: {f['coverage']}% "
                    f"({f['missing_lines']} uncovered lines)"
                )
            body_lines.append(
                "\n---\n*This issue was auto-created by claudette autonomous discovery.*"
            )
            body = "\n".join(body_lines)

            ready_label = _primary_label(config.github.labels.ready_for_dev)
            labels = [autonomy.auto_label]
            if ready_label:
                labels.append(ready_label)

            if dry_run:
                logger.info("[DRY-RUN] Would create coverage issue: %s", title)
            else:
                try:
                    issue = github.create_issue(repo, title, body, labels=labels)
                    created.append({
                        "repo": repo, "number": issue.number,
                        "title": title, "mode": "discover",
                    })
                    existing_titles.add(title)
                except Exception as e:
                    logger.error("Failed to create coverage issue on %s: %s", repo, e)
            count += 1

    return count


def _improve_mode(
    config: Config,
    github: GitHubClient,
    repo: str,
    repo_path: str,
    existing_titles: set[str],
    autonomy: Config.autonomy.__class__,
    created: list[dict],
    issues_this_repo: int,
    dry_run: bool,
) -> int:
    """Create an improvement-focused issue for the agent to work on. Returns count created."""
    count = 0

    # Build a targeted improvement issue based on what's enabled
    targets = autonomy.improve_targets
    if not targets:
        return 0

    title = f"Autonomous improvement: analyze and improve {repo.split('/')[-1]}"
    if _dedupe_title(title, existing_titles):
        return 0

    body_parts = [
        "## Autonomous Improvement Task\n",
        "Analyze this repository and make targeted improvements. "
        "Focus on the following areas:\n",
    ]

    target_descriptions = {
        "test_coverage": (
            "**Test Coverage** — identify untested code paths and write tests. "
            "Run the existing test suite first to understand current coverage."
        ),
        "error_handling": (
            "**Error Handling** — find places where errors are silently swallowed, "
            "exceptions are too broad, or error messages are unhelpful. Fix them."
        ),
        "performance": (
            "**Performance** — look for N+1 queries, unnecessary loops, "
            "missing caching opportunities, or inefficient algorithms."
        ),
        "documentation": (
            "**Documentation** — add or improve docstrings for public APIs, "
            "update README if it's stale, add type hints where missing."
        ),
        "dead_code": (
            "**Dead Code** — find and remove unused imports, unreachable code, "
            "commented-out blocks, and stale feature flags."
        ),
        "type_safety": (
            "**Type Safety** — add type annotations, fix mypy/pyright errors, "
            "replace `Any` with concrete types where possible."
        ),
    }

    for target in targets:
        desc = target_descriptions.get(target)
        if desc:
            body_parts.append(f"- {desc}")

    body_parts.append(
        "\n### Guidelines\n"
        "- Make small, focused commits — one improvement per commit\n"
        "- Run tests after each change to avoid regressions\n"
        "- Open a PR with a clear summary of what was improved and why\n"
        "- If you find critical issues, create separate issues for them\n"
        "\n---\n*This issue was auto-created by claudette autonomous improvement mode.*"
    )

    body = "\n".join(body_parts)
    ready_label = _primary_label(config.github.labels.ready_for_dev)
    labels = [autonomy.auto_label]
    if ready_label:
        labels.append(ready_label)

    if issues_this_repo + count >= autonomy.max_issues_per_tick:
        return 0

    if dry_run:
        logger.info("[DRY-RUN] Would create improvement issue: %s", title)
    else:
        try:
            issue = github.create_issue(repo, title, body, labels=labels)
            created.append({
                "repo": repo, "number": issue.number,
                "title": title, "mode": "improve",
            })
            existing_titles.add(title)
            logger.info("Created improvement issue %s#%d: %s", repo, issue.number, title)
        except Exception as e:
            logger.error("Failed to create improvement issue on %s: %s", repo, e)
    count += 1
    return count


def _ideate_mode(
    config: Config,
    github: GitHubClient,
    repo: str,
    repo_path: str,
    existing_titles: set[str],
    autonomy: Config.autonomy.__class__,
    created: list[dict],
    issues_this_repo: int,
    dry_run: bool,
) -> int:
    """Create a feature ideation issue — the agent explores what could be built."""
    count = 0

    targets = autonomy.ideate_targets
    if not targets:
        return 0

    title = f"Feature ideation: explore improvements for {repo.split('/')[-1]}"
    if _dedupe_title(title, existing_titles):
        return 0

    body_parts = [
        "## Feature Ideation Task\n",
        "Analyze this repository's functionality and propose new features or "
        "improvements. Think creatively about what would make this project better.\n",
        "### Focus Areas\n",
    ]

    ideation_descriptions = {
        "developer_experience": (
            "**Developer Experience** — better CLI output, smarter defaults, "
            "helpful error messages, autocompletion, configuration validation"
        ),
        "observability": (
            "**Observability** — structured logging, metrics endpoints, "
            "health checks, tracing, dashboards"
        ),
        "security": (
            "**Security** — input validation, auth improvements, secrets handling, "
            "dependency auditing, HTTPS enforcement"
        ),
        "accessibility": (
            "**Accessibility** — if this has a UI: ARIA labels, keyboard nav, "
            "color contrast, screen reader support"
        ),
    }

    for target in targets:
        desc = ideation_descriptions.get(target)
        if desc:
            body_parts.append(f"- {desc}")

    body_parts.append(
        "\n### Instructions\n"
        "1. Analyze the codebase to understand what the project does\n"
        "2. Identify 3-5 concrete, implementable improvements\n"
        "3. For each: describe the feature, explain the value, estimate complexity\n"
        "4. Pick the highest-value, lowest-complexity improvement and implement it\n"
        "5. Create separate issues for the remaining ideas (with the ready-for-dev label)\n"
        "6. Open a PR for the one you implemented\n"
        "\n---\n*This issue was auto-created by claudette autonomous ideation mode.*"
    )

    body = "\n".join(body_parts)
    ready_label = _primary_label(config.github.labels.ready_for_dev)
    labels = [autonomy.auto_label]
    if ready_label:
        labels.append(ready_label)

    if issues_this_repo + count >= autonomy.max_issues_per_tick:
        return 0

    if dry_run:
        logger.info("[DRY-RUN] Would create ideation issue: %s", title)
    else:
        try:
            issue = github.create_issue(repo, title, body, labels=labels)
            created.append({
                "repo": repo, "number": issue.number,
                "title": title, "mode": "ideate",
            })
            existing_titles.add(title)
            logger.info("Created ideation issue %s#%d: %s", repo, issue.number, title)
        except Exception as e:
            logger.error("Failed to create ideation issue on %s: %s", repo, e)
    count += 1
    return count
