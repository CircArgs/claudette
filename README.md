# claudette

**Why is everything so hard — just make me a sandwich.**

Claudette is an autonomous GitHub-mediated orchestration system where you collaborate with LLM agents entirely through Issues and Pull Requests. File an issue, walk away, come back to a PR. She watches your repos, coordinates across projects, builds a dependency graph, and launches a claude code session that dispatches sub-agents via git worktrees to write code, run tests, and review PRs -- all while you do literally anything else.

```
claudette watch
```

```
claudette -- status at a glance

  Repos: backend-api * | frontend-repo * | shared-lib [paused]
  Manager session: PID 48291 (12m 34s)
    Issues: backend-api#8, frontend-repo#3

  Queue: 2 ready, 1 blocked, 1 waiting-on-human
  Last tick: 2m ago    Next: ~3m

[every 5s -- Ctrl+C to stop]
```

## What it does

1. **Polls** your GitHub repos on a cron schedule
2. **Builds a dependency graph** across all your repos -- cross-repo blocking just works
3. **Summarizes** long issue threads with a fast one-shot `claude --print` call to save context
4. **Routes** straightforward tasks deterministically -- new PR gets flagged for review, no LLM needed
5. **Assembles a prompt** listing all ready issues and PRs needing review
6. **Launches a claude code session** that dispatches sub-agents into isolated git worktrees
7. **Shows you everything** with `claudette watch` (a watch(1)-style status loop)

## Quick start

### Prerequisites

- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** installed and configured (`claude --help` should work)
- **`gh` CLI** authenticated (`gh auth status` should work)
- Python 3.11+

### Install

```bash
# With uv (recommended)
uv tool install git+https://github.com/CircArgs/claudette.git

# Or with pip
pip install git+https://github.com/CircArgs/claudette.git
```

### Initialize a project

```bash
# Point claudette at a directory containing your repos
claudette init ~/projects/my-saas

# It will:
#   1. Scan the directory for git repos with GitHub remotes
#   2. Let you select which repos to manage
#   3. Validate GitHub access via `gh`
#   4. Optionally customize labels (each can be one or more synonyms, or disabled)
#   5. Configure issue routing (require ready label, ignore labels)
#   6. Bootstrap .claudette/ config, state, and prompts
#   7. Clone any repos that aren't already local
#   8. Create required GitHub labels
#   9. Install Claude Code skills
#  10. Optionally install a cron job for automatic polling
```

### Use it

```bash
cd ~/projects/my-saas

# System status (default command)
claudette

# Preview what a tick would do
claudette tick --dry-run

# Force an immediate tick
claudette tick

# Watch status on a loop
claudette watch
```

No Anthropic API key needed -- claudette uses the `claude` CLI for all LLM inference.

## Configuration

Config lives at `<project-dir>/.claudette/config.yaml`. Created by `claudette init`.

```yaml
project_dir: /home/user/projects/my-saas

system:
  polling_interval_minutes: 5
  session_timeout_minutes: 45
  dry_run: false

repositories:
  - name: owner/frontend-repo
    path: /home/user/projects/my-saas/frontend-repo
    default_branch: main
    labels:
      in_progress:
        - "status: in-progress"
      blocked:
        - "status: blocked"
      waiting_on_user:
        - "status: waiting-on-user"
      needs_review:
        - "status: needs-review"
      ready_for_dev:
        - "status: ready-for-dev"
      paused:
        - "system: paused"
    budget:
      max_tokens_per_issue: 500000
      max_tokens_per_repo_per_day: 5000000
      pause_on_budget_exceeded: true
  - name: owner/backend-api
    default_branch: main

llm:
  manager_prompt: manager.jinja2
  summarizer_prompt: summarizer.jinja2

github:
  dependency_pattern: "Depends on\\s+(?:([\\w-]+/[\\w-]+))?#(\\d+)"
  labels:
    in_progress:
      - "status: in-progress"
    blocked:
      - "status: blocked"
    waiting_on_user:
      - "status: waiting-on-user"
    needs_review:
      - "status: needs-review"
    ready_for_dev:
      - "status: ready-for-dev"
    paused:
      - "system: paused"
  routing:
    require_ready_label: true
    ignore_labels: []

deterministic_rules:
  auto_review_new_prs: true
  default_reviewer_agent: peer-review

relay:
  enabled: false
  command_timeout: 30
  max_pending: 5
  poll_interval: 0.3
  allowed_commands:
    - "git "
    - "gh "
    - "npm "
    - "pytest "
    - "python "
    # ... see config.py for full default list
  blocked_patterns:
    - "rm\\s+-rf\\s+/"
    - "sudo\\s+"
```

