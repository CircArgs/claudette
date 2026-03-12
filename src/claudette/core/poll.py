"""The main tick pipeline — runs on every cron invocation."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claudette.core.budget import BudgetTracker
from claudette.core.config import Config, _label_match, _primary_label
from claudette.core.dag import build_dag, find_cycles, get_blocked_issues, get_ready_issues
from claudette.core.identity import Author, parse_author, stamp_manager

if TYPE_CHECKING:
    from claudette.protocols.clock import Clock
    from claudette.protocols.github import GitHubClient, Issue
    from claudette.protocols.llm import LLMClient

logger = logging.getLogger("claudette.poll")


@dataclass
class SessionInfo:
    """Tracks the active manager session."""

    pid: int
    started_at: str
    log_path: str
    issues_included: list[str] = field(default_factory=list)


class TickContext:
    """All dependencies needed to execute a tick, bundled for easy injection."""

    def __init__(
        self,
        config: Config,
        github: GitHubClient,
        llm: LLMClient,
        clock: Clock,
        budget: BudgetTracker,
        state_dir: Path,
        dry_run: bool = False,
        extra_prompt: str | None = None,
    ) -> None:
        self.config = config
        self.github = github
        self.llm = llm
        self.clock = clock
        self.budget = budget
        self.state_dir = state_dir
        self.dry_run = dry_run
        self.extra_prompt = extra_prompt


def run_tick(
    github: GitHubClient,
    llm: LLMClient,
    clock: Clock | None = None,
    config: Config | None = None,
    budget: BudgetTracker | None = None,
    state_dir: Path | None = None,
    dry_run: bool = False,
    extra_prompt: str | None = None,
) -> TickResult:
    """Execute one polling tick across all configured repositories."""
    if config is None:
        raise ValueError("config is required")
    if state_dir is None:
        state_dir = config.state_dir
    if budget is None:
        budget = BudgetTracker(state_dir)
    if clock is None:
        from claudette.core.clock import SystemClock

        clock = SystemClock()

    ctx = TickContext(
        config=config,
        github=github,
        llm=llm,
        clock=clock,
        budget=budget,
        state_dir=state_dir,
        dry_run=dry_run or config.system.dry_run,
        extra_prompt=extra_prompt,
    )

    result = TickResult()

    # Phase 0: Acquire process lock
    lock = acquire_lock(state_dir)
    if lock is None:
        logger.info("Another tick is already running, skipping")
        result.lock_failed = True
        return result

    try:
        return _run_tick_locked(ctx, result, config, clock)
    finally:
        lock.close()


def _run_tick_locked(
    ctx: TickContext,
    result: TickResult,
    config: Config,
    clock: Clock,
) -> TickResult:
    """Execute the tick pipeline while holding the process lock."""
    github = ctx.github
    llm = ctx.llm
    budget = ctx.budget

    # Phase 1: Pre-flight — check for timed-out manager session
    _phase_watchdog(ctx, result)

    # If a manager session is already running, skip this tick
    active_session = _load_manager_session(ctx)
    if active_session is not None:
        logger.info("Manager session PID %d still running, skipping tick", active_session.pid)
        result.session_active = True
        return result

    # Fetch deltas from all repos
    all_issues: list[Issue] = []
    for repo_config in config.repositories:
        repo = repo_config.name

        if _is_paused(ctx, repo):
            logger.info("Skipping paused repo: %s", repo)
            result.skipped_repos.append(repo)
            continue

        if config.budget.pause_on_budget_exceeded and budget.is_exceeded(
            repo, config.budget.max_tokens_per_repo_per_day
        ):
            logger.warning("Budget exceeded for %s, skipping", repo)
            result.skipped_repos.append(repo)
            continue

        since = _read_sync_cursor(ctx, repo)
        try:
            issues = github.fetch_issues(repo, since)
        except Exception as e:
            logger.error("Failed to fetch issues for %s: %s", repo, e)
            result.errors.append(f"fetch failed for {repo}: {e}")
            continue

        all_issues.extend(issues)

    if not all_issues:
        logger.info("No issues to process")
        return result

    # Pre-sync memory so workers have the latest index
    if not ctx.dry_run:
        _sync_memory(config, all_issues, "pre")

    # Phase 2: Build DAG and route
    graph = build_dag(all_issues, config.github.dependency_pattern)

    # Handle cycles
    cycles = find_cycles(graph)
    for cycle in cycles:
        for key in cycle:
            issue = graph.nodes.get(key)
            if issue:
                result.cycle_members.append(key)
                if not ctx.dry_run:
                    blocked_label = _primary_label(config.github.labels.blocked)
                    if blocked_label:
                        github.apply_label(issue.repo, issue.number, blocked_label)
                    github.post_comment(
                        issue.repo,
                        issue.number,
                        stamp_manager(f"Circular dependency detected: {' -> '.join(cycle)}"),
                    )

    blocked = get_blocked_issues(graph)
    ready_keys = get_ready_issues(graph)

    # Apply blocked labels
    for key, _blockers in blocked.items():
        issue = graph.nodes.get(key)
        if issue and not _label_match(issue.labels, config.github.labels.blocked):
            blocked_label = _primary_label(config.github.labels.blocked)
            if not ctx.dry_run and blocked_label:
                github.apply_label(issue.repo, issue.number, blocked_label)
            result.newly_blocked.append(key)

    # Collect issues for the manager session
    routing = config.github.routing
    manager_batch: list[Issue] = []
    prs_for_review: list[Issue] = []
    for key in ready_keys:
        issue = graph.nodes.get(key)
        if issue is None:
            continue

        # Skip ignored issues
        if any(lbl in issue.labels for lbl in routing.ignore_labels):
            continue

        # Skip issues not owned by this user (when owner is set)
        if routing.owner and issue.author != routing.owner:
            continue

        # Deterministic: auto-flag new PRs needing review
        if (
            issue.is_pull_request
            and config.deterministic_rules.auto_review_new_prs
            and not _label_match(issue.labels, config.github.labels.needs_review)
        ):
            logger.info("Flagging PR for review: %s", key)
            if not ctx.dry_run:
                review_label = _primary_label(config.github.labels.needs_review)
                if review_label:
                    github.apply_label(issue.repo, issue.number, review_label)
            prs_for_review.append(issue)
            result.dispatched_reviews.append(key)
            continue

        # Skip issues that aren't ready-for-dev (when require_ready_label is on)
        if (
            not _label_match(issue.labels, config.github.labels.in_progress)
            and routing.require_ready_label
            and not _label_match(issue.labels, config.github.labels.ready_for_dev)
        ):
            continue

        manager_batch.append(issue)

    # Phase 3: Context optimization
    # Fetch comments for issues in the manager batch
    for i, issue in enumerate(manager_batch):
        try:
            full_issue = github.get_issue(issue.repo, issue.number)
            manager_batch[i] = full_issue
        except Exception as e:
            logger.warning("Failed to fetch comments for %s#%d: %s", issue.repo, issue.number, e)

    summary_cache = _load_summary_cache(ctx)
    payload_items: list[dict[str, Any]] = []

    for issue in manager_batch:
        cache_key = f"{issue.repo}#{issue.number}:{len(issue.comments)}"

        if cache_key in summary_cache:
            summary = summary_cache[cache_key]
        elif len(issue.comments) > 3:
            history = "\n".join(c.body for c in issue.comments[:-3])
            try:
                resp = llm.summarize(history)
                summary = resp.text
                budget.record(issue.repo, issue.number, resp.input_tokens + resp.output_tokens)
                summary_cache[cache_key] = summary
            except Exception as e:
                logger.warning("Summarization failed for %s: %s", issue.number, e)
                summary = None
        else:
            summary = None

        recent = issue.comments[-3:] if issue.comments else []
        payload_items.append(
            {
                "repo": issue.repo,
                "number": issue.number,
                "title": issue.title,
                "labels": issue.labels,
                "summary": summary,
                "recent_comments": [{"author": c.author, "body": c.body} for c in recent],
            }
        )

    _save_summary_cache(ctx, summary_cache)

    # Phase 3b: Context optimization for PRs needing review
    for i, pr in enumerate(prs_for_review):
        try:
            full_pr = github.get_issue(pr.repo, pr.number)
            prs_for_review[i] = full_pr
        except Exception as e:
            logger.warning("Failed to fetch PR details for %s#%d: %s", pr.repo, pr.number, e)

    pr_review_items: list[dict[str, Any]] = []
    for pr in prs_for_review:
        # Summarize PR conversation thread if long
        cache_key = f"{pr.repo}#{pr.number}:{len(pr.comments)}"
        if cache_key in summary_cache:
            pr_summary = summary_cache[cache_key]
        elif len(pr.comments) > 3:
            history = "\n".join(c.body for c in pr.comments[:-3])
            try:
                resp = llm.summarize(history)
                pr_summary = resp.text
                budget.record(pr.repo, pr.number, resp.input_tokens + resp.output_tokens)
                summary_cache[cache_key] = pr_summary
            except Exception as e:
                logger.warning("PR summarization failed for %s#%d: %s", pr.repo, pr.number, e)
                pr_summary = None
        else:
            pr_summary = None

        recent = pr.comments[-3:] if pr.comments else []

        # Flatten reviews into a structured list
        review_items = []
        for review in pr.reviews:
            review_data: dict[str, Any] = {
                "author": review.author,
                "state": review.state,
                "body": review.body,
            }
            if review.comments:
                review_data["inline_comments"] = [
                    {"path": c.path or "", "body": c.body} for c in review.comments
                ]
            review_items.append(review_data)

        pr_review_items.append(
            {
                "repo": pr.repo,
                "number": pr.number,
                "title": pr.title,
                "labels": pr.labels,
                "summary": pr_summary,
                "recent_comments": [{"author": c.author, "body": c.body} for c in recent],
                "reviews": review_items,
            }
        )

    if not payload_items and not pr_review_items:
        logger.info("No issues to send to Manager session")
        _update_sync_cursors(ctx, clock)
        return result

    # Phase 4: Assemble manager prompt and launch session
    from jinja2 import Environment, FileSystemLoader

    # Resolve prompt template: project-local first, then package defaults
    prompt_name = config.llm.manager_prompt
    project_prompts = config.prompts_dir
    pkg_prompts = Path(__file__).parent.parent / "prompts"

    prompts_dir = project_prompts if (project_prompts / prompt_name).exists() else pkg_prompts

    env = Environment(loader=FileSystemLoader(str(prompts_dir)))
    template = env.get_template(prompt_name)

    # Build repo listing for the session
    project_root = config.project_dir
    worktree_root = config.worktree_dir
    worktree_root.mkdir(parents=True, exist_ok=True)

    workspace_repos: list[dict[str, str]] = []
    for repo_config in config.repositories:
        safe_name = repo_config.name.replace("/", "_")
        repo_path = repo_config.path or str(project_root / safe_name)
        workspace_repos.append(
            {
                "name": repo_config.name,
                "path": repo_path,
                "safe_name": safe_name,
                "default_branch": repo_config.default_branch,
            }
        )

    prompt = template.render(
        repositories=config.repositories,
        payload=payload_items,
        pr_reviews=pr_review_items,
        labels=config.github.labels,
        workspace_repos=workspace_repos,
        workspace_root=str(project_root),
        worktree_root=str(worktree_root),
        relay_enabled=config.relay.enabled,
        subagents_enabled=config.relay.subagents_enabled,
        relay_dir=str(config.relay_dir),
    )

    if ctx.extra_prompt:
        prompt += f"\n\n## Additional instructions\n\n{ctx.extra_prompt}\n"

    result.issues_in_prompt = [f"{item['repo']}#{item['number']}" for item in payload_items]
    result.prs_in_prompt = [f"{item['repo']}#{item['number']}" for item in pr_review_items]

    if ctx.dry_run:
        logger.info(
            "[DRY-RUN] Would launch manager session with %d issues and %d PRs",
            len(payload_items),
            len(pr_review_items),
        )
        result.dry_run_actions.append(
            f"launch_session issues={result.issues_in_prompt} prs={result.prs_in_prompt}"
        )
        _update_sync_cursors(ctx, clock)
        return result

    # Phase 5: Launch the manager session
    try:
        workspace_cwd = str(project_root)
        session_log = config.log_dir / "sessions"
        session_log.mkdir(parents=True, exist_ok=True)
        log_path = str(session_log / f"session_{clock.now().strftime('%Y%m%d_%H%M%S')}.log")

        proc = llm.launch_manager_session(prompt, workspace_cwd, log_path=log_path)

        session_info = SessionInfo(
            pid=proc.pid,
            started_at=clock.now().isoformat(),
            log_path=log_path,
            issues_included=result.issues_in_prompt + result.prs_in_prompt,
        )
        _save_manager_session(ctx, session_info)
        result.session_launched = True
        result.session_pid = proc.pid
        logger.info(
            "Launched manager session PID %d with %d issues",
            proc.pid,
            len(payload_items) + len(pr_review_items),
        )

    except Exception as e:
        logger.error("Failed to launch manager session: %s", e)
        result.errors.append(f"Manager session launch failed: {e}")

    _update_sync_cursors(ctx, clock)

    # Post-sync memory to capture any changes from the session
    if not ctx.dry_run:
        _sync_memory(config, all_issues, "post")

    return result


class TickResult:
    """Collects outcomes from a tick for logging and testing."""

    def __init__(self) -> None:
        self.skipped_repos: list[str] = []
        self.cycle_members: list[str] = []
        self.newly_blocked: list[str] = []
        self.dispatched_reviews: list[str] = []
        self.issues_in_prompt: list[str] = []
        self.prs_in_prompt: list[str] = []
        self.session_launched: bool = False
        self.session_active: bool = False
        self.session_pid: int | None = None
        self.lock_failed: bool = False
        self.dry_run_actions: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []


# --- Helpers ---


def _sync_memory(config: Config, issues: list, phase: str) -> None:
    """Sync memory index. Incremental — only embeds new/changed docs."""
    if not issues:
        return
    try:
        from claudette.core.memory import MemoryIndex

        memory = MemoryIndex(config.memory_dir, backend=config.memory.backend)
        stats = memory.sync(issues)
        logger.info(
            "Memory %s-sync: +%d updated=%d total=%d",
            phase,
            stats["added"],
            stats["updated"],
            stats["total"],
        )
    except Exception as e:
        logger.warning("Memory %s-sync failed: %s", phase, e)


def _phase_watchdog(ctx: TickContext, result: TickResult) -> None:
    """Check for a timed-out manager session and clean it up."""
    session = _load_manager_session(ctx)
    if session is None:
        return

    timeout_seconds = ctx.config.system.session_timeout_minutes * 60

    try:
        started_at = datetime.fromisoformat(session.started_at)
    except ValueError:
        # Corrupt timestamp, remove the session file
        _clear_manager_session(ctx)
        return

    elapsed = (ctx.clock.now() - started_at).total_seconds()
    if elapsed > timeout_seconds:
        logger.warning(
            "Manager session PID %d exceeded timeout (%dm), terminating",
            session.pid,
            ctx.config.system.session_timeout_minutes,
        )
        _terminate_process(session.pid)
        _clear_manager_session(ctx)
        result.errors.append(
            f"Manager session PID {session.pid} timed out after "
            f"{ctx.config.system.session_timeout_minutes} minutes"
        )
    elif not _is_process_alive(session.pid):
        logger.info("Manager session PID %d has finished, cleaning up", session.pid)
        _clear_manager_session(ctx)


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_process(pid: int, grace_seconds: int = 30) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not _is_process_alive(pid):
            return
        time.sleep(1)

    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def _load_manager_session(ctx: TickContext) -> SessionInfo | None:
    session_file = ctx.state_dir / "manager_session.json"
    if not session_file.exists():
        return None
    try:
        with open(session_file) as f:
            data = json.load(f)
        session = SessionInfo(
            pid=data["pid"],
            started_at=data["started_at"],
            log_path=data.get("log_path", ""),
            issues_included=data.get("issues_included", []),
        )
        # Check if the process is still alive
        if not _is_process_alive(session.pid):
            _clear_manager_session(ctx)
            return None
        return session
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _save_manager_session(ctx: TickContext, session: SessionInfo) -> None:
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    session_file = ctx.state_dir / "manager_session.json"
    with open(session_file, "w") as f:
        json.dump(
            {
                "pid": session.pid,
                "started_at": session.started_at,
                "log_path": session.log_path,
                "issues_included": session.issues_included,
            },
            f,
            indent=2,
        )


def _clear_manager_session(ctx: TickContext) -> None:
    session_file = ctx.state_dir / "manager_session.json"
    if session_file.exists():
        session_file.unlink()


def _is_paused(ctx: TickContext, repo: str) -> bool:
    return repo in ctx.config.paused_repos


def _read_sync_cursor(ctx: TickContext, repo: str) -> datetime:
    safe_name = repo.replace("/", "_")
    cursor_file = ctx.state_dir / f"{safe_name}_sync.txt"
    if cursor_file.exists():
        text = cursor_file.read_text().strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
    # Default to 24 hours ago
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _update_sync_cursors(ctx: TickContext, clock: Clock) -> None:
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    for repo_config in ctx.config.repositories:
        safe_name = repo_config.name.replace("/", "_")
        cursor_file = ctx.state_dir / f"{safe_name}_sync.txt"
        cursor_file.write_text(clock.now().isoformat())


def _has_new_human_comment(issue: Issue) -> bool:
    if not issue.comments:
        return False
    author, _ = parse_author(issue.comments[-1].body)
    return author == Author.HUMAN


def _load_summary_cache(ctx: TickContext) -> dict[str, str]:
    cache_file = ctx.state_dir / "summary_cache.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)
    return {}


def _save_summary_cache(ctx: TickContext, cache: dict[str, str]) -> None:
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    cache_file = ctx.state_dir / "summary_cache.json"
    with open(cache_file, "w") as f:
        json.dump(cache, f)


def _parse_target(target: str, config: Config) -> tuple[str | None, int]:
    """Parse 'owner/repo#N' or '#N' into (repo, number)."""
    target = target.strip()
    if "#" not in target:
        return None, 0

    parts = target.split("#")
    number_str = parts[-1]
    try:
        number = int(number_str)
    except ValueError:
        return None, 0

    repo_part = parts[0] if parts[0] else None
    if repo_part:
        return repo_part, number

    # Default to first repo if no repo specified
    if config.repositories:
        return config.repositories[0].name, number
    return None, 0


def acquire_lock(state_dir: Path) -> Any:
    """Acquire the process lock. Returns the file handle (keep it open) or None if locked."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "manager.lock"
    lock_file = open(lock_path, "w")  # noqa: SIM115 — must stay open as lock
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except BlockingIOError:
        lock_file.close()
        return None
