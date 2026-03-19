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

from claudette.core.autonomy import run_autonomous_discovery
from claudette.core.budget import BudgetTracker
from claudette.core.config import Config, _label_match, _primary_label
from claudette.core.dag import build_dag, find_cycles, get_blocked_issues, get_ready_issues
from claudette.core.identity import Author, parse_author, stamp_manager
from claudette.core.metrics import MetricsStore
from claudette.core.notifications import notify

if TYPE_CHECKING:
    from claudette.protocols.clock import Clock
    from claudette.protocols.forge import ForgeClient, Issue
    from claudette.protocols.llm import LLMClient

    # Backward-compat alias used in type annotations throughout this module
    GitHubClient = ForgeClient

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
        self.metrics = MetricsStore(state_dir)


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

    # Record tick event
    ctx.metrics.record("tick")

    # Phase 1: Pre-flight — check for timed-out manager session
    _phase_watchdog(ctx, result)

    # Phase 1b: Detect stale in-progress issues and re-queue them
    _phase_stale_issues(ctx, result)

    # If a manager session is already running, skip this tick
    active_session = _load_manager_session(ctx)
    if active_session is not None:
        logger.info("Manager session PID %d still running, skipping tick", active_session.pid)
        result.session_active = True
        return result

    # Fetch deltas from all repos
    all_issues: list[Issue] = []
    issues_by_repo: dict[str, list[Issue]] = {}
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
            ctx.metrics.record("error", repo=repo, detail=f"fetch failed: {e}")
            notify(config.notifications, "error", f"Tick error: {e}")
            continue

        issues_by_repo[repo] = issues
        all_issues.extend(issues)

    # Phase 1c: Autonomous work generation
    if config.autonomy.enabled:
        idle = not all_issues
        if idle and config.autonomy.run_on_idle:
            logger.info("No human issues — running autonomous discovery")
            auto_created = run_autonomous_discovery(
                config, github, ctx.state_dir, dry_run=ctx.dry_run,
            )
            for ac in auto_created:
                ctx.metrics.record("auto_issue_created", repo=ac["repo"], mode=ac["mode"])
                result.auto_created_issues.append(f"{ac['repo']}#{ac['number']}")
            # Re-fetch issues so newly created ones get included in this tick
            if auto_created and not ctx.dry_run:
                for repo_config in config.repositories:
                    repo = repo_config.name
                    if _is_paused(ctx, repo):
                        continue
                    since = _read_sync_cursor(ctx, repo)
                    try:
                        issues = github.fetch_issues(repo, since)
                        issues_by_repo[repo] = issues
                        all_issues = []
                        for repo_issues in issues_by_repo.values():
                            all_issues.extend(repo_issues)
                    except Exception:
                        pass
        elif not idle and "discover" in config.autonomy.modes:
            # Non-idle tick — still run discovery in background for next tick
            auto_created = run_autonomous_discovery(
                config, github, ctx.state_dir, dry_run=ctx.dry_run,
            )
            for ac in auto_created:
                ctx.metrics.record("auto_issue_created", repo=ac["repo"], mode=ac["mode"])
                result.auto_created_issues.append(f"{ac['repo']}#{ac['number']}")

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
            ctx.metrics.record("pr_opened", repo=issue.repo, pr=key)
            continue

        # Skip issues that aren't ready-for-dev (when require_ready_label is on)
        if (
            not _label_match(issue.labels, config.github.labels.in_progress)
            and routing.require_ready_label
            and not _label_match(issue.labels, config.github.labels.ready_for_dev)
        ):
            continue

        # Retry with escalation: check if issue is in_progress but session finished
        if _label_match(issue.labels, config.github.labels.in_progress):
            issue_key = f"{issue.repo}#{issue.number}"
            retry_count = _get_retry_count(ctx.state_dir, issue_key)
            max_retries = config.system.max_retries_per_issue
            if retry_count >= max_retries:
                # Escalate: remove in_progress, apply waiting_on_user
                if not ctx.dry_run:
                    in_progress_label = _primary_label(config.github.labels.in_progress)
                    if in_progress_label:
                        github.remove_label(issue.repo, issue.number, in_progress_label)
                    waiting_label = _primary_label(config.github.labels.waiting_on_user)
                    if waiting_label:
                        github.apply_label(issue.repo, issue.number, waiting_label)
                    github.post_comment(
                        issue.repo,
                        issue.number,
                        stamp_manager(
                            f"Failed after {retry_count} attempt(s). Needs human attention."
                        ),
                    )
                result.escalated.append(issue_key)
                ctx.metrics.record("issue_escalated", repo=issue.repo, issue=issue_key)
                logger.info("Escalated issue %s after %d retries", issue_key, retry_count)
                continue
            else:
                # Retry: increment counter, include in batch
                _increment_retry(ctx.state_dir, issue_key)
                logger.info(
                    "Retrying issue %s (attempt %d/%d)",
                    issue_key,
                    retry_count + 1,
                    max_retries,
                )

        manager_batch.append(issue)

    # Detect PRs needing revision (changes_requested reviews)
    prs_needing_revision: list[Issue] = []
    for key in ready_keys:
        issue = graph.nodes.get(key)
        if issue is None or not issue.is_pull_request:
            continue
        if any(lbl in issue.labels for lbl in routing.ignore_labels):
            continue
        if routing.owner and issue.author != routing.owner:
            continue
        # Check if any review requested changes
        if any(r.state == "CHANGES_REQUESTED" for r in issue.reviews):
            if _label_match(issue.labels, config.github.labels.in_progress):
                continue  # Already being worked on
            prs_needing_revision.append(issue)
            result.prs_needing_revision.append(key)

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

    # Phase 3c: Context optimization for PRs needing revision
    for i, pr in enumerate(prs_needing_revision):
        try:
            full_pr = github.get_issue(pr.repo, pr.number)
            prs_needing_revision[i] = full_pr
        except Exception as e:
            logger.warning("Failed to fetch PR details for %s#%d: %s", pr.repo, pr.number, e)

    pr_revision_items: list[dict[str, Any]] = []
    for pr in prs_needing_revision:
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

        pr_revision_items.append(
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

    if not payload_items and not pr_review_items and not pr_revision_items:
        logger.info("No issues to send to Manager session")
        _update_sync_cursors(ctx, clock, issues_by_repo)
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

    # Compute active pipeline stages
    pipeline = config.pipeline
    active_stages = [s for s in pipeline.stages if s not in pipeline.skip_stages]

    prompt = template.render(
        repositories=config.repositories,
        payload=payload_items,
        pr_reviews=pr_review_items,
        pr_revisions=pr_revision_items,
        labels=config.github.labels,
        workspace_repos=workspace_repos,
        workspace_root=str(project_root),
        worktree_root=str(worktree_root),
        relay_enabled=config.relay.enabled,
        subagents_enabled=config.relay.subagents_enabled,
        relay_dir=str(config.relay_dir),
        pipeline_enabled=pipeline.enabled,
        pipeline_stages=active_stages,
    )

    if ctx.extra_prompt:
        prompt += f"\n\n## Additional instructions\n\n{ctx.extra_prompt}\n"

    result.issues_in_prompt = [f"{item['repo']}#{item['number']}" for item in payload_items]
    result.prs_in_prompt = [f"{item['repo']}#{item['number']}" for item in pr_review_items]
    result.prs_revision_in_prompt = [
        f"{item['repo']}#{item['number']}" for item in pr_revision_items
    ]

    if ctx.dry_run:
        logger.info(
            "[DRY-RUN] Would launch manager session with %d issues, "
            "%d PRs for review, %d PRs for revision",
            len(payload_items),
            len(pr_review_items),
            len(pr_revision_items),
        )
        result.dry_run_actions.append(
            f"launch_session issues={result.issues_in_prompt} "
            f"prs={result.prs_in_prompt} "
            f"revisions={result.prs_revision_in_prompt}"
        )
        _update_sync_cursors(ctx, clock, issues_by_repo)
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
            issues_included=(
                result.issues_in_prompt + result.prs_in_prompt + result.prs_revision_in_prompt
            ),
        )
        _save_manager_session(ctx, session_info)
        result.session_launched = True
        result.session_pid = proc.pid
        ctx.metrics.record("session_launched", issues=len(payload_items) + len(pr_review_items))
        logger.info(
            "Launched manager session PID %d with %d issues",
            proc.pid,
            len(payload_items) + len(pr_review_items) + len(pr_revision_items),
        )

        notify(
            config.notifications,
            "session_launched",
            f"Manager session started with {len(payload_items)} issues "
            f"and {len(pr_review_items)} PRs",
        )

    except Exception as e:
        logger.error("Failed to launch manager session: %s", e)
        result.errors.append(f"Manager session launch failed: {e}")
        ctx.metrics.record("error", detail=f"session launch failed: {e}")
        notify(config.notifications, "error", f"Tick error: {e}")

    # Phase 6: Auto-merge approved PRs
    _phase_auto_merge(ctx, result, graph)

    _update_sync_cursors(ctx, clock, issues_by_repo)

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
        self.prs_needing_revision: list[str] = []
        self.stale_requeued: list[str] = []
        self.issues_in_prompt: list[str] = []
        self.prs_in_prompt: list[str] = []
        self.prs_revision_in_prompt: list[str] = []
        self.session_launched: bool = False
        self.session_active: bool = False
        self.session_pid: int | None = None
        self.lock_failed: bool = False
        self.dry_run_actions: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.auto_merged: list[str] = []
        self.escalated: list[str] = []
        self.auto_created_issues: list[str] = []


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


