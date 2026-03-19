# Configuration

Config lives at `<project-dir>/.claudette/config.yaml`. Created by `claudette init`.

## Full example

```yaml
project_dir: /home/user/projects/my-saas

system:
  polling_interval_minutes: 5
  session_timeout_minutes: 45
  stale_issue_timeout_minutes: 120
  max_retries_per_issue: 1
  dry_run: false

repositories:
  - name: owner/frontend-repo
    path: /home/user/projects/my-saas/frontend-repo
    default_branch: main
    labels:
      in_progress: ["status: in-progress"]
      blocked: ["status: blocked"]
      waiting_on_user: ["status: waiting-on-user"]
      needs_review: ["status: needs-review"]
      ready_for_dev: ["status: ready-for-dev"]
      paused: ["system: paused"]
    budget:
      max_tokens_per_issue: 500000
      max_tokens_per_repo_per_day: 5000000
      pause_on_budget_exceeded: true
  - name: owner/backend-api
    default_branch: main

memory:
  backend: hybrid  # dense | bm25 | hybrid

llm:
  manager_prompt: manager.jinja2
  summarizer_prompt: summarizer.jinja2
  cmd_one_shot: "claude -p {prompt}"
  cmd_session: "claude -p --dangerously-skip-permissions {prompt}"
  cmd_subagent: "claude -p --dangerously-skip-permissions {prompt}"
  cmd_summarizer: ""  # optional: cheaper model for summarization

github:
  dependency_pattern: "Depends on\\s+(?:([\\w-]+/[\\w-]+))?#(\\d+)"
  labels:
    in_progress: ["status: in-progress"]
    blocked: ["status: blocked"]
    waiting_on_user: ["status: waiting-on-user"]
    needs_review: ["status: needs-review"]
    ready_for_dev: ["status: ready-for-dev"]
    paused: ["system: paused"]
  routing:
    owner: your-github-username
    require_ready_label: true
    ignore_labels: []

deterministic_rules:
  auto_review_new_prs: true
  default_reviewer_agent: peer-review
  auto_merge_approved_prs: true
  auto_merge_method: squash

notifications:
  webhook_url: ""
  events: [session_launched, pr_opened, issue_completed, error]

autonomy:
  enabled: false
  modes: [discover, improve, ideate]
  max_issues_per_tick: 3
  max_open_issues_per_repo: 10
  cooldown_minutes: 30
  run_on_idle: true
  auto_label: "claudette: auto"
  improve_targets: [test_coverage, error_handling, performance, documentation, dead_code, type_safety]
  ideate_targets: [developer_experience, observability, security, accessibility]

cron:
  capture_env: [GITHUB_TOKEN, GH_TOKEN, ANTHROPIC_API_KEY, HOME, PATH]

pipeline:
  enabled: true
  stages: [scout, architect, builder, tester, reviewer]
  skip_stages: []

discovery:
  enabled: false
  sources: [todos, coverage]
  min_coverage_threshold: 50.0
  file_extensions: [".py", ".js", ".ts", ".go", ".rs", ".java", ".rb"]

relay:
  enabled: false
  command_timeout: 30
  max_pending: 5
  poll_interval: 0.3
```

## Labels

Each label can be:

- **A single string:** `in_progress: "WIP"` -- one label to match and apply
- **A YAML list:** multiple synonyms; any matches when checking, first is used when applying
- **An empty list `[]`:** disables that concept entirely

Labels are defined globally (`github.labels`) and per-repo (`repositories[].labels`). Per-repo overrides global.

## Routing

- **`owner`** -- GitHub username. Only pick up issues you created. Cross-user deps still work.
- **`require_ready_label`** (default: `true`) -- issues must have the ready label before claudette works on them.
- **`ignore_labels`** -- issues with these labels are invisible to claudette.

## Budget

Per-repo, not global. When `pause_on_budget_exceeded` is true, claudette skips the repo once the daily token limit is hit.

## Cron environment capture

`cron.capture_env` lists environment variable **names** to forward to the cron job. Values are read from your current shell when you run `claudette cron on`, never stored in config. This keeps tokens out of `config.yaml`.

## Multi-project support

Global registry at `~/.claudette/projects.json`. Target a specific project:

```bash
claudette --project ~/projects/other-saas status
CLAUDETTE_PROJECT=~/projects/other-saas claudette status
```

## Per-repo overrides

Repos can include a `.claudette.yaml` at their root to override labels, budget, and default branch. Merged during `claudette init`.
