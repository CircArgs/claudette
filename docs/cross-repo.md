# Cross-repo coordination

Claudette builds a unified dependency graph across all configured repos.

## Declaring dependencies

Use GitHub's cross-repo reference syntax in issue bodies:

```markdown
Depends on owner/backend-api#8
```

Same-repo shorthand works too:

```markdown
Depends on #3
```

## How it works

1. Every tick, all open issues are fetched and parsed for dependency patterns
2. A DAG is constructed across all repos
3. Issues with unresolved deps are labeled `blocked`
4. When a blocking issue closes, the dependent issue becomes `ready` on the next tick
5. Circular dependencies are detected -- all cycle members get labeled `blocked` with a comment

The manager session sees all repos in a single prompt and can dispatch sub-agents to work on multiple repos in parallel via worktrees.

## Dependency pattern

The regex is configurable:

```yaml
github:
  dependency_pattern: "Depends on\\s+(?:([\\w-]+/[\\w-]+))?#(\\d+)"
```
