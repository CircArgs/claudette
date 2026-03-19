# Contributing to claudette

## Development setup

```bash
git clone https://github.com/CircArgs/claudette.git
cd claudette

# With uv (recommended)
uv sync --dev

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
# All unit + integration tests
uv run pytest

# Just unit tests (fast, no I/O)
uv run pytest -m unit

# Just integration tests (full pipeline with fakes)
uv run pytest -m integration

# With coverage
uv run coverage run -m pytest && uv run coverage report
```

## Testing architecture

The codebase is designed around protocols that separate all external I/O from business logic:

| Protocol | Production | Test fake |
|---|---|---|
| `GitHubClient` | `HttpxGitHubClient` (httpx + PAT) | `FakeGitHubClient` -- in-memory issues, labels, comments |
| `GitHubClient` | `GhCliGitHubClient` (`gh api` subprocess) | Same `FakeGitHubClient` |
| `LLMClient` | `claude` CLI (summarizer via `--print`, manager via session) | `FakeLLMClient` -- canned responses, records session launches |
| `Clock` | `SystemClock` (`datetime.now()`) | `FakeClock` -- controllable time |

Two `GitHubClient` implementations exist: `HttpxGitHubClient` talks directly to the GitHub API with httpx, while `GhCliGitHubClient` shells out to the `gh` CLI -- useful in corporate environments where `gh` handles TLS but httpx cannot.

This means the full `poll.py` pipeline can run in tests without network, subprocesses, or LLM calls.

### Test layers

1. **Unit tests** (`tests/unit/`) -- Test individual components (DAG builder, config, budget tracker, metrics store, discovery scanners, pipeline phases) in isolation. No fakes needed, just direct function calls.

2. **Integration tests** (`tests/integration/`) -- Run the complete tick pipeline with fake dependencies. Seed a `FakeGitHubClient` with issues, run a tick, assert on state changes and that the right prompt was assembled for the manager session.

3. **Sandbox tests** (`tests/fixtures/`) -- YAML scenario files for live testing against throwaway GitHub repos. Run with `claudette sandbox --fixture <file>`.

## Architecture overview

Each tick:
1. Fetches new issues/PRs from GitHub
2. Builds a cross-repo dependency graph (deterministic, no LLM)
3. Detects stale in-progress issues and re-queues them (with escalation on repeated stalls)
4. Summarizes long threads via `claude --print` one-shot calls (cached)
5. Assembles a comprehensive manager prompt with all ready issues and PRs
6. Launches a `claude` session that dispatches sub-agents via git worktrees
7. Auto-merges approved PRs when CI is green (if enabled in config)
8. Runs autonomous work discovery when enabled -- scans repos for TODOs, coverage gaps, and dependency issues, then files new issues automatically

Between ticks the system also:
- Tracks metrics (sessions launched, PRs opened/merged, escalations) via `MetricsStore`
- Sends webhook notifications (Slack, Discord, or generic) for configured events
- Detects zombie processes from previous sessions

## Code style

```bash
# Lint and autofix
uv run ruff check claudette/ tests/ --fix

# Format
uv run ruff format claudette/ tests/

# Type check
uv run mypy claudette/
```

- Keep it simple. No abstractions unless something is used in 3+ places.

## Project structure

```
claudette/
|-- __init__.py
|-- cli/                    # Click commands
|   |-- __init__.py
|   |-- app.py              # Entry point, Click group
|   |-- init.py             # Interactive init flow
|   |-- commands.py         # CLI command implementations
|   +-- dashboard.py        # Rich TUI dashboard (`claudette watch`)
|-- core/                   # Business logic
|   |-- __init__.py
|   |-- config.py           # Pydantic config models
|   |-- dag.py              # Dependency graph builder + cycle detection
|   |-- identity.py         # HTML signature parser
|   |-- poll.py             # The main tick pipeline
|   |-- budget.py           # Token usage tracking
|   |-- llm_client.py       # Claude CLI client (summarizer + session launcher)
|   |-- bootstrap.py        # Project setup, repo cloning, label creation
|   |-- memory.py           # Semantic index (model2vec + sqlite + numpy)
|   |-- relay.py            # File-based IPC for sandboxed environments
|   |-- skills.py           # Claude Code skill definitions
|   |-- autonomy.py         # Autonomous work generation (discover, improve, ideate)
|   |-- metrics.py          # MetricsStore -- event/counter persistence
|   |-- notifications.py    # Outgoing webhook notifications (Slack/Discord/generic)
|   |-- discovery.py        # TODO/coverage/dependency scanning for autonomy
|   |-- github_client.py    # GitHubClient via httpx + PAT
|   |-- gh_cli_client.py    # GitHubClient via `gh` CLI (bypasses Python TLS)
|   +-- clock.py            # SystemClock (production Clock implementation)
|-- protocols/              # Dependency injection interfaces
|   |-- __init__.py
|   |-- github.py           # GitHubClient protocol
|   |-- llm.py              # LLMClient protocol
|   +-- clock.py            # Clock protocol
+-- prompts/
    |-- manager.jinja2
    +-- summarizer.jinja2

tests/
|-- unit/
|   |-- test_dag.py
|   |-- test_identity.py
|   |-- test_config.py
|   |-- test_budget.py
|   |-- test_llm_client.py
|   |-- test_relay.py
|   |-- test_github_client.py
|   |-- test_metrics.py
|   |-- test_discovery.py
|   +-- test_pipeline.py
|-- integration/
|   |-- conftest.py         # Shared fakes
|   +-- test_tick.py
+-- fixtures/
    |-- cross_repo_handoff.yaml
    +-- basic_issue_lifecycle.yaml
```

## Commits

- Keep commits focused -- one logical change per commit.
- Write commit messages that explain *why*, not *what*.

## Pull requests

- Open a draft PR early if you want feedback.
- PRs need passing tests (`uv run pytest`) and clean lint (`uv run ruff check src/ tests/`).
- One approval required to merge.
