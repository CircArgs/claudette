# Metrics and notifications

## Metrics

Claudette tracks operational metrics in `metrics.json`:

```bash
claudette metrics              # summary + 7-day breakdown
claudette metrics --days 30    # last 30 days
```

Tracked events: `tick`, `session_launched`, `pr_opened`, `pr_merged`, `pr_approved`, `pr_rejected`, `issue_completed`, `issue_escalated`, `stale_requeued`, `error`, `auto_issue_created`.

Summary includes: total ticks, sessions, PRs opened/merged, approval rate, issues completed/escalated, errors, uptime, and per-repo breakdowns.

## Rich TUI dashboard

`claudette watch` shows a live dashboard with:

- Session status (PID, uptime, alive/dead, last output)
- Active worktrees
- Repos with pause status
- Live metrics (ticks, sessions, PRs, merge rate, errors)
- Scrolling session log
- System config

```bash
claudette watch              # Rich TUI (default)
claudette watch -n 5         # refresh every 5s
claudette watch --simple     # plain text fallback
```

The dashboard reads real state only and performs zombie-aware process detection via `/proc` on Linux.

## Webhooks

Send notifications to Slack, Discord, or any generic webhook. Payload format is auto-detected from the URL.

```yaml
notifications:
  webhook_url: "https://hooks.slack.com/services/T.../B.../..."
  events: [session_launched, pr_opened, issue_completed, error]
```

Set `webhook_url` to empty string to disable.