def _phase_stale_issues(ctx: TickContext, result: TickResult) -> None:
    """Detect stale in-progress issues and re-queue them.

    This is a placeholder — the full implementation lives in the stale-issue
    detection feature.  Defining it here prevents NameError when the call
    site in ``_run_tick_locked`` executes.
    """


def _phase_auto_merge(ctx: TickContext, result: TickResult, graph: Any) -> None:
    """Auto-merge PRs that are approved and have passing CI."""
    config = ctx.config
    if not config.deterministic_rules.auto_merge_approved_prs:
        return
    if ctx.dry_run:
        return

    merge_method = config.deterministic_rules.auto_merge_method
    github = ctx.github

    for key, issue in graph.nodes.items():
        if not issue.is_pull_request:
            continue

        # Check for an APPROVED review
        has_approval = any(r.state == "APPROVED" for r in issue.reviews)
        if not has_approval:
            continue

        # Check if CI is passing via the forge client protocol
        try:
            ci_ok = github.check_ci_status(issue.repo, issue.number)
            if not ci_ok:
                logger.debug("CI not passing for %s, skipping auto-merge", key)
                continue
        except Exception as e:
            logger.warning("Failed to check CI for %s: %s", key, e)
            continue

        # Merge the PR via the forge client protocol
        try:
            merged = github.merge_pr(issue.repo, issue.number, method=merge_method)
            if merged:
                result.auto_merged.append(key)
                ctx.metrics.record("pr_merged", repo=issue.repo, pr=key)
                logger.info("Auto-merged PR %s via %s", key, merge_method)
            else:
                logger.warning("Auto-merge failed for %s", key)
        except Exception as e:
            logger.warning("Auto-merge failed for %s: %s", key, e)


