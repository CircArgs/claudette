"""Shared test fakes for integration tests."""

from __future__ import annotations

import subprocess
from datetime import datetime
from unittest.mock import MagicMock

from claudette.protocols.github import Comment, Issue, Review
from claudette.protocols.llm import LLMResponse


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 3, 5, 12, 0, 0)
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now += timedelta(seconds=seconds)


class FakeGitHubClient:
    def __init__(self) -> None:
        self.issues: dict[str, dict[int, Issue]] = {}
        self.api_calls: list[tuple[str, ...]] = []

    def add_issue(
        self,
        repo: str,
        number: int,
        title: str = "",
        body: str = "",
        state: str = "open",
        labels: list[str] | None = None,
        comments: list[Comment] | None = None,
        reviews: list[Review] | None = None,
        is_pull_request: bool = False,
    ) -> None:
        self.issues.setdefault(repo, {})[number] = Issue(
            repo=repo,
            number=number,
            title=title or f"Issue {number}",
            body=body,
            state=state,
            labels=labels or [],
            comments=comments or [],
            reviews=reviews or [],
            is_pull_request=is_pull_request,
        )

    def fetch_issues(self, repo: str, since: datetime) -> list[Issue]:
        self.api_calls.append(("fetch_issues", repo))
        return list(self.issues.get(repo, {}).values())

    def get_issue(self, repo: str, number: int) -> Issue:
        return self.issues[repo][number]

    def post_comment(self, repo: str, number: int, body: str) -> None:
        self.api_calls.append(("post_comment", repo, str(number), body))
        issue = self.issues[repo][number]
        issue.comments.append(Comment(body=body, author="system"))

    def apply_label(self, repo: str, number: int, label: str) -> None:
        self.api_calls.append(("apply_label", repo, str(number), label))
        issue = self.issues[repo][number]
        if label not in issue.labels:
            issue.labels.append(label)

    def remove_label(self, repo: str, number: int, label: str) -> None:
        self.api_calls.append(("remove_label", repo, str(number), label))
        issue = self.issues[repo][number]
        if label in issue.labels:
            issue.labels.remove(label)

    def get_labels(self, repo: str, number: int) -> list[str]:
        return self.issues[repo][number].labels

    def ensure_label_exists(self, repo: str, label: str) -> None:
        self.api_calls.append(("ensure_label_exists", repo, label))

    def has_label(self, repo: str, label: str) -> bool:
        return any(label in issue.labels for issue in self.issues.get(repo, {}).values())


class FakeLLMClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_index = 0
        self.calls: list[tuple[str, str]] = []
        self.sessions_launched: list[tuple[str, str]] = []

    def summarize(self, thread: str) -> LLMResponse:
        self.calls.append(("summarize", thread))
        return LLMResponse(text="Summary of thread.", input_tokens=100, output_tokens=50)

    def launch_manager_session(
        self,
        prompt: str,
        cwd: str,
        log_path: str | None = None,
    ) -> subprocess.Popen:
        self.calls.append(("launch_manager_session", prompt))
        self.sessions_launched.append((prompt, cwd))
        # Return a mock Popen with a fake PID
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 99999
        return mock_proc
