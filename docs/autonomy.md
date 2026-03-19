# Autonomous work generation

When enabled, claudette discovers and creates its own GitHub issues without waiting for humans.

## Modes

- **discover** -- Scans for TODO/FIXME comments and coverage gaps, files consolidated issues per file. Reuses the `claudette discover` engine.
- **improve** -- Creates improvement tasks targeting: test coverage, error handling, performance, documentation, dead code, type safety.
- **ideate** -- Creates feature exploration tasks. The agent analyzes the codebase, proposes 3-5 improvements, implements the best one, and files issues for the rest.

## CLI

```bash
claudette autonomy on                     # enable all modes
claudette autonomy on --modes discover    # enable specific modes
claudette autonomy off                    # disable
claudette autonomy status                 # show config
claudette autonomy run                    # one-shot run now
claudette autonomy run --dry-run          # preview what would be created
```

## How it runs

During each tick:
- If **no human issues exist** and `run_on_idle: true` (default), autonomy runs and creates issues that get picked up in the same tick.
- If **human issues exist** and `discover` mode is enabled, discovery still runs in the background to queue work for future ticks.

## Safeguards

| Setting | Default | Description |
|---|---|---|
| `cooldown_minutes` | 30 | Per-repo cooldown between autonomous runs |
| `max_issues_per_tick` | 3 | Max issues created per tick |
| `max_open_issues_per_repo` | 10 | Prevents backlog flooding |
| `auto_label` | `claudette: auto` | Applied to all auto-created issues |
| `run_on_idle` | true | Only run when no human issues exist |

Titles are fuzzy-matched against existing open issues to avoid duplicates. State is tracked in `autonomy_state.json`.

## Config

```yaml
autonomy:
  enabled: false
  modes: [discover, improve, ideate]
  max_issues_per_tick: 3
  max_open_issues_per_repo: 10
  cooldown_minutes: 30
  run_on_idle: true
  auto_label: "claudette: auto"
  improve_targets:
    - test_coverage
    - error_handling
    - performance
    - documentation
    - dead_code
    - type_safety
  ideate_targets:
    - developer_experience
    - observability
    - security
    - accessibility
```
