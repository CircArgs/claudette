# CLI reference

Global option: `--project` / `-p` / `$CLAUDETTE_PROJECT` to target a specific project.

Issue references (`<ref>`) accept: `owner/repo#42`, `#42`, or just `42`.

## Core

| Command | Description |
|---|---|
| `claudette` | System status (same as `claudette status`) |
| `claudette status` | System health at a glance |
| `claudette init <dir>` | Initialize a project -- discover repos, configure, install cron |
| `claudette update` | Self-update to latest version |
| `claudette refresh` | Regenerate AGENTS.md, skills, labels, prompts from config |
| `claudette list` | List all registered projects |

## Tick and watch

| Command | Description |
|---|---|
| `claudette tick [EXTRA_PROMPT]` | Force an immediate polling cycle |
| `claudette tick --dry-run` | Preview what a tick would do |
| `claudette watch [-n SECS]` | Rich TUI dashboard (default: 5s refresh) |
| `claudette watch --simple` | Plain text status loop |

## Issues

| Command | Description |
|---|---|
| `claudette queue` | Show ready / blocked / waiting issues |
| `claudette queue --ready\|--blocked\|--waiting` | Filter by status |
| `claudette graph [--blocked] [--repo NAME]` | Print the dependency tree |
| `claudette why <ref>` | Explain why an issue is in its current state |
| `claudette open <ref>` | Open an issue or PR in the browser |
| `claudette session [-f]` | Show (or tail) the active manager session |
| `claudette log [--repo R] [--issue N] [--level L]` | Activity log |

## Issue creation

| Command | Description |
|---|---|
| `claudette issue create` | Fully interactive |
| `claudette issue create "Title"` | Provide title, interactive for the rest |
| `claudette issue create "Title" -b "Body"` | Title + body |
| `claudette issue create "Title" -r owner/repo` | Target specific repo |
| `claudette issue create "Title" --depends "#38"` | Declare dependency |
| `claudette issue create "Title" --ready` | Auto-apply ready label |
| `claudette issue create "Title" --no-ready` | Skip ready label |
| `claudette issue depends <ref> --on <ref>` | Declare a dependency |

## Label management

| Command | Description |
|---|---|
| `claudette claim <ref>` | Claim an issue (claudette won't touch it) |
| `claudette unclaim <ref>` | Release back to claudette |
| `claudette ready <ref>` | Mark as ready for claudette |
| `claudette unready <ref>` | Remove ready label |
| `claudette block <ref>` | Mark as blocked |
| `claudette unblock <ref>` | Remove blocked label |
| `claudette wait <ref>` | Mark as waiting on human |
| `claudette unwait <ref>` | Remove waiting label |

## Repos

| Command | Description |
|---|---|
| `claudette pause <repo>` | Pause automation on a repo |
| `claudette resume <repo>` | Resume a paused repo |
| `claudette repo add <name> [--path P] [--branch B]` | Add a repo |
| `claudette repo remove <name>` | Remove a repo |
| `claudette config set <key> <value>` | Update a config value |

## Autonomy

| Command | Description |
|---|---|
| `claudette autonomy on [--modes M]` | Enable autonomous work generation |
| `claudette autonomy off` | Disable |
| `claudette autonomy status` | Show configuration |
| `claudette autonomy run [--dry-run]` | One-shot run |

## Metrics

| Command | Description |
|---|---|
| `claudette metrics [--days N]` | Summary + daily breakdown (default: 7 days) |

## Discovery

| Command | Description |
|---|---|
| `claudette discover` | Scan repos for TODOs, coverage gaps, deps |
| `claudette discover --create` | File issues for discoveries |
| `claudette discover --dry-run` | Preview |

## Cron

| Command | Description |
|---|---|
| `claudette cron on` | Install cron job |
| `claudette cron off` | Remove cron job |
| `claudette cron status` | Check if installed |

## Memory

| Command | Description |
|---|---|
| `claudette memory sync` | Index all issues/PRs |
| `claudette memory search "query" [--state S]` | Semantic search |
| `claudette memory status` | Index stats |
| `claudette memory clear` | Wipe the index |

## Relay

| Command | Description |
|---|---|
| `claudette relay start [-f]` | Start watchdog (or foreground) |
| `claudette relay stop` | Stop watchdog |
| `claudette relay status` | Check if running |
