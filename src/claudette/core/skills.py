"""Install claudette's Claude Code skills into the project's .claude/skills/.

Skills are scoped:
  - worker: available to worker sub-agents in worktrees
  - manager: available to the manager session (includes worker skills)
  - human: only for the human operator (not installed as skills)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# ── Worker skills (also available to manager) ────────────────────────────

WORKER_SKILLS: dict[str, str] = {
    "claudette-memory": """\
---
name: claudette-memory
description: Search for related issues/PRs or sync the local semantic index. Use automatically when looking for similar past work or needing context.
argument-hint: [search|sync|status] [query]
---

# claudette memory — semantic search

## Search
```bash
claudette memory search "authentication bug"
claudette memory search "deployment pipeline" --state open
claudette memory search "refactor" -n 5
```
- `--state open|closed` — filter by state
- `-n`, `--limit` — max results (default 10)

## Sync the index
```bash
claudette memory sync
```

## Check stats
```bash
claudette memory status
```
""",
    "claudette-issues": """\
---
name: claudette-issues
description: Create issues, declare dependencies, or check issue status. Use when filing follow-up issues, breaking work into steps, or understanding why something is blocked.
argument-hint: [create|depends] ...
---

# claudette issues — create and manage

## Create an issue
```bash
claudette issue create "Title"
claudette issue create "Title" --body "Details" --repo owner/repo --ready
```
- `--body`, `-b` — issue body
- `--repo`, `-r` — target repo (defaults to first configured)
- `--ready` — also apply the ready-for-dev label

## Declare a dependency
```bash
claudette issue depends 42 --on 38
claudette issue depends 42 --on owner/other-repo#15
```
Appends `Depends on #38` to the issue body. The dependent issue will show as blocked until the dependency closes.

## Check issue state
```bash
claudette why 42
```
""",
    "claudette-labels": """\
---
name: claudette-labels
description: Change the status of an issue — mark it ready, blocked, or waiting on human input. Use when you finish work, hit a blocker, or need human input.
argument-hint: [ready|unready|block|unblock|wait|unwait] <issue-ref>
---

# claudette labels — issue status

All commands map to configured labels. Issue refs: `42`, `#42`, or `owner/repo#42`.

## Ready for work
```bash
claudette ready 42       # mark ready for claudette
claudette unready 42     # remove ready label
```

## Blocked
```bash
claudette block 42       # mark blocked
claudette unblock 42     # remove blocked
```

## Waiting on human
```bash
claudette wait 42        # needs human input
claudette unwait 42      # human responded, resume
```
When using `wait`, also post a comment explaining what you need.
""",
}

# ── Manager-only skills ──────────────────────────────────────────────────

MANAGER_SKILLS: dict[str, str] = {
    "claudette-orchestration": """\
---
name: claudette-orchestration
description: System overview, queue inspection, and repo management. Use when checking what's ready to work on, viewing the dependency graph, or managing repos.
argument-hint: [status|queue|graph|pause|resume|session|log] ...
---

# claudette orchestration — system management

## System status
```bash
claudette
claudette status
```

## Issue queue
```bash
claudette queue
claudette queue --ready
claudette queue --blocked
claudette queue --waiting
```

## Dependency graph
```bash
claudette graph
claudette graph --blocked
claudette graph --repo owner/repo
```

## Pause/resume a repo
```bash
claudette pause owner/repo
claudette resume owner/repo
```

## Active session
```bash
claudette session
claudette session -f     # tail log
```

## Activity log
```bash
claudette log
claudette log --repo owner/repo --issue 42 --level warning
```
""",
}


def install_skills(project_dir: Path, scope: str = "worker") -> list[str]:
    """Install skills into <project_dir>/.claude/skills/.

    scope:
      "worker" — only worker-scoped skills (for worktree .claude/)
      "manager" — worker + manager skills (for project root .claude/)
      "all" — worker + manager skills

    Relay instructions are NOT installed as skills — they live in AGENTS.md
    so they're always active without requiring explicit invocation.
    """
    skills_dir = project_dir / ".claude" / "skills"
    skills: dict[str, str] = {}

    skills.update(WORKER_SKILLS)
    if scope in ("manager", "all"):
        skills.update(MANAGER_SKILLS)

    installed = []
    for name, content in skills.items():
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        installed.append(name)
    return installed
