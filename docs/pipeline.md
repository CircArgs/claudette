# Agent pipeline

When `pipeline.enabled` is true (the default), the manager dispatches each issue through a multi-stage pipeline of specialized sub-agents.

## Stages

1. **Scout** (read-only) -- Analyzes the codebase: affected files, dependencies, risks, complexity. Must not modify files.
2. **Architect** -- Produces a spec from the scout's analysis: approach, files to modify, test strategy, edge cases. Must not write code.
3. **Builder** -- Implements the spec: writes code and tests, commits with issue references.
4. **Tester** -- Runs the full test suite, checks coverage, reports results.
5. **Reviewer** -- Reviews the diff against spec and requirements. Approves or requests changes (loops back to Builder).

## Config

```yaml
pipeline:
  enabled: true
  stages: [scout, architect, builder, tester, reviewer]
  skip_stages: []  # e.g. ["scout", "architect"] to go straight to builder
```

Set `pipeline.enabled: false` to use simple single-worker dispatch instead.

## Work discovery

Separate from the pipeline, `claudette discover` scans repos for potential work:

```bash
claudette discover               # scan all repos
claudette discover --create      # file issues for discoveries
claudette discover --dry-run     # preview
```

Sources: TODO/FIXME comments, coverage gaps (Cobertura XML), dependency listings.