def _is_process_alive(pid: int) -> bool:
    """Check whether a process is alive (not zombie, not dead)."""
    try:
        status_file = Path(f"/proc/{pid}/status")
        if status_file.exists():
            for line in status_file.read_text().splitlines():
                if line.startswith("State:"):
                    return "Z" not in line  # Z = zombie
            return True
        # /proc not available (macOS) — fall back to kill(0)
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


def _update_sync_cursors(
    ctx: TickContext, clock: Clock, issues_by_repo: dict[str, list] | None = None
) -> None:
    """Advance sync cursors based on what we actually fetched.

    Only advances a repo's cursor to the max updated_at of issues we saw.
    If we saw zero issues for a repo, the cursor stays put so we don't
    skip over issues created between ticks.
    """
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    for repo_config in ctx.config.repositories:
        safe_name = repo_config.name.replace("/", "_")
        cursor_file = ctx.state_dir / f"{safe_name}_sync.txt"

        if issues_by_repo and repo_config.name in issues_by_repo:
            repo_issues = issues_by_repo[repo_config.name]
            if repo_issues:
                # Advance to the latest updated_at we actually saw
                max_ts = max(
                    (i.updated_at for i in repo_issues if i.updated_at),
                    default=None,
                )
                if max_ts:
                    cursor_file.write_text(max_ts.isoformat())
                    continue

        # No issues fetched for this repo — don't advance the cursor.
        # This prevents skipping issues created between ticks.


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


# --- Retry tracking helpers ---


def _get_retry_count(state_dir: Path, issue_key: str) -> int:
    retry_file = state_dir / "retries.json"
    if not retry_file.exists():
        return 0
    data = json.loads(retry_file.read_text())
    return data.get(issue_key, 0)


def _increment_retry(state_dir: Path, issue_key: str) -> int:
    retry_file = state_dir / "retries.json"
    data = json.loads(retry_file.read_text()) if retry_file.exists() else {}
    data[issue_key] = data.get(issue_key, 0) + 1
    retry_file.write_text(json.dumps(data))
    return data[issue_key]


def _clear_retry(state_dir: Path, issue_key: str) -> None:
    retry_file = state_dir / "retries.json"
    if not retry_file.exists():
        return
    data = json.loads(retry_file.read_text())
    data.pop(issue_key, None)
    retry_file.write_text(json.dumps(data))


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
