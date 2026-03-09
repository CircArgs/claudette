"""GitHub API protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class Issue:
    repo: str
    number: int
    title: str
    body: str
    state: str
    labels: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    is_pull_request: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Comment:
    body: str
    author: str
    created_at: datetime | None = None
    path: str | None = None  # file path for PR inline review comments


@dataclass
class Review:
    """A PR review (approval, changes requested, or comment)."""

    author: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
    body: str
    comments: list[Comment] = field(default_factory=list)  # inline review comments
    submitted_at: datetime | None = None


@runtime_checkable
class GitHubClient(Protocol):
    def fetch_issues(self, repo: str, since: datetime) -> list[Issue]:
        """Fetch issues updated since the given timestamp."""
        ...

    def get_issue(self, repo: str, number: int) -> Issue:
        """Fetch a single issue/PR with comments. For PRs, also fetches reviews."""
        ...

    def post_comment(self, repo: str, number: int, body: str) -> None:
        """Post a comment on an issue or PR."""
        ...

    def apply_label(self, repo: str, number: int, label: str) -> None:
        """Add a label to an issue or PR."""
        ...

    def remove_label(self, repo: str, number: int, label: str) -> None:
        """Remove a label from an issue or PR."""
        ...

    def get_labels(self, repo: str, number: int) -> list[str]:
        """Get all labels on an issue or PR."""
        ...

    def ensure_label_exists(self, repo: str, label: str) -> None:
        """Create the label on the repo if it doesn't already exist."""
        ...

    def has_label(self, repo: str, label: str) -> bool:
        """Check if any open issue in the repo has this label."""
        ...

    def create_issue(
        self, repo: str, title: str, body: str = "", labels: list[str] | None = None
    ) -> Issue:
        """Create a new issue. Returns the created Issue."""
        ...

    def update_issue_body(self, repo: str, number: int, body: str) -> None:
        """Replace the body of an issue."""
        ...
