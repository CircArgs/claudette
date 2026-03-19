"""Integration tests for the full tick pipeline."""

from pathlib import Path

from claudette.core.config import Config, RepoConfig
from claudette.core.poll import run_tick
from claudette.protocols.github import Comment, Review
from tests.integration.conftest import (
    FakeGitHubClient,
    FakeLLMClient,
)


def _config(tmp_path: Path, *repos: str) -> Config:
    # Create prompts dir with a minimal template so rendering works
    prompts_dir = tmp_path / ".claudette" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        project_dir=tmp_path,
        repositories=[RepoConfig(name=r) for r in repos],
    )


class TestBasicTick:
    def test_empty_repos(self, tmp_path: Path):
        gh = FakeGitHubClient()
        result = run_tick(
            github=gh,
            llm=FakeLLMClient(),
            config=_config(tmp_path),
            state_dir=tmp_path,
        )
        assert result.errors == []

    def test_ready_issue_launches_session(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, title="Fix bug", labels=["claudette: ready-for-dev"])

        llm = FakeLLMClient()

        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert result.session_launched
        assert result.session_pid == 99999
        assert "owner/repo#1" in result.issues_in_prompt
        assert len(llm.sessions_launched) == 1

    def test_no_issues_no_session(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, title="Waiting", labels=["claudette: waiting-on-user"])

        llm = FakeLLMClient()
        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert llm.calls == []  # No LLM calls at all
        assert not result.session_launched

    def test_session_prompt_includes_issue_details(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, title="Fix bug", labels=["claudette: ready-for-dev"])
        gh.add_issue("owner/repo", 2, title="Add feature", labels=["claudette: ready-for-dev"])

        llm = FakeLLMClient()

        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert result.session_launched
        assert len(llm.sessions_launched) == 1
        prompt = llm.sessions_launched[0][0]
        assert "Fix bug" in prompt
        assert "Add feature" in prompt
        assert "owner/repo#1" in prompt
        assert "owner/repo#2" in prompt


class TestDependencyBlocking:
    def test_blocked_issue_gets_label(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, state="open")
        gh.add_issue("owner/repo", 2, body="Depends on #1", labels=["claudette: ready-for-dev"])

        run_tick(
            github=gh,
            llm=FakeLLMClient(),
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert "claudette: blocked" in gh.get_labels("owner/repo", 2)

    def test_cross_repo_blocking(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/backend", 8, state="open")
        gh.add_issue(
            "owner/frontend",
            20,
            body="Depends on owner/backend#8",
            labels=["claudette: ready-for-dev"],
        )

        run_tick(
            github=gh,
            llm=FakeLLMClient(),
            config=_config(tmp_path, "owner/backend", "owner/frontend"),
            state_dir=tmp_path,
        )

        assert "claudette: blocked" in gh.get_labels("owner/frontend", 20)

    def test_unblocked_when_dep_closed(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, state="closed")
        gh.add_issue("owner/repo", 2, body="Depends on #1", labels=["claudette: ready-for-dev"])

        llm = FakeLLMClient()

        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert "claudette: blocked" not in gh.get_labels("owner/repo", 2)
        assert result.session_launched
        assert "owner/repo#2" in result.issues_in_prompt


class TestCycleDetection:
    def test_cycle_members_blocked_and_commented(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, body="Depends on #2", labels=["claudette: ready-for-dev"])
        gh.add_issue("owner/repo", 2, body="Depends on #1", labels=["claudette: ready-for-dev"])

        result = run_tick(
            github=gh,
            llm=FakeLLMClient(),
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert len(result.cycle_members) >= 2
        # Both should be labeled blocked
        assert "claudette: blocked" in gh.get_labels("owner/repo", 1)
        assert "claudette: blocked" in gh.get_labels("owner/repo", 2)
        # Comments should mention circular dependency
        comments_1 = [c.body for c in gh.get_issue("owner/repo", 1).comments]
        assert any("Circular dependency" in c for c in comments_1)


class TestDeterministicPRRouting:
    def test_new_pr_flagged_for_review(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue(
            "owner/repo",
            5,
            title="Add feature",
            labels=["claudette: ready-for-dev"],
            is_pull_request=True,
        )

        llm = FakeLLMClient()
        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert "owner/repo#5" in result.dispatched_reviews
        assert "claudette: needs-review" in gh.get_labels("owner/repo", 5)

    def test_pr_review_context_in_prompt(self, tmp_path: Path):
        """PR reviews and inline comments should appear in the manager prompt."""
        gh = FakeGitHubClient()
        gh.add_issue(
            "owner/repo",
            10,
            title="Refactor auth",
            labels=["claudette: ready-for-dev"],
            is_pull_request=True,
            comments=[
                Comment(body="LGTM overall but see inline comments", author="reviewer1"),
            ],
            reviews=[
                Review(
                    author="reviewer1",
                    state="CHANGES_REQUESTED",
                    body="Needs error handling",
                    comments=[
                        Comment(
                            body="This could panic on None",
                            author="reviewer1",
                            path="src/auth.py",
                        ),
                    ],
                ),
            ],
        )

        llm = FakeLLMClient()
        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
        )

        assert "owner/repo#10" in result.dispatched_reviews
        assert result.session_launched
        prompt = llm.sessions_launched[0][0]
        assert "Refactor auth" in prompt
        assert "CHANGES_REQUESTED" in prompt
        assert "Needs error handling" in prompt
        assert "src/auth.py" in prompt
        assert "This could panic on None" in prompt


class TestDryRun:
    def test_dry_run_no_mutations(self, tmp_path: Path):
        gh = FakeGitHubClient()
        gh.add_issue("owner/repo", 1, title="Fix bug", labels=["claudette: ready-for-dev"])

        llm = FakeLLMClient()

        result = run_tick(
            github=gh,
            llm=llm,
            config=_config(tmp_path, "owner/repo"),
            state_dir=tmp_path,
            dry_run=True,
        )

        assert len(result.dry_run_actions) > 0
        assert not result.session_launched
        assert llm.sessions_launched == []
        # Only fetch_issues should have been called, no mutations
        mutation_calls = [c for c in gh.api_calls if c[0] != "fetch_issues"]
        assert mutation_calls == []
