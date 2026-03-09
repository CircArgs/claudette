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


RELAY_SKILLS: dict[str, str] = {
    "claudette-relay": """\
---
name: claudette-relay
description: ALL command execution MUST go through the relay. You do not have direct shell access. Write JSON request files to run commands or spawn subagents — the relay watchdog executes them on your behalf.
argument-hint: (file-based IPC)
---

# claudette relay — MANDATORY command execution interface

**You do NOT have direct shell access. ALL commands MUST go through the relay.**

Do NOT use subprocess, os.system, or run shell commands directly — they will fail.
A watchdog process runs outside the sandbox and executes requests on your behalf.

## Running a command

1. Write a request file (write to .tmp first, then rename for atomicity):
```bash
# Write to temp file first
cat > .claudette/relay/requests/mycmd.json.tmp << 'REQEOF'
{"id": "mycmd", "command": "git status", "cwd": "/path/to/repo", "timeout": 30}
REQEOF
mv .claudette/relay/requests/mycmd.json.tmp .claudette/relay/requests/mycmd.json
```

2. Poll for the response:
```bash
cat .claudette/relay/responses/mycmd.json
# {"id": "mycmd", "returncode": 0, "stdout": "...", "stderr": "...", "timed_out": false, "error": null}
```

## Spawning a subagent

For tasks that need a full Claude session (not just a shell command):

1. Write a subagent request:
```bash
cat > .claudette/relay/subagents/requests/worker1.json.tmp << 'REQEOF'
{"id": "worker1", "prompt": "Implement the auth middleware in src/auth.py. Write tests.", "cwd": "/path/to/worktree", "timeout": 1800, "print_mode": false}
REQEOF
mv .claudette/relay/subagents/requests/worker1.json.tmp .claudette/relay/subagents/requests/worker1.json
```

2. Poll for status (updates from pending → running → completed/failed):
```bash
cat .claudette/relay/subagents/responses/worker1.json
# {"id": "worker1", "status": "running", "pid": 12345, "output": "", "error": null}
# ... later ...
# {"id": "worker1", "status": "completed", "pid": 12345, "output": "...", "error": null}
```

### Fields

**Request:**
- `id` — unique identifier
- `prompt` — the prompt for the subagent
- `cwd` — working directory (typically a worktree)
- `timeout` — max seconds (default 1800 / 30 min)
- `print_mode` — `true` for one-shot `claude --print`, `false` for full session

**Response:**
- `status` — `pending`, `running`, `completed`, `failed`, `timed_out`
- `pid` — process ID (once running)
- `output` — stdout from the session (once completed)
- `error` — error message (if failed)

## Important

- **ALL commands go through the relay — no exceptions.** Never run commands directly.
- Always write to `.tmp` first, then rename — prevents the watchdog from reading partial files
- The watchdog validates commands against an allowlist before executing
- Subagent sessions run as full `claude` CLI invocations
""",
}


def install_skills(
    project_dir: Path, scope: str = "worker", relay_enabled: bool = False
) -> list[str]:
    """Install skills into <project_dir>/.claude/skills/.

    scope:
      "worker" — only worker-scoped skills (for worktree .claude/)
      "manager" — worker + manager skills (for project root .claude/)
      "all" — worker + manager + relay (legacy, same as manager + relay)

    relay_enabled:
      When True, relay skills are installed regardless of scope — ALL Claude
      instances must use the relay when it's enabled.
    """
    skills_dir = project_dir / ".claude" / "skills"
    skills: dict[str, str] = {}

    skills.update(WORKER_SKILLS)
    if scope in ("manager", "all"):
        skills.update(MANAGER_SKILLS)
    if relay_enabled or scope in ("relay", "all"):
        skills.update(RELAY_SKILLS)

    installed = []
    for name, content in skills.items():
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        installed.append(name)
    return installed
