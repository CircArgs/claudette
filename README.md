# claudette

**Why is everything so hard -- just make me a sandwich.**

Claudette is an autonomous GitHub-mediated orchestration system. File an issue, walk away, come back to a PR. She watches your repos, builds a cross-repo dependency graph, and launches Claude Code sessions that dispatch sub-agents via git worktrees to write code, run tests, review PRs, and merge them -- all while you do literally anything else.

## Install

```bash
curl -sSL https://raw.githubusercontent.com/CircArgs/claudette/main/install.sh | bash
```

Requires: [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code), [`gh` CLI](https://cli.github.com/) (authenticated), Python 3.11+.

## Quick start

```bash
claudette init ~/projects/my-saas    # discover repos, configure, install cron
cd ~/projects/my-saas
claudette                            # status at a glance
claudette watch                      # Rich TUI dashboard
claudette tick --dry-run             # preview what a tick would do
claudette issue create               # interactively file an issue
```

No Anthropic API key needed -- claudette uses the `claude` CLI for all LLM inference.

## What it does

1. **Polls** your GitHub repos on a cron schedule
2. **Builds a dependency graph** across repos -- cross-repo blocking just works
3. **Summarizes** long threads to save context
4. **Routes** deterministically -- PRs get flagged for review, no LLM needed
5. **Auto-merges** approved PRs with passing CI
6. **Launches Claude Code sessions** that dispatch sub-agents into isolated git worktrees
7. **Retries or escalates** stale issues to humans
8. **Generates its own work** -- finding TODOs, improving code quality, ideating features
9. **Tracks metrics** and sends webhook notifications

```
              GitHub API (via gh CLI)
                      |
        +-------------v--------------+
        |   poll.py (cron tick)       |
        |                            |
        |  fetch → DAG → route →     |
        |  stale detect → autonomy → |
        |  summarize → auto-merge    |
        +-------------+--------------+
                      |
              +-------v--------+
              | Manager Session |
              | (claude code)   |
              +---+----+----+--+
                  |    |    |
                  v    v    v
              [worktree] [worktree] ...
              sub-agents write code,
              run tests, open PRs
```

## CLI

| Command | Description |
|---|---|
| `claudette` | System status |
| `claudette init <dir>` | Initialize a project |
| `claudette watch [--simple]` | Rich TUI dashboard |
| `claudette tick [--dry-run]` | Force a polling cycle |
| `claudette issue create` | Create an issue (interactive) |
| `claudette queue` | Show issue queue |
| `claudette metrics [--days N]` | Metrics summary |
| `claudette graph` | Dependency tree |
| `claudette why <ref>` | Explain an issue's state |
| `claudette autonomy on\|off\|status\|run` | Autonomous work generation |
| `claudette cron on\|off\|status` | Manage cron polling |
| `claudette memory sync\|search\|status` | Semantic memory index |
| `claudette relay start\|stop\|status` | Command relay (sandboxed envs) |
| `claudette pause/resume <repo>` | Pause/resume a repo |
| `claudette claim/unclaim <ref>` | Claim/release an issue |
| `claudette open <ref>` | Open in browser |

[Full CLI reference](docs/cli.md)

## Docs

- **[Configuration](docs/configuration.md)** -- config.yaml reference, labels, routing, budgets
- **[Autonomy](docs/autonomy.md)** -- discover, improve, ideate modes
- **[Pipeline](docs/pipeline.md)** -- multi-stage agent pipeline (scout/architect/builder/tester/reviewer)
- **[Auto-merge & PR rules](docs/pr-rules.md)** -- merge methods, auto-review, retry/escalation
- **[Metrics & notifications](docs/metrics.md)** -- tracking, webhooks, dashboard
- **[Memory & search](docs/memory.md)** -- semantic index backends
- **[Command relay](docs/relay.md)** -- file-based IPC for sandboxed environments
- **[Cross-repo coordination](docs/cross-repo.md)** -- dependency graph, blocking, cycles
- **[CLI reference](docs/cli.md)** -- every command and option

## Project layout

```
<project-dir>/
├── repo-a/                    # git clones (never modified directly)
├── repo-b/
├── AGENTS.md                  # generated manager instructions
├── CLAUDE.md -> AGENTS.md
└── .claudette/
    ├── config.yaml
    ├── prompts/               # editable Jinja2 templates
    ├── state/                 # sync cursors, metrics, session tracking
    ├── worktrees/             # isolated git worktrees per issue
    ├── memory/                # semantic search index
    ├── relay/                 # IPC (when enabled)
    └── logs/sessions/
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests: `pytest tests/` -- full pipeline runs with fakes, no network or LLM calls needed.

## License

MIT