Budget is per-repo (configured on each repository entry), not global.

### Labels

Each label in the config can be:

- **A single string:** `in_progress: "WIP"` — one label to match and apply.
- **A YAML list:** multiple synonyms; any label in the list matches when checking issues, and the first one is used when applying labels.
- **An empty list `[]`:** disables that concept entirely (claudette won't check or apply it).

```yaml
labels:
  in_progress:
    - "status: in-progress"
    - "WIP"
  blocked:
    - "status: blocked"
  paused: []  # disable the paused label
```

Labels are defined at two levels: per-repo (under `repositories[].labels`) and globally (under `github.labels`). Per-repo labels override the global defaults.

### Routing

The `github.routing` section controls how claudette decides which issues to pick up:

- **`require_ready_label`** (default: `true`) — when true, issues must have the `ready_for_dev` label before claudette will work on them. When false, any open issue without a blocking label is considered ready.
- **`ignore_labels`** — a list of labels that make claudette ignore issues entirely (it won't show or touch them).

```yaml
github:
  routing:
    require_ready_label: true
    ignore_labels:
      - "wontfix"
      - "question"
```

### Multi-project support

Claudette maintains a global registry at `~/.claudette/projects.json`. Each project is independent with its own config, state, and worktrees.

```bash
# List all registered projects
claudette list

# Target a specific project from anywhere
claudette --project ~/projects/other-saas status
# or
CLAUDETTE_PROJECT=~/projects/other-saas claudette status
```

### Per-repo overrides

Repos can include a `.claudette.yaml` file at their root to override labels, budget, and default branch. These are merged during `claudette init`.

## How it works

```
                    +---------------+
                    |  GitHub API   |
                    |  (via gh CLI) |
                    +-------+-------+
                            |
              +-------------v--------------+
              |   poll.py (cron tick)       |
              |                            |
              |  1. Fetch deltas           |
              |  2. Sync semantic memory   |
              |  3. Build cross-repo DAG   |
              |  4. Prune blocked          |
              |  5. Route deterministic    |
              +-------------+--------------+
                            |
                    +-------v--------+
                    |  Summarizer    |  (claude --print, cached)
                    +-------+--------+
                            |
              +-------------v--------------+
              |   Manager Session          |
              |   (claude code session)    |
              |                            |
              |  Claims issues, creates    |
              |  git worktrees, dispatches |
              |  sub-agents per worktree   |
              +---+----+----+----+---------+
                  |    |    |    |
                  v    v    v    v
              [worktree] [worktree] ...
              sub-agent  sub-agent
              writes code, runs tests,
              opens PRs, reviews PRs
```

### Worktree isolation

Each unit of work gets its own git worktree under `<project-dir>/.claudette/worktrees/`. The manager session creates a worktree per issue, dispatches a sub-agent into it, and cleans up when done. This prevents branch collisions and cross-contamination between concurrent tasks.

### Semantic memory

Claudette maintains a local embedding index over all GitHub issues and PRs using [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M, ~8 MB). Embeddings are stored incrementally (sqlite + numpy). The manager session searches memory before starting each issue to find related past work.

```bash
claudette memory sync                          # index all issues/PRs
claudette memory search "auth bug" --state open # semantic search
claudette memory status                         # index stats
claudette memory clear                          # wipe the index
```

### Command relay

For sandboxed Claude environments where direct shell access is restricted, claudette provides a file-based IPC relay. The relay watchdog runs outside the sandbox, watches for JSON request files, executes allowed commands, and writes JSON response files.

```bash
claudette relay start       # start the watchdog (backgrounds by default)
claudette relay start -f    # run in foreground
claudette relay status      # check if running
claudette relay stop        # stop the watchdog
```

Commands are validated against an allowlist and blocklist defined in config. The relay is documented in the generated AGENTS.md so the manager session knows how to use it.

## CLI commands

| Command | Description |
|---|---|
| `claudette` | System status (same as `claudette status`) |
| `claudette init <project-dir>` | Initialize a project -- discover repos, bootstrap config, install cron |
| `claudette update` | Regenerate AGENTS.md, skills, labels, and prompts from current config |
| `claudette list` | List all registered projects |
| `claudette status` | System health at a glance |
| `claudette watch [-n SECS]` | Refresh status on a loop (default: every 5s) |
| `claudette tick [EXTRA_PROMPT]` | Force an immediate polling cycle |
| `claudette tick --dry-run` | Preview what a tick would do |
| `claudette queue` | Show ready / blocked / waiting issues |
| `claudette queue --ready\|--blocked\|--waiting` | Filter by status |
| `claudette graph [--blocked] [--repo OWNER/NAME]` | Print the dependency tree |
| `claudette session [-f]` | Show (or tail) the active manager session |
| `claudette log [--repo R] [--issue N] [--level L]` | Activity log |
| `claudette why <issue-ref>` | Explain why an issue is in its current state |
| `claudette open <issue-ref>` | Open an issue or PR in the browser |
| `claudette claim <issue-ref>` | Claim an issue so claudette won't work on it |
| `claudette unclaim <issue-ref>` | Release a claimed issue back to claudette |
| `claudette pause <repo>` | Pause automation on a repo |
| `claudette resume <repo>` | Resume a paused repo |
| `claudette config set <key> <value>` | Update a config value |
| `claudette repo add <name> [--path P] [--branch B]` | Add a repo to this project |
| `claudette repo remove <name>` | Remove a repo from config |
| `claudette cron on\|off\|status` | Manage the automatic polling cron job |
| `claudette relay start\|stop\|status` | Manage the command relay watchdog |
| `claudette memory sync\|search\|status\|clear` | Manage the semantic memory index |

Issue references (`<issue-ref>`) accept: `owner/repo#42`, `#42`, or just `42`.

Global option: `--project` / `-p` / `$CLAUDETTE_PROJECT` to target a specific project directory.

## Cross-repo coordination

Claudette builds a unified dependency graph across all configured repos. Use GitHub's native cross-repo reference syntax in issue bodies:

```markdown
Depends on owner/backend-api#8
```

The frontend issue stays blocked until the backend issue closes. The manager session sees all repos in a single prompt and can reason about sequencing, dispatching sub-agents to work on multiple repos in parallel via worktrees.

Circular dependencies are detected and all cycle members are labeled `status: blocked` with a comment explaining the cycle.

## Project layout

```
<project-dir>/
├── repo-a/                          # git clone (base -- never modified directly)
├── repo-b/
├── AGENTS.md                        # generated manager instructions
├── CLAUDE.md -> AGENTS.md           # symlink for Claude Code
└── .claudette/
    ├── config.yaml                  # project config
    ├── prompts/
    │   ├── manager.jinja2           # editable prompt templates
    │   └── summarizer.jinja2
    ├── state/
    │   ├── manager.lock             # fcntl process lock
    │   ├── manager_session.json     # active session tracking
    │   ├── summary_cache.json       # thread summarization cache
    │   ├── budget_owner_repo-a.json
    │   └── owner_repo-a_sync.txt    # sync cursor
    ├── worktrees/                   # git worktrees for sub-agents
    ├── memory/
    │   ├── index.db                 # sqlite metadata
    │   ├── embeddings.npy           # numpy embedding matrix
    │   └── keys.json                # key-to-index mapping
    ├── relay/
    │   ├── relay.pid
    │   ├── requests/                # JSON request files
    │   └── responses/               # JSON response files
    └── logs/
        └── sessions/
```

## Testing

```bash
# Unit tests -- pure logic, no I/O
pytest tests/unit/

# Integration tests -- full pipeline with fake GitHub/LLM
pytest tests/integration/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on the testing architecture.

## License

MIT
