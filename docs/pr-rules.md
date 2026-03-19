# Auto-merge and PR rules

## Auto-merge

On by default. When a PR has an `APPROVED` review and all CI checks pass, claudette merges it during the tick pipeline.

```yaml
deterministic_rules:
  auto_merge_approved_prs: true   # set false to disable
  auto_merge_method: squash       # merge, squash, or rebase
  auto_review_new_prs: true       # auto-flag new PRs for review
```

## Auto-review

When `auto_review_new_prs` is true, new PRs are automatically labeled with the `needs_review` label and dispatched to a reviewer agent.

## PR revision flow

When a reviewer requests changes:
1. Next tick detects `CHANGES_REQUESTED` reviews
2. A worker is dispatched with the review feedback
3. Worker pushes updated commits to the PR branch

## Stale issue detection

Issues stuck in-progress longer than `stale_issue_timeout_minutes` (default: 120) are re-queued.

## Retry and escalation

1. **Retry** -- Issue is retried up to `max_retries_per_issue` times (default: 1). Counter stored in `retries.json`.
2. **Escalation** -- After exhausting retries, claudette removes in-progress, applies `waiting-on-user`, and posts a comment. Issue is now flagged for human attention.

```yaml
system:
  stale_issue_timeout_minutes: 120
  max_retries_per_issue: 1
```
