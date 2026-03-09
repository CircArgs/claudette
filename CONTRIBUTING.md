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

The codebase is designed around three protocols that separate all external I/O from business logic:

| Protocol | Production | Test fake |
|---|---|---|
| `GitHubClient` | `httpx` + PAT | `FakeGitHubClient` — in-memory issues, labels, comments |
| `LLMClient` | `claude` CLI (summarizer via `--print`, manager via session) | `FakeLLMClient` — canned responses, records session launches |
| `Clock` | `datetime.now()` | `FakeClock` — controllable time |

This means the full `poll.py` pipeline can run in tests without network, subprocesses, or LLM calls.

### Test layers

1. **Unit tests** (`tests/unit/`) — Test individual components (DAG builder, config, budget tracker) in isolation. No fakes needed, just direct function calls.

2. **Integration tests** (`tests/integration/`) — Run the complete tick pipeline with fake dependencies. Seed a `FakeGitHubClient` with issues, run a tick, assert on state changes and that the right prompt was assembled for the manager session.

3. **Sandbox tests** (`tests/fixtures/`) — YAML scenario files for live testing against throwaway GitHub repos. Run with `claudette sandbox --fixture <file>`.

## Architecture overview

Each tick:
1. Fetches new issues/PRs from GitHub
2. Builds a cross-repo dependency graph (deterministic, no LLM)
3. Summarizes long threads via `claude --print` one-shot calls (cached)
4. Assembles a comprehensive manager prompt with all ready issues and PRs
5. Launches a `claude` session that dispatches sub-agents via git worktrees

The manager session handles claiming issues, writing code, opening PRs, and reviewing PRs autonomously.

## Code style

```bash
# Lint and autofix
uv run ruff check src/ tests/ --fix

# Format
uv run ruff format src/ tests/

# Type check
uv run mypy src/
```

- Keep it simple. No abstractions unless something is used in 3+ places.

## Project structure

```
src/claudette/
|-- __init__.py
|-- cli/                # Click commands
|   |-- __init__.py
|   |-- app.py          # Entry point, Click group
|   |-- init.py         # Interactive init flow
|   +-- commands.py     # CLI command implementations
|-- core/               # Business logic
|   |-- __init__.py
|   |-- config.py       # Pydantic config models
|   |-- dag.py          # Dependency graph builder + cycle detection
|   |-- identity.py     # HTML signature parser
|   |-- poll.py         # The main tick pipeline
|   |-- budget.py       # Token usage tracking
|   |-- llm_client.py   # Claude CLI client (summarizer + session launcher)
|   |-- bootstrap.py    # Project setup, repo cloning, label creation
|   |-- memory.py       # Semantic index (model2vec + sqlite + numpy)
|   |-- relay.py        # File-based IPC for sandboxed environments
|   +-- skills.py       # Claude Code skill definitions
|-- protocols/          # Dependency injection interfaces
|   |-- __init__.py
|   |-- github.py       # GitHubClient protocol
|   |-- llm.py          # LLMClient protocol
|   +-- clock.py        # Clock protocol
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
|   +-- test_relay.py
|-- integration/
|   |-- conftest.py     # Shared fakes
|   +-- test_tick.py
+-- fixtures/
    |-- cross_repo_handoff.yaml
    +-- basic_issue_lifecycle.yaml
```

## Commits

- Keep commits focused — one logical change per commit.
- Write commit messages that explain *why*, not *what*.

## Pull requests

- Open a draft PR early if you want feedback.
- PRs need passing tests (`uv run pytest`) and clean lint (`uv run ruff check src/ tests/`).
- One approval required to merge.
